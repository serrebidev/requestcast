"""Refused downloads are retried, paced, and can be queued again by hand.

Bulk runs — a channel, a discography, a playlist import — get refused in batches with
403 or "video unavailable" and then succeed later, so the retry behaviour is what keeps
those imports whole.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from unittest.mock import patch


for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-retries-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
os.environ["REQUESTCAST_DOWNLOAD_DIR"] = str(WORKSPACE / "downloads")
os.environ["REQUESTCAST_STATE_DIR"] = str(WORKSPACE / "state")
os.environ["REQUESTCAST_SECRET_KEY"] = "download-retry-test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import app as appmod  # noqa: E402
from requestcast import config  # noqa: E402


MEDIA = {"unique_id": "media-id", "path": "Requests/track.mp3"}


def track(number: int) -> dict:
    return {"source": "youtube", "kind": "song", "artist": "Test Artist",
            "title": f"Track {number}", "video_id": f"video{number:04d}"}


def signed_in_client(nonce: str):
    client = appmod.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["admin_authenticated"] = True
        session["nonce"] = nonce
    return client, hmac.new(appmod.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def add_job(state: str, payload: dict | None = None) -> str:
    job_id = uuid.uuid4().hex
    now = int(time.time())
    with appmod.db_connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id,state,label,detail,payload,created_at,updated_at,attempts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, state, "Test job", "", json.dumps(payload or {"source": "youtube"}), now, now, 2),
        )
    return job_id


try:
    # A 403 is read as the site pushing back; a missing file is not.
    assert appmod.looks_rate_limited("HTTP Error 403: Forbidden")
    assert appmod.looks_rate_limited("ERROR: [youtube] abc: Video unavailable")
    assert appmod.looks_rate_limited("Sign in to confirm you're not a bot")
    assert appmod.looks_rate_limited("HTTP Error 429: Too Many Requests")
    assert not appmod.looks_rate_limited("No such file or directory")
    assert not appmod.looks_rate_limited("This track has no direct download URL to store.")
    print("rate_limit_failures_recognised=passed")

    # The wording explains what a bare 403 actually means. With Deno present that is
    # rate limiting; without it, the missing runtime is the real cause and says so.
    with patch.object(appmod, "DENO", "deno"):
        described = appmod.describe_download_error("HTTP Error 403: Forbidden")
    assert "rate limiting" in described, described
    assert "403" in described
    with patch.object(appmod, "DENO", ""):
        described = appmod.describe_download_error("HTTP Error 403: Forbidden")
    assert "Deno is not installed" in described, described
    print("download_errors_explained=passed")

    # Waits double, and never exceed the cooldown ceiling.
    with (
        patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 10),
        patch.object(appmod, "RATE_LIMIT_COOLDOWN", 60),
    ):
        assert appmod.retry_delay_for(1) == 10
        assert appmod.retry_delay_for(2) == 20
        assert appmod.retry_delay_for(3) == 40
        assert appmod.retry_delay_for(9) == 60
    with patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 0):
        assert appmod.retry_delay_for(3) == 0
    print("retry_delay_backs_off=passed")

    # A track refused twice and then accepted still counts as downloaded.
    attempts: list[int] = []

    def flaky(track_data, attempt=0):
        attempts.append(attempt)
        if len(attempts) < 3:
            raise RuntimeError("HTTP Error 403: Forbidden")
        return "downloaded", Path("track.mp3"), MEDIA

    with (
        patch.object(appmod, "DOWNLOAD_RETRIES", 2),
        patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 0),
        patch.object(appmod, "DOWNLOAD_GAP_SECONDS", 0),
        patch.object(appmod, "download_one", side_effect=flaky),
        patch.object(appmod, "update_job"),
    ):
        completed, _request_id, errors = appmod.run_downloads([track(1)], "job")
    assert completed == 1, completed
    assert errors == [], errors
    # Each attempt asks YouTube as a different client.
    assert attempts == [0, 1, 2], attempts
    print("track_retried_until_it_succeeds=passed")

    # Retries can be turned off entirely, and then one failure is one failure.
    calls: list[int] = []

    def always_refused(track_data, attempt=0):
        calls.append(attempt)
        raise RuntimeError("HTTP Error 403: Forbidden")

    with (
        patch.object(appmod, "DOWNLOAD_RETRIES", 0),
        patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 0),
        patch.object(appmod, "DOWNLOAD_GAP_SECONDS", 0),
        patch.object(appmod, "download_one", side_effect=always_refused),
        patch.object(appmod, "update_job"),
    ):
        completed, _request_id, errors = appmod.run_downloads([track(1)], "job")
    assert completed == 0 and len(calls) == 1, (completed, calls)
    assert len(errors) == 1 and "403" in errors[0], errors
    print("retries_can_be_turned_off=passed")

    # A run of refusals pauses the job, and the final pass picks up what recovered.
    seen: list[str] = []

    def refuse_first_pass(track_data, attempt=0):
        title = track_data["title"]
        seen.append(title)
        if seen.count(title) <= 2:
            raise RuntimeError("HTTP Error 403: Forbidden")
        return "downloaded", Path("track.mp3"), MEDIA

    with (
        patch.object(appmod, "DOWNLOAD_RETRIES", 1),
        patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 0),
        patch.object(appmod, "DOWNLOAD_GAP_SECONDS", 0),
        patch.object(appmod, "RATE_LIMIT_COOLDOWN", 0),
        patch.object(appmod, "download_one", side_effect=refuse_first_pass),
        patch.object(appmod, "update_job") as updates,
    ):
        completed, _request_id, errors = appmod.run_downloads(
            [track(1), track(2), track(3)], "job"
        )
    assert completed == 3, completed
    assert errors == [], errors
    details = " ".join(str(call.kwargs.get("detail", "")) for call in updates.call_args_list)
    assert "Final pass" in details, details
    print("final_pass_recovers_refused_tracks=passed")

    # Repeated refusals trigger a cooldown before the queue carries on.
    with (
        patch.object(appmod, "DOWNLOAD_RETRIES", 0),
        patch.object(appmod, "DOWNLOAD_GAP_SECONDS", 0),
        patch.object(appmod, "RATE_LIMIT_COOLDOWN", 90),
        patch.object(appmod, "download_one", side_effect=RuntimeError("HTTP Error 403: Forbidden")),
        patch.object(appmod, "update_job"),
        patch.object(appmod.time, "sleep") as slept,
    ):
        completed, _request_id, errors = appmod.run_downloads(
            [track(1), track(2), track(3)], "job"
        )
    assert completed == 0 and len(errors) == 3, (completed, errors)
    assert 90 in [call.args[0] for call in slept.call_args_list], slept.call_args_list
    print("repeated_refusals_pause_the_queue=passed")

    # Tracks are spaced out so a long queue does not trip the limit to begin with.
    with (
        patch.object(appmod, "DOWNLOAD_RETRIES", 0),
        patch.object(appmod, "DOWNLOAD_GAP_SECONDS", 4),
        patch.object(appmod, "download_one", return_value=("downloaded", Path("t.mp3"), MEDIA)),
        patch.object(appmod, "update_job"),
        patch.object(appmod.time, "sleep") as paced,
    ):
        completed, _request_id, errors = appmod.run_downloads([track(1), track(2), track(3)], "job")
    assert completed == 3, completed
    # A gap before every track after the first, and none before the first.
    assert [call.args[0] for call in paced.call_args_list] == [4, 4], paced.call_args_list
    print("downloads_are_paced=passed")

    # A whole job that fails goes back in the queue while it has retries left.
    row = {"id": "job-1", "attempts": 0}
    with (
        patch.object(appmod, "JOB_RETRY_LIMIT", 2),
        patch.object(appmod, "DOWNLOAD_RETRY_DELAY", 0),
        patch.object(appmod, "update_job") as requeued,
    ):
        appmod.fail_or_requeue(row, RuntimeError("HTTP Error 403: Forbidden"))
    assert requeued.call_args_list[0].kwargs["state"] == "queued"
    row = {"id": "job-1", "attempts": 2}
    with (
        patch.object(appmod, "JOB_RETRY_LIMIT", 2),
        patch.object(appmod, "DENO", "deno"),
        patch.object(appmod, "update_job") as gave_up,
    ):
        appmod.fail_or_requeue(row, RuntimeError("HTTP Error 403: Forbidden"))
    assert gave_up.call_args.kwargs["state"] == "failed"
    assert "rate limiting" in gave_up.call_args.kwargs["error"]
    print("failed_jobs_requeue_until_the_limit=passed")

    # Claiming a job counts the attempt, so retries cannot loop forever.
    job_id = add_job("queued")
    claimed = appmod.claim_job()
    assert claimed is not None and claimed["id"] == job_id
    with appmod.db_connect() as connection:
        stored = connection.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert stored["attempts"] == 3, stored["attempts"]
    print("attempts_are_counted=passed")

    # Any finished download can be queued again by hand.
    failed_job = add_job("failed")
    client, csrf = signed_in_client("retry-job")
    response = client.post(f"/jobs/{failed_job}/retry", data={"csrf": csrf})
    assert response.status_code == 302, response.status_code
    with appmod.db_connect() as connection:
        after = connection.execute(
            "SELECT state, attempts, error FROM jobs WHERE id=?", (failed_job,)
        ).fetchone()
    assert after["state"] == "queued" and after["attempts"] == 0 and after["error"] == ""
    print("finished_job_can_be_retried=passed")

    # Retrying needs a CSRF token, and work in progress is left alone.
    client, _csrf = signed_in_client("retry-no-csrf")
    assert client.post(f"/jobs/{failed_job}/retry", data={}).status_code == 400
    running_job = add_job("running")
    client, csrf = signed_in_client("retry-running")
    assert client.post(f"/jobs/{running_job}/retry", data={"csrf": csrf}).status_code == 302
    with appmod.db_connect() as connection:
        untouched = connection.execute(
            "SELECT state FROM jobs WHERE id=?", (running_job,)
        ).fetchone()
    assert untouched["state"] == "running", untouched["state"]
    assert client.post(f"/jobs/{uuid.uuid4().hex}/retry", data={"csrf": csrf}).status_code == 404
    print("retry_route_is_guarded=passed")

    # The status page offers the retry, and shows how many attempts have been made.
    finished_job = add_job("failed")
    page = client.get(f"/jobs/{finished_job}").data.decode()
    assert "Try this download again" in page
    assert "Attempts" in page and ">2<" in page
    # A download still working is not offered a retry.
    assert "Try this download again" not in client.get(f"/jobs/{running_job}").data.decode()
    print("status_page_offers_retry=passed")

    # Every retry setting saves, and impossible values are clamped rather than refused.
    stored = config.load()
    stored.update({
        "download_retries": 4, "download_retry_delay": 45, "download_gap_seconds": 6,
        "rate_limit_cooldown": 300, "job_retry_limit": 3,
    })
    config.save(stored)
    appmod.apply_settings(config.load())
    assert appmod.DOWNLOAD_RETRIES == 4
    assert appmod.DOWNLOAD_RETRY_DELAY == 45
    assert appmod.DOWNLOAD_GAP_SECONDS == 6
    assert appmod.RATE_LIMIT_COOLDOWN == 300
    assert appmod.JOB_RETRY_LIMIT == 3
    stored = config.load()
    stored.update({"download_retries": 999, "download_gap_seconds": -5})
    config.save(stored)
    loaded = config.load()
    assert loaded["download_retries"] == config.NUMERIC_LIMITS["download_retries"][1]
    assert loaded["download_gap_seconds"] == config.NUMERIC_LIMITS["download_gap_seconds"][0]
    print("retry_settings_saved_and_clamped=passed")

    # The preferences form carries every retry control.
    client, _csrf = signed_in_client("retry-preferences")
    preferences = client.get("/preferences").data.decode()
    for field in (
        "download_retries", "download_retry_delay", "download_gap_seconds",
        "rate_limit_cooldown", "job_retry_limit", "search_result_limit",
        "auto_update_tools",
    ):
        assert f'name="{field}"' in preferences, field
    print("preferences_expose_retry_settings=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("download_retries=passed")
