"""Regression tests for local Windows web interface access."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("REQUESTCAST_DISABLE_WORKER", "1")

import run as entrypoint
from requestcast import browser_shell, server
from requestcast.app import app as flask_app


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

    def test_waitress_flushes_small_pages_immediately(self) -> None:
        self.assertEqual(entrypoint.WAITRESS_SEND_BYTES, 1)

    def test_legacy_secure_cookie_is_disabled_for_loopback_http(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "127.0.0.1")
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])

    def test_network_bind_keeps_secure_cookie_setting(self) -> None:
        app = DummyApp()
        server.allow_loopback_http_sessions(app, "0.0.0.0")
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_real_flask_root_page_renders_requestcast_html(self) -> None:
        response = flask_app.test_client().get("/", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RequestCast", response.data)
        self.assertIn("text/html", response.content_type)

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

    @patch(
        "requestcast.browser_shell._request_page",
        side_effect=[
            (302, {"location": "/login"}, b""),
            (200, {"content-type": "text/html; charset=utf-8"}, b"<title>RequestCast</title>"),
        ],
    )
    def test_real_web_page_probe_follows_local_redirects(self, request_page) -> None:
        ok, detail = browser_shell.probe_web_interface("http://127.0.0.1:8797/")
        self.assertTrue(ok)
        self.assertIn("RequestCast HTML verified", detail)
        self.assertEqual(request_page.call_count, 2)

    @patch(
        "requestcast.browser_shell._request_page",
        return_value=(500, {"content-type": "text/html"}, b"Internal Server Error"),
    )
    def test_real_web_page_probe_rejects_broken_root_page(self, _request_page) -> None:
        ok, detail = browser_shell.probe_web_interface("http://127.0.0.1:8797/")
        self.assertFalse(ok)
        self.assertIn("HTTP 500", detail)

    def test_windows_browser_launch_uses_clean_no_proxy_browser_first(self) -> None:
        url = "http://127.0.0.1:8797/"
        executable = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        with (
            patch.object(browser_shell.os, "name", "nt"),
            patch("requestcast.browser_shell._launch_clean_chromium", return_value=executable) as clean,
            patch("requestcast.browser_shell._startfile") as startfile,
            patch("requestcast.browser_shell._shell_execute") as shell_execute,
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            self.assertTrue(browser_shell.open_default_browser(url))
            clean.assert_called_once_with(url)
            startfile.assert_not_called()
            shell_execute.assert_not_called()

    def test_windows_browser_launch_falls_back_to_url_association(self) -> None:
        url = "http://127.0.0.1:8797/"
        with (
            patch.object(browser_shell.os, "name", "nt"),
            patch("requestcast.browser_shell._launch_clean_chromium", return_value=None),
            patch("requestcast.browser_shell._startfile", return_value=True) as startfile,
            patch("requestcast.browser_shell._shell_execute") as shell_execute,
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            self.assertTrue(browser_shell.open_default_browser(url))
            startfile.assert_called_once_with(url)
            shell_execute.assert_not_called()

    def test_browser_launcher_checks_real_page_before_opening(self) -> None:
        with (
            patch("requestcast.browser_shell.server.create_browser_shortcut", return_value=None),
            patch("requestcast.browser_shell.server.wait_for_server", return_value=True) as wait,
            patch(
                "requestcast.browser_shell.probe_web_interface",
                return_value=(True, "RequestCast HTML verified"),
            ) as probe,
            patch("requestcast.browser_shell.open_default_browser", return_value=True) as open_browser,
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            self.assertTrue(
                browser_shell.launch_browser_when_ready("http://127.0.0.1:8797/", timeout=5.0)
            )
            wait.assert_called_once_with("http://127.0.0.1:8797/", timeout=5.0)
            probe.assert_called_once_with("http://127.0.0.1:8797/")
            open_browser.assert_called_once_with("http://127.0.0.1:8797/", None)


if __name__ == "__main__":
    unittest.main()
