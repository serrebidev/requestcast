"""First run requires both passwords and supports local-only downloads."""

from pathlib import Path
import os
import re
import shutil
import sys
import tempfile

# A fresh install means a fresh environment: drop anything the shell already set.
for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-first-run-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import config  # noqa: E402
from requestcast import app as appmod  # noqa: E402

try:
    assert not config.is_configured(), "a fresh install must not look configured"

    client = appmod.app.test_client()

    response = client.get("/")
    assert response.status_code == 302, response.status_code
    assert "/setup" in response.headers["Location"], response.headers["Location"]
    print("unconfigured_redirects_to_setup=passed")

    page = client.get("/setup")
    assert page.status_code == 200
    assert b"Download folder" in page.data
    assert b"Send finished downloads to an AzuraCast station" in page.data
    assert b'input id="password" name="password" type="password" required' in page.data
    assert b'input id="admin_password" name="admin_password" type="password"' in page.data
    assert b"Default:" not in page.data
    print("setup_page_renders=passed")

    # Both passwords are required, even in local-only mode.
    refused = client.post("/setup", data={"download_dir": str(WORKSPACE / "music"), "bind_host": "127.0.0.1"})
    assert refused.status_code == 400, refused.status_code
    assert b"Set a password" in refused.data
    assert b"Set an admin password" in refused.data
    print("first_run_requires_both_passwords=passed")

    downloads = WORKSPACE / "music"
    saved = client.post("/setup", data={
        "download_dir": str(downloads), "bind_host": "127.0.0.1",
        "password": "ListenerPass", "admin_password": "InitialAdmin",
    })
    assert saved.status_code == 302, saved.status_code
    assert saved.headers["Location"].endswith("/")
    print("setup_saves=passed")

    assert downloads.is_dir(), "the download folder should have been created"
    stored = config.load_file(Path(os.environ["REQUESTCAST_CONFIG"]))
    assert stored["download_dir"] == str(downloads)
    assert stored["secret_key"], "a session key should have been generated"
    assert stored["azuracast_enabled"] is False
    assert stored["password_hash"], "the listener password should be hashed"
    assert stored["admin_password_hash"], "the admin password should be hashed"
    assert stored["admin_password_salt"], "the admin password should have a unique salt"
    assert appmod.verify_password("ListenerPass")
    assert appmod.verify_admin_password("InitialAdmin")
    print("settings_written=passed")

    # Setup authenticates the new session straight away.
    home = client.get("/")
    assert home.status_code == 200, home.status_code
    assert b"Upload a music list" in home.data
    print("setup_session_authenticated=passed")

    assert appmod.AZURACAST_ENABLED is False
    assert appmod.MEDIA_DIR == downloads, appmod.MEDIA_DIR
    assert appmod.DB_PATH.exists(), "the job database should have been created"
    print("local_mode_paths=passed")

    # AzuraCast on, but with nothing filled in, must be rejected rather than half-configured.
    settings_page = client.get("/preferences")
    csrf = re.search(r'name="csrf" value="([0-9a-f]{64})"', settings_page.data.decode())
    assert csrf, "the settings form should carry a CSRF token once configured"
    bad = client.post("/preferences", data={
        "csrf": csrf.group(1), "download_dir": str(downloads),
        "bind_host": "127.0.0.1", "azuracast_enabled": "1",
    })
    assert bad.status_code == 400
    assert b"AzuraCast base API address" in bad.data
    print("azuracast_requires_details=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("first_run=passed")
