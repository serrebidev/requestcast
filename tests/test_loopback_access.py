"""Regression tests for local Windows web interface access."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
