"""Regression tests for the full RequestCast diagnostic collector."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from requestcast import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_sensitive_settings_are_redacted(self) -> None:
        result = diagnostics.sanitized_settings(
            {
                "bind_host": "127.0.0.1",
                "azuracast_api_key": "private-key",
                "secret_key": "session-secret",
                "deezer_arl": "private-arl",
                "password_hash": "private-hash",
            }
        )
        self.assertEqual(result["bind_host"], "127.0.0.1")
        self.assertEqual(result["azuracast_api_key"], "[REDACTED]")
        self.assertEqual(result["secret_key"], "[REDACTED]")
        self.assertEqual(result["deezer_arl"], "[REDACTED]")
        self.assertEqual(result["password_hash"], "[REDACTED]")

    def test_command_output_redacts_credentials(self) -> None:
        with patch("requestcast.diagnostics._known_secret_values", return_value=["real-secret"]):
            text = diagnostics._redact_text(
                "https://name:pass@example.test password=hunter2 token:abc real-secret"
            )
        self.assertNotIn("name:pass", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abc", text)
        self.assertNotIn("real-secret", text)
        self.assertIn("[REDACTED]", text)

    def test_http_body_is_reduced_to_nonsecret_metadata(self) -> None:
        metadata = diagnostics._body_metadata(
            b"<!doctype html><html><head><title>RequestCast Setup</title></head>"
            b"<body><input value='private-api-key'></body></html>"
        )
        self.assertTrue(metadata["contains_requestcast"])
        self.assertTrue(metadata["contains_html"])
        self.assertEqual(metadata["title"], "RequestCast Setup")
        self.assertNotIn("private-api-key", repr(metadata))

    def test_wsgi_middleware_records_request_and_response(self) -> None:
        def application(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"okay"]

        captured: list[str] = []
        middleware = diagnostics.RequestLoggingMiddleware(application)
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_HOST": "127.0.0.1:8797",
            "HTTP_USER_AGENT": "test-browser",
        }
        statuses: list[str] = []

        def start_response(status, headers, exc_info=None):
            statuses.append(status)

        with patch("requestcast.diagnostics.log_http", side_effect=captured.append):
            body = b"".join(middleware(environ, start_response))

        self.assertEqual(body, b"okay")
        self.assertEqual(statuses, ["200 OK"])
        self.assertTrue(any("REQUEST start" in line for line in captured))
        self.assertTrue(any("RESPONSE finish" in line and "bytes=4" in line for line in captured))

    def test_collector_creates_one_sendable_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            with (
                patch("requestcast.diagnostics.diagnostics_root", return_value=root),
                patch("requestcast.diagnostics._collect_commands"),
                patch("requestcast.diagnostics._collect_http_timeline"),
                patch("requestcast.diagnostics._copy_text_if_exists"),
                patch("requestcast.diagnostics.server.log_startup"),
                patch("requestcast.diagnostics.config.load", return_value={"bind_host": "127.0.0.1", "bind_port": 8797}),
            ):
                archive = diagnostics.collect_bundle(
                    ["http://127.0.0.1:8797/"], duration=0.0, show_dialog=False
                )
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as zipped:
                names = set(zipped.namelist())
            self.assertIn("README-SEND-THIS-ZIP.txt", names)
            self.assertIn("settings-redacted.json", names)
            self.assertIn("environment-redacted.json", names)


if __name__ == "__main__":
    unittest.main()
