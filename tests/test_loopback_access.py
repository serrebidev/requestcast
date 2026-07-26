"""Regression tests for local Windows web interface access."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from requestcast import server


class DummyApp:
    def __init__(self) -> None:
        self.config = {"SESSION_COOKIE_SECURE": True}


class LoopbackAccessTests(unittest.TestCase):
    def test_localhost_and_ipv4_urls_are_always_advertised(self) -> None:
        self.assertEqual(
            server.browser_urls("localhost", 8797),
            ["http://localhost:8797/", "http://127.0.0.1:8797/"],
        )
        self.assertEqual(
            server.browser_urls("127.0.0.1", 8797),
            ["http://localhost:8797/", "http://127.0.0.1:8797/"],
        )

    def test_automatic_launch_prefers_ipv4_loopback(self) -> None:
        self.assertEqual(
            server.preferred_browser_url("localhost", 8797),
            "http://127.0.0.1:8797/",
        )
        self.assertEqual(
            server.preferred_browser_url("127.0.0.1", 8797),
            "http://127.0.0.1:8797/",
        )

    @patch("requestcast.server.ipv6_loopback_available", return_value=True)
    def test_loopback_bind_uses_ipv4_and_ipv6(self, _mock_ipv6) -> None:
        self.assertEqual(
            server.waitress_bind_options("localhost", 8797),
            {"listen": "127.0.0.1:8797 [::1]:8797"},
        )

    @patch("requestcast.server.ipv6_loopback_available", return_value=False)
    def test_loopback_bind_falls_back_to_ipv4(self, _mock_ipv6) -> None:
        self.assertEqual(
            server.waitress_bind_options("localhost", 8797),
            {"listen": "127.0.0.1:8797"},
        )

    def test_legacy_secure_cookie_is_disabled_for_loopback_http(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "127.0.0.1")
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_network_bind_keeps_secure_cookie_setting(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "0.0.0.0")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    @patch("requestcast.server.socket.create_connection")
    def test_wait_for_server_closes_successful_connection(self, create_connection) -> None:
        connection = MagicMock()
        create_connection.return_value = connection

        self.assertTrue(server.wait_for_server("http://127.0.0.1:8797/", timeout=1.0))
        create_connection.assert_called_once_with(("127.0.0.1", 8797), timeout=1.0)
        connection.close.assert_called_once_with()

    def test_windows_browser_launch_uses_startfile_first(self) -> None:
        with (
            patch.object(server.os, "name", "nt"),
            patch.object(server.os, "startfile", create=True) as startfile,
            patch("requestcast.server.webbrowser.open") as browser_open,
        ):
            self.assertTrue(server.open_default_browser("http://127.0.0.1:8797/"))
            startfile.assert_called_once_with("http://127.0.0.1:8797/")
            browser_open.assert_not_called()

    def test_windows_browser_launch_falls_back_to_rundll32(self) -> None:
        with (
            patch.object(server.os, "name", "nt"),
            patch.object(server.os, "startfile", side_effect=OSError, create=True),
            patch("requestcast.server.subprocess.Popen") as popen,
        ):
            self.assertTrue(server.open_default_browser("http://127.0.0.1:8797/"))
            popen.assert_called_once_with(
                [
                    "rundll32.exe",
                    "url.dll,FileProtocolHandler",
                    "http://127.0.0.1:8797/",
                ],
                stdout=server.subprocess.DEVNULL,
                stderr=server.subprocess.DEVNULL,
            )


if __name__ == "__main__":
    unittest.main()
