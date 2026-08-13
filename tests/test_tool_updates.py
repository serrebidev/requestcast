"""yt-dlp and musicdl keep themselves current.

An out-of-date yt-dlp is the usual reason a download fails with 403 while the same track
plays fine in a browser, so updating has to work without anyone asking it to.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-updates-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
os.environ["REQUESTCAST_DOWNLOAD_DIR"] = str(WORKSPACE / "downloads")
os.environ["REQUESTCAST_STATE_DIR"] = str(WORKSPACE / "state")
os.environ["REQUESTCAST_SECRET_KEY"] = "tool-update-test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import app as appmod  # noqa: E402
from requestcast import config, tools  # noqa: E402


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["tool"], returncode=returncode, stdout=stdout, stderr=stderr)


try:
    settings = {"ytdlp_path": str(WORKSPACE / "tools" / "yt-dlp.exe"), "musicdl_enabled": True}

    # Nothing is downloaded when the installed copy is already the newest.
    with (
        patch.object(tools, "find_tool", return_value="yt-dlp"),
        patch.object(tools, "ytdlp_version", return_value="2026.07.01"),
        patch.object(tools, "latest_ytdlp_version", return_value="2026.07.01"),
        patch.object(tools, "install_ytdlp") as never_installed,
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "current", result
    never_installed.assert_not_called()
    print("current_ytdlp_is_left_alone=passed")

    # A nightly is newer than the newest stable release and must never be replaced by it.
    assert tools.is_outdated("2026.01.01", "2026.07.04") is True
    assert tools.is_outdated("2026.07.23.234303", "2026.07.04") is False
    assert tools.is_outdated("2.4.0", "2.5.4") is True
    assert tools.is_outdated("2.5.4", "2.5.4") is False
    assert tools.is_outdated("2.10.0", "2.9.4") is False
    assert tools.is_outdated("", "2.5.4") is False
    assert tools.is_outdated("2.5.4-rc1", "2.5.4") is True
    with (
        patch.object(tools, "find_tool", return_value="/usr/bin/yt-dlp"),
        patch.object(tools, "ytdlp_version", return_value="2026.07.23.234303"),
        patch.object(tools, "latest_ytdlp_version", return_value="2026.07.04"),
        patch.object(tools, "_run") as never_downgraded,
        patch.object(tools, "_pip_upgrade") as never_pip,
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "current", result
    never_downgraded.assert_not_called()
    never_pip.assert_not_called()
    print("newer_build_is_never_downgraded=passed")

    # Our own copy in the tools folder is replaced with the newer release.
    managed = tools.tools_dir() / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    versions = iter(["2026.01.01", "2026.07.20"])
    with (
        patch.object(tools, "find_tool", return_value=str(managed)),
        patch.object(tools, "ytdlp_version", side_effect=lambda path: next(versions)),
        patch.object(tools, "latest_ytdlp_version", return_value="2026.07.20"),
        patch.object(tools, "install_ytdlp", return_value=str(managed)) as installed,
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "updated", result
    assert "2026.07.20" in result["message"], result
    assert result["path"] == str(managed)
    installed.assert_called_once()
    print("managed_ytdlp_is_replaced=passed")

    # Someone else's yt-dlp is never modified: RequestCast installs its own copy instead,
    # because that one may be shared with programs pinned to the version they have.
    with (
        patch.object(tools, "find_tool", return_value="/usr/local/bin/yt-dlp"),
        patch.object(tools, "ytdlp_version", return_value="2026.01.01"),
        patch.object(tools, "latest_ytdlp_version", return_value="2026.07.20"),
        patch.object(tools, "install_ytdlp", return_value=str(managed)) as own_copy,
        patch.object(tools, "_run") as never_touched,
        patch.object(tools, "_pip_upgrade") as never_pip,
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "updated", result
    assert result["path"] == str(managed)
    assert "left alone" in result["message"], result
    own_copy.assert_called_once()
    never_touched.assert_not_called()
    never_pip.assert_not_called()
    print("other_peoples_ytdlp_is_left_alone=passed")

    # A lookup that fails keeps the working copy instead of breaking it.
    with (
        patch.object(tools, "find_tool", return_value="/usr/bin/yt-dlp"),
        patch.object(tools, "ytdlp_version", return_value="2026.01.01"),
        patch.object(tools, "latest_ytdlp_version", return_value=""),
        patch.object(tools, "install_ytdlp") as untouched,
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "skipped", result
    untouched.assert_not_called()
    print("failed_version_lookup_is_safe=passed")

    # A download that fails is reported, and nothing is left half-installed.
    with (
        patch.object(tools, "find_tool", return_value=""),
        patch.object(tools, "install_ytdlp", side_effect=RuntimeError("no network")),
    ):
        result = tools.update_ytdlp(settings)
    assert result["status"] == "failed" and "no network" in result["message"], result
    print("failed_ytdlp_download_is_reported=passed")

    # Deno is what answers YouTube's JavaScript challenges, so a missing one is installed.
    installed_deno = str(WORKSPACE / "tools" / "deno")
    with (
        patch.object(tools, "find_tool", return_value=""),
        patch.object(tools, "deno_asset", return_value="deno-x86_64-pc-windows-msvc.zip"),
        patch.object(tools, "install_deno", return_value=installed_deno) as fetched,
        patch.object(tools, "deno_version", return_value="2.5.4"),
    ):
        result = tools.update_deno(settings)
    assert result["status"] == "updated", result
    assert result["path"] == installed_deno
    assert "JavaScript" in result["message"], result
    fetched.assert_called_once()
    print("missing_deno_is_installed=passed")

    # Our own copy is replaced when a newer Deno is out; a current one is left alone.
    managed_deno = tools.tools_dir() / ("deno.exe" if os.name == "nt" else "deno")
    deno_versions = iter(["2.4.0", "2.5.4"])
    with (
        patch.object(tools, "find_tool", return_value=str(managed_deno)),
        patch.object(tools, "deno_version", side_effect=lambda path: next(deno_versions)),
        patch.object(tools, "latest_deno_version", return_value="2.5.4"),
        patch.object(tools, "install_deno", return_value=str(managed_deno)),
    ):
        result = tools.update_deno(settings)
    assert result["status"] == "updated" and result["path"] == str(managed_deno), result
    with (
        patch.object(tools, "find_tool", return_value=str(managed_deno)),
        patch.object(tools, "deno_version", return_value="2.5.4"),
        patch.object(tools, "latest_deno_version", return_value="2.5.4"),
        patch.object(tools, "install_deno") as untouched_deno,
    ):
        assert tools.update_deno(settings)["status"] == "current"
    untouched_deno.assert_not_called()
    print("managed_deno_is_kept_current=passed")

    # A Deno someone else installed is never upgraded underneath them — other programs
    # may depend on that exact version. RequestCast takes its own copy instead.
    with (
        patch.object(tools, "find_tool", return_value="/usr/local/bin/deno"),
        patch.object(tools, "deno_version", return_value="2.4.0"),
        patch.object(tools, "latest_deno_version", return_value="2.5.4"),
        patch.object(tools, "install_deno", return_value=str(managed_deno)) as own_deno,
        patch.object(tools, "_run") as never_upgraded,
    ):
        result = tools.update_deno(settings)
    assert result["status"] == "updated" and result["path"] == str(managed_deno), result
    assert "left alone" in result["message"], result
    own_deno.assert_called_once()
    never_upgraded.assert_not_called()
    print("other_peoples_deno_is_left_alone=passed")

    # A machine with no Deno build says so instead of failing silently.
    with (
        patch.object(tools, "find_tool", return_value=""),
        patch.object(tools, "deno_asset", return_value=""),
    ):
        result = tools.update_deno(settings)
    assert result["status"] == "skipped" and "deno.com" in result["message"], result
    print("unsupported_platform_reports_deno=passed")

    # The version string Deno prints is read correctly.
    with patch.object(tools, "_run", return_value=completed(stdout="deno 2.5.4 (stable, release, x86_64-pc-windows-msvc)\nv8 14.0\ntypescript 5.9")):
        assert tools.deno_version("deno") == "2.5.4"
    with patch.object(tools, "_run", return_value=completed(1, stderr="not found")):
        assert tools.deno_version("deno") == ""
    assert tools.deno_version("") == ""
    print("deno_version_is_read=passed")

    # This machine has a Deno build, and the download URL points at it.
    asset = tools.deno_asset()
    assert asset and asset.endswith(".zip"), asset
    assert asset in tools.DENO_DOWNLOAD_URL.format(asset=asset)
    print("deno_asset_matches_this_machine=passed")

    # Deno counts as a required tool, and can be fetched on this machine.
    with patch.object(tools, "find_tool", return_value=""):
        assert "deno" in tools.missing_tools({})
        assert "deno" in tools.installable_tools({})
    assert tools.can_auto_install() is True
    print("deno_is_a_required_tool=passed")

    # ffprobe is a required tool alongside ffmpeg, and is reported when missing.
    with patch.object(tools, "find_tool", return_value=""):
        missing = tools.missing_tools({})
        assert "ffmpeg" in missing and "ffprobe" in missing, missing
    print("ffprobe_is_a_required_tool=passed")

    # musicdl is upgraded from PyPI when a newer release exists.
    with (
        patch.object(tools, "installed_package_version", return_value="2.3.6"),
        patch.object(tools, "latest_package_version", return_value="2.4.0"),
        patch.object(tools, "_pip_upgrade", return_value={"name": "musicdl", "status": "updated", "message": "Upgraded musicdl to 2.4.0."}) as pip,
    ):
        result = tools.update_musicdl(settings)
    assert result["status"] == "updated", result
    pip.assert_called_once_with("musicdl")

    with (
        patch.object(tools, "installed_package_version", return_value="2.4.0"),
        patch.object(tools, "latest_package_version", return_value="2.4.0"),
        patch.object(tools, "_pip_upgrade") as never,
    ):
        assert tools.update_musicdl(settings)["status"] == "current"
    never.assert_not_called()
    assert tools.update_musicdl({"musicdl_enabled": False})["status"] == "skipped"
    print("musicdl_upgrades_from_pypi=passed")

    # The portable build cannot pip install into itself and says so plainly.
    with patch.object(config, "is_frozen", return_value=True):
        frozen = tools._pip_upgrade("musicdl")
    assert frozen["status"] == "skipped", frozen
    assert "portable" in frozen["message"], frozen
    print("frozen_build_reports_bundled_musicdl=passed")

    # A deployment venv that is root-owned or systemd-hardened read-only is left
    # alone: it updates with the deployment, and the updater must not fail every cycle.
    with (
        patch.object(config, "is_frozen", return_value=False),
        patch.object(tools, "_pip_target_writable", return_value=False),
        patch.object(tools, "_run") as never_pip,
    ):
        readonly = tools._pip_upgrade("musicdl")
    assert readonly["status"] == "skipped", readonly
    assert "deployment" in readonly["message"], readonly
    never_pip.assert_not_called()
    print("readonly_environment_skips_musicdl=passed")

    # A read-only-filesystem error from pip is recognised and reported as a skip too,
    # rather than a broken tool.
    with (
        patch.object(config, "is_frozen", return_value=False),
        patch.object(tools, "_pip_target_writable", return_value=True),
        patch.object(tools, "_run", return_value=completed(
            1, stderr="Read-only file system: /opt/requestcast/.venv/bin/musicdl",
        )),
    ):
        readonly_pip = tools._pip_upgrade("musicdl")
    assert readonly_pip["status"] == "skipped", readonly_pip
    assert "deployment" in readonly_pip["message"], readonly_pip
    print("pip_readonly_error_is_a_skip=passed")

    # A pip failure is reported rather than raised.
    with (
        patch.object(config, "is_frozen", return_value=False),
        patch.object(tools, "_pip_target_writable", return_value=True),
        patch.object(tools, "_run", return_value=completed(1, stderr="No matching distribution")),
    ):
        broken = tools._pip_upgrade("musicdl")
    assert broken["status"] == "failed", broken
    assert "No matching distribution" in broken["message"], broken
    print("pip_failure_is_reported=passed")

    # Versions are read from the published sources, and unreadable answers are empty.
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    with patch.object(tools.requests, "get", return_value=FakeResponse({"tag_name": "2026.07.20"})):
        assert tools.latest_ytdlp_version() == "2026.07.20"
    with patch.object(tools.requests, "get", return_value=FakeResponse({"info": {"version": "2.4.0"}})):
        assert tools.latest_package_version("musicdl") == "2.4.0"
    with patch.object(tools.requests, "get", side_effect=tools.requests.RequestException("offline")):
        assert tools.latest_ytdlp_version() == ""
        assert tools.latest_package_version("musicdl") == ""
    print("published_versions_are_read=passed")

    # Checks are remembered so they happen on a schedule rather than every minute.
    state = WORKSPACE / "state"
    assert tools.last_update_check(state) == 0.0
    tools.record_update_check(state, [{"name": "yt-dlp", "status": "current", "message": "fine"}])
    assert tools.last_update_check(state) > 0
    print("update_checks_are_remembered=passed")

    # The update runs every tool and reports one outcome each.
    with (
        patch.object(tools, "update_ytdlp", return_value={"name": "yt-dlp", "status": "current", "message": "a"}),
        patch.object(tools, "update_deno", return_value={"name": "deno", "status": "current", "message": "b"}),
        patch.object(tools, "update_musicdl", return_value={"name": "musicdl", "status": "current", "message": "c"}),
    ):
        results = tools.update_all(settings)
    assert [item["name"] for item in results] == ["yt-dlp", "deno", "musicdl"], results
    print("update_all_covers_every_tool=passed")

    # Missing tools install themselves, and where they landed is remembered.
    (WORKSPACE / "tools").mkdir(parents=True, exist_ok=True)
    new_ytdlp = str(WORKSPACE / "tools" / "yt-dlp")
    new_deno = str(WORKSPACE / "tools" / "deno")
    Path(new_ytdlp).write_text("stub", encoding="utf-8")
    Path(new_deno).write_text("stub", encoding="utf-8")
    with (
        patch.object(appmod, "WORKER_DISABLED", False),
        patch.object(tools, "installable_tools", return_value=["yt-dlp", "deno"]),
        patch.object(tools, "install_missing", return_value={"ytdlp_path": new_ytdlp, "deno_path": new_deno}) as auto,
    ):
        installed = appmod.ensure_tools_installed()
    assert installed == {"ytdlp_path": new_ytdlp, "deno_path": new_deno}, installed
    auto.assert_called_once()
    assert config.load()["deno_path"] == new_deno
    assert appmod.DENO == new_deno
    print("missing_tools_install_themselves=passed")

    # Nothing is fetched when everything is already present.
    with (
        patch.object(appmod, "WORKER_DISABLED", False),
        patch.object(tools, "installable_tools", return_value=[]),
        patch.object(tools, "install_missing") as never_fetched,
    ):
        assert appmod.ensure_tools_installed() == {}
    never_fetched.assert_not_called()
    print("present_tools_are_left_alone=passed")

    # yt-dlp is told where our own Deno is, because it is not on PATH.
    with patch.object(appmod, "DENO", new_deno):
        assert appmod.js_runtime_arguments() == ["--js-runtimes", f"deno:{new_deno}"]
    with patch.object(appmod, "DENO", ""):
        assert appmod.js_runtime_arguments() == []
    print("ytdlp_is_pointed_at_deno=passed")

    # A 403 with no Deno installed is explained by the missing runtime, not by pacing.
    with patch.object(appmod, "DENO", ""):
        message = appmod.describe_download_error("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert "Deno is not installed" in message, message
    with patch.object(appmod, "DENO", new_deno):
        message = appmod.describe_download_error("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert "rate limiting" in message, message
    print("missing_deno_explains_403=passed")

    # Preferences offer the update, and running it saves a newly installed yt-dlp path.
    client = appmod.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["admin_authenticated"] = True
        session["nonce"] = "tool-update"
    csrf = hmac.new(appmod.SECRET_KEY.encode(), b"tool-update", hashlib.sha256).hexdigest()
    with patch.object(tools, "ytdlp_version", return_value="2026.07.20"):
        page = client.get("/preferences").data.decode()
    assert "Check for updates and install them" in page
    assert "2026.07.20" in page
    assert 'name="auto_update_interval_hours"' in page

    new_path = str(WORKSPACE / "tools" / "yt-dlp-new")
    with patch.object(
        tools, "update_all",
        return_value=[{"name": "yt-dlp", "status": "updated", "message": "Updated yt-dlp.", "path": new_path}],
    ):
        response = client.post("/setup/tools/update", data={"csrf": csrf})
    assert response.status_code == 302, response.status_code
    assert config.load()["ytdlp_path"] == new_path, config.load()["ytdlp_path"]
    assert client.post("/setup/tools/update", data={}).status_code == 400
    print("preferences_can_update_tools=passed")

    # A settings file that cannot be written must not cost us the update. A service
    # confined to a few writable paths, or a portable copy on read-only media, still has
    # to end up using the tool it just installed, and the updater has to survive to run
    # again — losing the background thread here would leave yt-dlp to rot.
    before = config.load()["ytdlp_path"]
    unwritable = str(WORKSPACE / "tools" / "yt-dlp-unwritable")
    Path(unwritable).write_text("stub", encoding="utf-8")
    with patch.object(config, "save", side_effect=OSError("Read-only file system")) as refused:
        appmod.save_tool_paths([
            {"name": "yt-dlp", "status": "updated", "message": "Updated yt-dlp.", "path": unwritable},
        ])
    refused.assert_called_once()
    assert appmod.YTDLP == unwritable, appmod.YTDLP
    assert config.load()["ytdlp_path"] == before, "the unwritable save must not have landed"

    with (
        patch.object(appmod, "WORKER_DISABLED", False),
        patch.object(tools, "installable_tools", return_value=["deno"]),
        patch.object(tools, "install_missing", return_value={"deno_path": new_deno}),
        patch.object(config, "save", side_effect=OSError("Read-only file system")),
    ):
        assert appmod.ensure_tools_installed() == {"deno_path": new_deno}
    assert appmod.DENO == new_deno, appmod.DENO

    # The scheduled round trip runs start to finish with nowhere to write.
    with (
        patch.object(config, "save", side_effect=OSError("Read-only file system")),
        patch.object(
            tools, "update_all",
            return_value=[{"name": "yt-dlp", "status": "updated", "message": "Updated yt-dlp.", "path": unwritable}],
        ),
    ):
        results = tools.update_all(config.load())
        tools.record_update_check(WORKSPACE / "state", results)
        appmod.save_tool_paths(results)
    assert tools.last_update_check(WORKSPACE / "state") > 0
    print("unwritable_settings_do_not_lose_the_update=passed")

    appmod.apply_settings(config.load())

    # Automatic updating can be turned off and its interval changed.
    stored = config.load()
    stored.update({"auto_update_tools": False, "auto_update_interval_hours": 6})
    config.save(stored)
    appmod.apply_settings(config.load())
    assert appmod.AUTO_UPDATE_TOOLS is False
    assert appmod.AUTO_UPDATE_INTERVAL_HOURS == 6
    stored = config.load()
    stored["auto_update_interval_hours"] = 100000
    config.save(stored)
    assert config.load()["auto_update_interval_hours"] == config.NUMERIC_LIMITS["auto_update_interval_hours"][1]
    print("automatic_updates_are_configurable=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("tool_updates=passed")
