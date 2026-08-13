"""Run RequestCast's self-contained automated test scripts.

The repository also has fixture-driven and live-server checks. Those are intentionally
not included here because they require private playlist files or an already configured
RequestCast/AzuraCast deployment.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_artist_browse.py",
    "test_browser_and_diagnostics.py",
    "test_deezer_download.py",
    "test_deezer_import_match.py",
    "test_download_permissions.py",
    "test_download_retries.py",
    "test_first_run.py",
    "test_job_history.py",
    "test_large_import.py",
    "test_legacy_env.py",
    "test_live_streams.py",
    "test_musicdl_source.py",
    "test_playlist_import.py",
    "test_preferences.py",
    "test_quality_preservation.py",
    "test_request_flow.py",
    "test_search_limits.py",
    "test_server_http.py",
    "test_soulseek.py",
    "test_tool_updates.py",
    "test_upload_route.py",
    "test_url_input.py",
    "test_youtube_collections.py",
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="requestcast-suite-") as temp_name:
        temp = Path(temp_name)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("REQUESTCAST_", "ADDTO_"))
        }
        environment.update(
            {
                "REQUESTCAST_CONFIG": str(temp / "config.json"),
                "REQUESTCAST_DISABLE_WORKER": "1",
                "REQUESTCAST_DOWNLOAD_DIR": str(temp / "downloads"),
                "REQUESTCAST_STATE_DIR": str(temp / "state"),
                "REQUESTCAST_SECRET_KEY": "automated-test-secret-key",
            }
        )

        for filename in TESTS:
            command = [sys.executable, str(ROOT / "tests" / filename)]
            if filename == "test_quality_preservation.py":
                command.append(str(temp / "quality"))
            print(f"\n== {filename} ==", flush=True)
            result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
            if result.returncode:
                print(f"{filename} failed with exit code {result.returncode}.", file=sys.stderr)
                return result.returncode

    print(f"\nAll {len(TESTS)} automated test scripts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
