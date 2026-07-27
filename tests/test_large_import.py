"""Whole-library lists must be accepted, not turned away by a small row limit."""

from __future__ import annotations

import hashlib
import hmac
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Configure before importing the app, so this runs on its own as well as inside the suite.
# Windows keeps the sqlite handle open until the process exits, so cleanup is best effort.
_temporary = tempfile.TemporaryDirectory(
    prefix="requestcast-large-import-", ignore_cleanup_errors=True
)
_root = Path(_temporary.name)
os.environ.setdefault("REQUESTCAST_CONFIG", str(_root / "config.json"))
os.environ.setdefault("REQUESTCAST_DOWNLOAD_DIR", str(_root / "downloads"))
os.environ.setdefault("REQUESTCAST_STATE_DIR", str(_root / "state"))
os.environ.setdefault("REQUESTCAST_SECRET_KEY", "large-import-test-secret-key")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"

from requestcast import app


# The ceilings are deliberately large; a list of a whole music library is normal.
assert app.MAX_IMPORT_ENTRIES >= 250_000, app.MAX_IMPORT_ENTRIES
assert app.MAX_IMPORT_TRACKS >= app.MAX_IMPORT_ENTRIES, app.MAX_IMPORT_TRACKS
assert app.MAX_UPLOAD_BYTES >= 64 * 1024 * 1024, app.MAX_UPLOAD_BYTES
assert app.app.config["MAX_CONTENT_LENGTH"] == app.MAX_UPLOAD_BYTES
assert app.MAX_PDF_PAGES >= 5_000, app.MAX_PDF_PAGES
print("import_limits=passed")


# A file well past the old 10,000 row limit is indexed, and quickly.
ROWS = 120_000
listing = "".join(f"Artist {index} - Song Number {index}\n" for index in range(ROWS)).encode()
started = time.perf_counter()
entries = app.parse_txt_import(BytesIO(listing))
elapsed = time.perf_counter() - started
assert len(entries) == ROWS, len(entries)
assert elapsed < 30.0, f"indexing {ROWS:,} rows took {elapsed:.1f}s"
print(f"large_txt_indexed={ROWS} rows in {elapsed:.2f}s")


# The same file goes through the upload route and reaches the queue intact.
with app.app.test_client() as client:
    nonce = "large-import"
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    csrf = hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/import",
        data={"csrf": csrf, "file": (BytesIO(listing), "library.txt")},
        content_type="multipart/form-data",
    )

assert response.status_code == 302, response.status_code
job_id = response.headers["Location"].rsplit("/", 1)[-1]
with app.db_connect() as connection:
    job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
assert job is not None
assert f"{ROWS} indexed entries" in job["label"], job["label"]
print("large_upload_route=passed")


# Listing recent downloads must not read those payloads back.
started = time.perf_counter()
jobs = app.recent_jobs()
elapsed = time.perf_counter() - started
assert jobs and jobs[0]["id"] == job_id
assert "payload" not in jobs[0].keys(), jobs[0].keys()
assert elapsed < 1.0, f"listing recent downloads took {elapsed:.2f}s"
print(f"recent_jobs_skips_payloads={elapsed:.3f}s")

with app.db_connect() as connection:
    connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))


# A file beyond the ceiling still fails, and fails before it is all read into memory.
too_many = "".join(
    f"Artist {index} - Song {index}\n" for index in range(app.MAX_RAW_IMPORT_ENTRIES + 5_000)
).encode()
try:
    app.parse_txt_import(BytesIO(too_many))
except RuntimeError as error:
    assert "250,000" in str(error), error
else:
    raise AssertionError("A file past the entry ceiling must be refused.")
print("oversized_file_refused=passed")

_temporary.cleanup()
