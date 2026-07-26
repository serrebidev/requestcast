"""Regression tests for deterministic local HTTP response framing."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from requestcast.http_framing import BufferedClosingMiddleware, RequestCastHTTP10RequestHandler


class HttpFramingTests(unittest.TestCase):
    def test_windows_request_handler_forces_http10(self) -> None:
        self.assertEqual(RequestCastHTTP10RequestHandler.protocol_version, "HTTP/1.0")

    def test_response_has_exact_length_and_closed_connection(self) -> None:
        captured: dict[str, object] = {}

        def application(_environ, start_response):
            write = start_response(
                "200 OK",
                [
                    ("Content-Type", "text/plain"),
                    ("Content-Length", "999"),
                    ("Transfer-Encoding", "chunked"),
                    ("Connection", "keep-alive"),
                ],
            )
            write(b"a")
            return [b"bc"]

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = list(headers)
            captured["exc_info"] = exc_info
            return lambda _data: None

        body = b"".join(
            BufferedClosingMiddleware(application)(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/healthz",
                    "REMOTE_ADDR": "127.0.0.1",
                    "HTTP_HOST": "127.0.0.1:8797",
                    "HTTP_USER_AGENT": "test",
                },
                start_response,
            )
        )
        headers = {str(name).casefold(): str(value) for name, value in captured["headers"]}
        self.assertEqual(body, b"abc")
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(headers["content-length"], "3")
        self.assertEqual(headers["connection"], "close")
        self.assertNotIn("transfer-encoding", headers)

    def test_head_preserves_length_without_sending_body(self) -> None:
        captured: dict[str, object] = {}

        def application(_environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "3")])
            return [b"abc"]

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = list(headers)
            return lambda _data: None

        body = b"".join(
            BufferedClosingMiddleware(application)(
                {"REQUEST_METHOD": "HEAD", "PATH_INFO": "/healthz"},
                start_response,
            )
        )
        headers = {str(name).casefold(): str(value) for name, value in captured["headers"]}
        self.assertEqual(body, b"")
        self.assertEqual(headers["content-length"], "3")
        self.assertEqual(headers["connection"], "close")


if __name__ == "__main__":
    unittest.main()
