"""A server configured with the older ADDTO_* variables must keep working unchanged."""

from pathlib import Path
import os
import shutil
import sys
import tempfile

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-legacy-"))
for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

# Exactly what /etc/addto-serrebiradio.env supplies.
os.environ.update({
    "REQUESTCAST_CONFIG": str(WORKSPACE / "unused.json"),
    "REQUESTCAST_DISABLE_WORKER": "1",
    "ADDTO_STATE_DIR": str(WORKSPACE / "state"),
    "ADDTO_MEDIA_DIR": str(WORKSPACE / "media"),
    "ADDTO_AZURACAST_API_BASE": "http://127.0.0.1:12000/api",
    "ADDTO_AZURACAST_API_KEY": "legacy-api-key",
    "ADDTO_REQUEST_PLAYLIST_ID": "10",
    "ADDTO_SECRET_KEY": "legacy-secret",
    "ADDTO_PASSWORD_SALT": "aa" * 32,
    "ADDTO_PASSWORD_HASH": "bb" * 32,
    "ADDTO_YTDLP": "/usr/local/bin/yt-dlp",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import config  # noqa: E402
from requestcast import app as appmod  # noqa: E402

try:
    settings = config.load()

    assert config.is_configured(settings), "the legacy environment alone must be enough"
    print("legacy_env_is_configured=passed")

    # No settings file exists, so every one of these came from ADDTO_* names.
    assert not (WORKSPACE / "unused.json").exists()
    assert settings["azuracast_api_key"] == "legacy-api-key"
    assert settings["secret_key"] == "legacy-secret"
    assert settings["azuracast_request_playlist_id"] == "10"
    assert settings["azuracast_api_base"] == "http://127.0.0.1:12000/api"
    assert settings["state_dir"] == str(WORKSPACE / "state")
    print("legacy_names_map_across=passed")

    # An API key in the environment turns the AzuraCast integration on by itself.
    assert settings["azuracast_enabled"] is True
    print("api_key_enables_azuracast=passed")

    # Values the old deployment never set must fall back to what the old code hardcoded.
    assert settings["azuracast_station_id"] == "1"
    assert settings["azuracast_upload_dir"] == "Requests"
    print("unset_values_match_old_defaults=passed")

    appmod.apply_settings(settings)
    assert appmod.AZURACAST_ENABLED is True
    assert appmod.MEDIA_DIR == WORKSPACE / "media", appmod.MEDIA_DIR
    assert appmod.REQUEST_PLAYLIST_ID == "10"
    assert appmod.station_api("/files") == "http://127.0.0.1:12000/api/station/1/files"
    assert appmod.PASSWORD_HASH == bytes.fromhex("bb" * 32)
    assert appmod.password_required() is True
    print("app_state_matches_old_behaviour=passed")

    # Behind an HTTPS proxy the session cookie must stay Secure even on a loopback bind.
    assert settings["bind_host"] == "127.0.0.1"
    assert appmod.app.config["SESSION_COOKIE_SECURE"] is True
    print("secure_cookie_kept_behind_proxy=passed")

    # The setup page is unreachable once the environment has configured everything.
    client = appmod.app.test_client()
    response = client.get("/setup")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"], response.headers["Location"]
    print("setup_locked_behind_login=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("legacy_env=passed")
