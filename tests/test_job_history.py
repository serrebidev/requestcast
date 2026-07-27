"""People must be able to clear their download history."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


def add_job(state: str) -> str:
    job_id = uuid.uuid4().hex
    now = int(time.time())
    with app.db_connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id,state,label,detail,payload,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, state, f"{state} job", "", json.dumps({"source": "youtube"}), now, now),
        )
    return job_id


def job_states() -> dict[str, str]:
    with app.db_connect() as connection:
        rows = connection.execute("SELECT id, state FROM jobs").fetchall()
    return {row["id"]: row["state"] for row in rows}


def signed_in_client(nonce: str):
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    return client, hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()


with app.db_connect() as connection:
    connection.execute("DELETE FROM jobs")

completed = add_job("completed")
failed = add_job("failed")
queued = add_job("queued")
running = add_job("running")

client, csrf = signed_in_client("clear-finished")
response = client.post("/jobs/clear", data={"csrf": csrf, "scope": "finished"})
assert response.status_code == 302, response.status_code
remaining = job_states()
assert completed not in remaining and failed not in remaining, remaining
assert remaining[queued] == "queued" and remaining[running] == "running", remaining
print("clear_finished_history=passed")

completed = add_job("completed")
client, csrf = signed_in_client("clear-all")
response = client.post("/jobs/clear", data={"csrf": csrf, "scope": "all"})
assert response.status_code == 302, response.status_code
remaining = job_states()
assert completed not in remaining, remaining
# Work that is still running is never removed from underneath the downloader.
assert remaining[queued] == "queued" and remaining[running] == "running", remaining
print("clear_all_history=passed")

# Clearing needs a valid CSRF token, so another site cannot wipe the history.
client, _csrf = signed_in_client("clear-no-csrf")
response = client.post("/jobs/clear", data={"scope": "all"})
assert response.status_code == 400, response.status_code
print("clear_history_requires_csrf=passed")

# An unknown scope is refused rather than guessed at.
client, csrf = signed_in_client("clear-bad-scope")
response = client.post("/jobs/clear", data={"csrf": csrf, "scope": "everything-forever"})
assert response.status_code == 400, response.status_code
print("clear_history_scope_validated=passed")

# One finished job can be removed on its own; a running one cannot.
single = add_job("completed")
client, csrf = signed_in_client("delete-one")
response = client.post(f"/jobs/{single}/delete", data={"csrf": csrf})
assert response.status_code == 302, response.status_code
assert single not in job_states()
print("delete_one_job=passed")

client, csrf = signed_in_client("delete-running")
response = client.post(f"/jobs/{running}/delete", data={"csrf": csrf})
assert response.status_code == 302, response.status_code
assert job_states()[running] == "running"
print("running_job_is_kept=passed")

client, csrf = signed_in_client("delete-missing")
response = client.post(f"/jobs/{uuid.uuid4().hex}/delete", data={"csrf": csrf})
assert response.status_code == 404, response.status_code
print("delete_unknown_job_is_404=passed")

with app.db_connect() as connection:
    connection.execute("DELETE FROM jobs")
