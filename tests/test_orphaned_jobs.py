"""A job that loses its worker is taken back instead of sitting in 'running'.

A download that outlives its thread, a worker killed mid-run, or an exception path
that never reaches ``fail_or_requeue`` all leave the job marked 'running' with
nobody left to finish it. Nothing owns the job after that, so it never downloads,
never gets requested, and its status page spins for the life of the service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid


for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-orphans-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
os.environ["REQUESTCAST_DOWNLOAD_DIR"] = str(WORKSPACE / "downloads")
os.environ["REQUESTCAST_STATE_DIR"] = str(WORKSPACE / "state")
os.environ["REQUESTCAST_SECRET_KEY"] = "orphaned-job-test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import app as appmod  # noqa: E402


def add_job(state: str, attempts: int) -> str:
    job_id = uuid.uuid4().hex
    now = int(time.time())
    with appmod.db_connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id,state,label,detail,payload,created_at,updated_at,attempts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                job_id, state, "Test job", "",
                json.dumps({"source": "youtube"}), now, now, attempts,
            ),
        )
    return job_id


def job(job_id: str) -> dict:
    with appmod.db_connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else {}


try:
    assert appmod.JOB_RETRY_LIMIT >= 1, appmod.JOB_RETRY_LIMIT

    # An idle worker with nothing left in 'running' has nothing to take back.
    assert appmod.reclaim_orphaned_job() is False
    print("idle_worker_reclaims_nothing=passed")

    # Jobs that are queued, finished, or already failed are left exactly where they are.
    queued = add_job("queued", 0)
    finished = add_job("completed", 1)
    dead = add_job("failed", 3)
    assert appmod.reclaim_orphaned_job() is False
    assert job(queued)["state"] == "queued"
    assert job(finished)["state"] == "completed"
    assert job(dead)["state"] == "failed"
    print("only_running_jobs_are_reclaimed=passed")

    # The shape of the live incident: claimed once, then abandoned by its worker.
    stuck = add_job("running", 1)
    assert appmod.reclaim_orphaned_job() is True
    row = job(stuck)
    assert row["state"] == "queued", row
    assert row["attempts"] == 1, row  # the next claim is what spends the retry
    assert "interrupted" in row["detail"], row
    print("abandoned_job_is_queued_again=passed")

    # A job that keeps outliving its worker runs out of retries and is recorded as
    # failed, so it reads as broken rather than spinning forever.
    stubborn = add_job("running", appmod.JOB_RETRY_LIMIT + 1)
    assert appmod.reclaim_orphaned_job() is True
    row = job(stubborn)
    assert row["state"] == "failed", row
    assert "interrupted" in row["error"], row
    print("unrecoverable_job_fails_clearly=passed")

    # A backlog of orphans drains completely, one per pass of the worker loop.
    first = add_job("running", 1)
    second = add_job("running", 1)
    assert appmod.reclaim_orphaned_job() is True
    assert appmod.reclaim_orphaned_job() is True
    assert job(first)["state"] == "queued", job(first)
    assert job(second)["state"] == "queued", job(second)
    assert appmod.reclaim_orphaned_job() is False
    print("orphan_backlog_drains=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("orphaned_jobs=passed")
