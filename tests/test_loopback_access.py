"""Regression tests for local Windows web interface access."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from requestcast import browser_shell, server


class DummyApp:
    def __init__(self) -> None:
        self.config = {"SESSION_COOKIE_SECURE": True}


class LoopbackAccessTests(unittest.TestCase):
    def test_local_and_wildcard_binds_advertise_both_browser_addresses(self) -> None:
        expected = ["http://localhost:8797/", "http://127.0.0.1:8797/"]
        for host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::", "*"):
            with self.subTest(host=host):
                self.assertEqual(server.browser_urls(host, 8797), expected)
                self.assertEqual(
                    server.preferred_browser_url(host, 8797),
                    "http://127.0.0.1:8797/",
                )

    @patch("requestcast.server.ipv6_loopback_available", return_value=True)
    def test_loopback_bind_has_ipv4_fallback(self, _mock_ipv6) -> None:
        self.assertEqual(
            server.waitress_bind_candidates("localhost", 8797),
            [
                {"listen": "127.0.0.1:8797 [::1]:8797"},
                {"host": "127.0.0.1", "port": 8797},
            ],
        )

    @patch("requestcast.server.ipv6_wildcard_available", return_value=True)
    def test_ipv6_wildcard_also_listens_on_ipv4(self, _mock_ipv6) -> None:
        self.assertEqual(
            server.waitress_bind_candidates("::", 8797),
            [
                {"listen": "0.0.0.0:8797 [::]:8797"},
                {"host": "0.0.0.0", "port": 8797},
            ],
        )

    def test_legacy_secure_cookie_is_disabled_for_loopback_http(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "127.0.0.1")
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_network_bind_keeps_secure_cookie_setting(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "0.0.0.0")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    @patch("requestcast.server.http.client.HTTPConnection")
    def test_health_check_requires_requestcast_response(self, connection_class) -> None:
        connection = connection_class.return_value
        response = MagicMock(status=200)
        response.read.return_value = b'{"status":"ok"}'
        connection.getresponse.return_value = response

        self.assertTrue(server.requestcast_is_reachable("http://127.0.0.1:8797/"))
        connection.request.assert_called_once_with(
            "GET",
            "/healthz",
            headers={"Host": "127.0.0.1", "User-Agent": "RequestCast-startup"},
        )
        connection.close.assert_called_once_with()

    @patch("requestcast.server.requestcast_is_reachable", side_effect=[False, True])
    @patch("requestcast.server.time.sleep")
    def test_wait_for_server_uses_http_health_check(self, sleep, reachable) -> None:
        self.assertTrue(server.wait_for_server("http://127.0.0.1:8797/", timeout=2.0))
        self.assertEqual(reachable.call_count, 2)
        sleep.assert_called_once()

    def test_windows_browser_launch_uses_url_association_first(self) -> None:
        url = "http://127.0.0.1:8797/"
        with (
            patch.object(browser_shell.os, "name", "nt"),
            patch("requestcast.browser_shell._startfile", return_value=True) as startfile,
            patch("requestcast.browser_shell._shell_execute") as shell_execute,
            patch("requestcast.browser_shell._rundll32_url") as rundll32,
            patch("requestcast.browser_shell._explorer_open") as explorer,
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            self.assertTrue(browser_shell.open_default_browser(url))
            startfile.assert_called_once_with(url)
            shell_execute.assert_not_called()
            rundll32.assert_not_called()
            explorer.assert_not_called()

    def test_windows_browser_launch_falls_back_to_shell_execute(self) -> None:
        url = "http://127.0.0.1:8797/"
        with (
            patch.object(browser_shell.os, "name", "nt"),
            patch("requestcast.browser_shell._startfile", return_value=False),
            patch("requestcast.browser_shell._shell_execute", return_value=True) as shell_execute,
            patch("requestcast.browser_shell._rundll32_url") as rundll32,
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            self.assertTrue(browser_shell.open_default_browser(url))
            shell_execute.assert_called_once_with(url)
            rundll32.assert_not_called()

    def test_browser_launcher_runs_synchronously_after_health_check(self) -> None:
        with (
            patch("requestcast.browser_shell.server.create_browser_shortcut", return_value=None),
            patch("requestcast.browser_shell.server.wait_for_server", return_value=True) as wait,
            patch("requestcast.browser_shell.open_default_browser", return_value=True) as open_browser,
        ):
            self.assertTrue(
                browser_shell.launch_browser_when_ready("http://127.0.0.1:8797/", timeout=5.0)
            )
            wait.assert_called_once_with("http://127.0.0.1:8797/", timeout=5.0)
            open_browser.assert_called_once_with("http://127.0.0.1:8797/", None)


if __name__ == "__main__":
    unittest.main()
