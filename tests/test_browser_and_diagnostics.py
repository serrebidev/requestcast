"""RequestCast must use the default browser and keep diagnostics off by default."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run
from requestcast import browser_shell, config


class DefaultBrowserTests(unittest.TestCase):
    def test_no_browser_profile_is_ever_created(self) -> None:
        """The old build copied a whole Chromium profile onto the user's disk."""
        source = (ROOT / "requestcast" / "browser_shell.py").read_text(encoding="utf-8")
        self.assertNotIn("--user-data-dir", source)
        self.assertNotIn("msedge.exe", source)
        self.assertNotIn("profile.mkdir", source)

    def test_the_url_goes_to_the_windows_default_handler_first(self) -> None:
        calls: list[str] = []

        def record_startfile(target: str) -> bool:
            calls.append(f"startfile:{target}")
            return True

        with (
            patch("requestcast.browser_shell.os.name", "nt"),
            patch("requestcast.browser_shell._startfile", side_effect=record_startfile),
            patch("requestcast.browser_shell._shell_execute", return_value=True),
            patch("requestcast.browser_shell.server.log_startup"),
        ):
            opened = browser_shell.open_default_browser("http://127.0.0.1:8797/")

        self.assertTrue(opened)
        self.assertEqual(calls, ["startfile:http://127.0.0.1:8797/"])

    def test_an_old_browser_profile_is_deleted_to_reclaim_disk_space(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            profile = root / "requestcast-browser-profile" / "msedge" / "Default"
            profile.mkdir(parents=True)
            (profile / "Cookies").write_bytes(b"x" * 4096)
            with (
                patch("requestcast.browser_shell.config.config_path", return_value=root / "requestcast.json"),
                patch("requestcast.browser_shell.config.app_dir", return_value=root),
                patch("requestcast.browser_shell.server.log_startup"),
            ):
                removed = browser_shell.remove_legacy_browser_profiles()
            self.assertTrue(removed)
            self.assertFalse((root / "requestcast-browser-profile").exists())

    def test_removing_profiles_is_safe_when_there_are_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            with (
                patch("requestcast.browser_shell.config.config_path", return_value=root / "requestcast.json"),
                patch("requestcast.browser_shell.config.app_dir", return_value=root),
                patch("requestcast.browser_shell.server.log_startup"),
            ):
                self.assertEqual(browser_shell.remove_legacy_browser_profiles(), [])


class DiagnosticsPreferenceTests(unittest.TestCase):
    def test_diagnostics_are_off_unless_the_preference_is_on(self) -> None:
        self.assertIs(config.FIELDS["diagnostics_enabled"], False)
        with patch.dict("os.environ", {}, clear=False):
            self.assertFalse(run.diagnostics_are_wanted({}, ["run.py"]))
            self.assertFalse(run.diagnostics_are_wanted({"diagnostics_enabled": False}, ["run.py"]))
            self.assertTrue(run.diagnostics_are_wanted({"diagnostics_enabled": True}, ["run.py"]))

    def test_the_command_line_can_turn_diagnostics_on_and_off(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(run.diagnostics_are_wanted({}, ["run.py", "--diagnostics"]))
            self.assertTrue(run.diagnostics_are_wanted({}, ["run.py"], diagnostic_only=True))
            self.assertFalse(
                run.diagnostics_are_wanted(
                    {"diagnostics_enabled": True}, ["run.py", "--no-diagnostics"]
                )
            )

    def test_the_environment_can_turn_diagnostics_on_and_off(self) -> None:
        with patch.dict("os.environ", {"REQUESTCAST_DIAGNOSTICS": "1"}, clear=False):
            self.assertTrue(run.diagnostics_are_wanted(config.load(), ["run.py"]))
        with patch.dict("os.environ", {"REQUESTCAST_DISABLE_DIAGNOSTICS": "1"}, clear=False):
            self.assertFalse(
                run.diagnostics_are_wanted({"diagnostics_enabled": True}, ["run.py"])
            )


if __name__ == "__main__":
    unittest.main()
