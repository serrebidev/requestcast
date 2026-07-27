"""Finished downloads must be readable by every account on the computer."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from requestcast import app, permissions


class PermissionTests(unittest.TestCase):
    def test_a_downloaded_file_becomes_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            music = Path(temporary_name) / "Artist - Title.flac"
            music.write_bytes(b"audio")
            if os.name != "nt":
                # Start from the private permissions a temporary folder hands out.
                os.chmod(music, 0o600)
                self.assertFalse(permissions.readable_by_everyone(music))
            self.assertTrue(permissions.make_readable(music))
            self.assertTrue(permissions.readable_by_everyone(music))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(music.stat().st_mode), permissions.FILE_MODE)

    def test_a_download_folder_becomes_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            folder = Path(temporary_name) / "Downloads"
            folder.mkdir()
            self.assertTrue(permissions.make_readable(folder))
            self.assertTrue(permissions.readable_by_everyone(folder))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode), permissions.DIRECTORY_MODE)

    def test_a_missing_file_is_reported_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            self.assertFalse(permissions.make_readable(Path(temporary_name) / "absent.flac"))

    def test_the_downloader_sets_permissions_after_moving_the_file(self) -> None:
        """A move keeps the temporary folder's private permissions, so they are reset."""
        source = (ROOT / "requestcast" / "app.py").read_text(encoding="utf-8")
        downloader = source.split("def download_one(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("permissions.make_readable(DOWNLOAD_DIR)", downloader)
        self.assertIn("permissions.make_readable(local_path)", downloader)
        self.assertNotIn("os.chmod(prepared", downloader)
        self.assertIs(app.permissions, permissions)


if __name__ == "__main__":
    unittest.main()
