"""Preferences require the second password and can update every setup field."""

from pathlib import Path
import os
import re
import shutil
import sys
import tempfile
from unittest.mock import patch


for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-preferences-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import app as appmod  # noqa: E402
from requestcast import config  # noqa: E402


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([0-9a-f]{64})"', response.data.decode())
    assert match, "the form needs a CSRF token"
    return match.group(1)


try:
    client = appmod.app.test_client()
    downloads = WORKSPACE / "downloads"
    response = client.post(
        "/setup",
        data={
            "download_dir": str(downloads), "bind_host": "127.0.0.1", "bind_port": "8797",
            "password": "InitialListener", "admin_password": "InitialAdmin",
        },
    )
    assert response.status_code == 302
    stored = config.load()
    assert stored["admin_password_salt"]
    assert stored["admin_password_hash"]
    assert appmod.verify_admin_password("InitialAdmin")
    print("chosen_admin_password_hashed=passed")

    preferences = client.get("/preferences")
    assert preferences.status_code == 200
    assert b"<h1>Preferences</h1>" in preferences.data
    assert b"New admin password" in preferences.data
    assert b"Preferences" in client.get("/").data
    print("preferences_link_and_page=passed")

    logout_csrf = csrf_from(preferences)
    assert client.post("/logout", data={"csrf": logout_csrf}).status_code == 302
    blocked = client.get("/preferences")
    assert blocked.status_code == 302
    assert "/login" in blocked.headers["Location"]
    portal_login = client.post(
        "/login", data={"password": "InitialListener", "next": "/preferences"}
    )
    assert portal_login.status_code == 302
    blocked = client.get("/preferences")
    assert blocked.status_code == 302
    assert "/admin/login" in blocked.headers["Location"]
    wrong = client.post("/admin/login", data={"password": "wrong"})
    assert wrong.status_code == 401
    unlocked = client.post("/admin/login", data={"password": "InitialAdmin"})
    assert unlocked.status_code == 302
    assert unlocked.headers["Location"].endswith("/preferences")
    print("admin_password_gate=passed")

    preferences = client.get("/preferences")
    csrf = csrf_from(preferences)
    new_downloads = WORKSPACE / "new-downloads"
    media = WORKSPACE / "station-media"
    submitted = {
        "csrf": csrf,
        "download_dir": str(new_downloads),
        "azuracast_enabled": "1",
        "azuracast_api_base": "https://radio.example/api/",
        "azuracast_api_key": "api-key",
        "azuracast_station_id": "7",
        "azuracast_request_playlist_id": "44",
        "azuracast_media_dir": str(media),
        "azuracast_upload_dir": "On Demand",
        "password": "ListenerPass",
        "admin_password": "RotatedAdmin",
        "bind_host": "127.0.0.1",
        "bind_port": "9876",
        "deezer_arl": "test-arl",
    }
    with patch.object(appmod.deezer, "DeezerClient", return_value=object()):
        saved = client.post("/preferences", data=submitted)
    assert saved.status_code == 302
    assert saved.headers["Location"].endswith("/preferences")
    stored = config.load()
    expected = {
        "download_dir": str(new_downloads),
        "azuracast_enabled": True,
        "azuracast_api_base": "https://radio.example/api",
        "azuracast_api_key": "api-key",
        "azuracast_station_id": "7",
        "azuracast_request_playlist_id": "44",
        "azuracast_media_dir": str(media),
        "azuracast_upload_dir": "On Demand",
        "bind_host": "127.0.0.1",
        "bind_port": 9876,
        "deezer_arl": "test-arl",
    }
    for key, value in expected.items():
        assert stored[key] == value, (key, stored[key], value)
    assert appmod.verify_password("ListenerPass")
    assert appmod.verify_admin_password("RotatedAdmin")
    assert not appmod.verify_admin_password("InitialAdmin")
    print("all_preferences_saved=passed")

    preferences = client.get("/preferences")
    unsafe = client.post(
        "/preferences",
        data={
            "csrf": csrf_from(preferences),
            "download_dir": str(new_downloads),
            "bind_host": "0.0.0.0",
            "bind_port": "9876",
            "clear_password": "1",
        },
    )
    assert unsafe.status_code == 400
    assert appmod.verify_password("ListenerPass")
    print("network_password_required=passed")

    preferences = client.get("/preferences")
    assert preferences.status_code == 200
    logout_csrf = csrf_from(preferences)
    client.post("/logout", data={"csrf": logout_csrf})
    portal_login = client.post(
        "/login", data={"password": "ListenerPass", "next": "/preferences"}
    )
    assert portal_login.status_code == 302
    assert portal_login.headers["Location"] == "/preferences"
    admin_redirect = client.get("/preferences")
    assert "/admin/login" in admin_redirect.headers["Location"]
    assert client.post("/admin/login", data={"password": "InitialAdmin"}).status_code == 401
    assert client.post("/admin/login", data={"password": "RotatedAdmin"}).status_code == 302
    print("admin_password_change=passed")

    # Upgrades that predate admin passwords can set one after the listener login.
    legacy = config.load()
    legacy["admin_password_salt"] = ""
    legacy["admin_password_hash"] = ""
    config.save(legacy)
    appmod.apply_settings(config.load())
    appmod._rate_events.clear()
    with client.session_transaction() as active_session:
        active_session.clear()
    assert client.post("/login", data={"password": "ListenerPass"}).status_code == 302
    redirected = client.get("/preferences")
    assert "/admin/login" in redirected.headers["Location"]
    set_password = client.get("/admin/login?next=/preferences")
    assert b"Set admin password" in set_password.data
    csrf = csrf_from(set_password)
    missing_csrf = client.post("/admin/login", data={"password": "MigrationAdmin"})
    assert missing_csrf.status_code == 400
    created = client.post(
        "/admin/login",
        data={"csrf": csrf, "password": "MigrationAdmin", "next": "/preferences"},
    )
    assert created.status_code == 302
    assert appmod.verify_admin_password("MigrationAdmin")
    assert client.get("/preferences").status_code == 200
    print("admin_password_migration=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("preferences=passed")
