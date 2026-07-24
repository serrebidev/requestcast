from io import BytesIO
import hashlib
import hmac
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


with app.app.test_client() as client:
    nonce = "upload-route-test"
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    csrf = hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/import",
        data={
            "csrf": csrf,
            "file": (BytesIO(b"ABBA - Dancing Queen\nABBA - Dancing Queen\n"), "songs.txt"),
        },
        content_type="multipart/form-data",
    )

assert response.status_code == 302
location = response.headers["Location"]
assert "/jobs/" in location
job_id = location.rsplit("/", 1)[-1]
with app.db_connect() as connection:
    job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
assert job is not None
assert job["state"] == "queued"
assert "1 indexed entries" in job["label"]
assert "every track per artist" in job["label"], job["label"]
assert '"source": "import"' in job["payload"]
assert '"artist_limit": 0' in job["payload"], job["payload"][:200]
print("file_upload_route=passed")


# An explicit cap from the form must survive into the job payload.
with app.app.test_client() as client:
    nonce = "upload-route-cap-test"
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    csrf = hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/import",
        data={
            "csrf": csrf,
            "artist_tracks": "25",
            "file": (BytesIO(b"ABBA\n"), "artists.txt"),
        },
        content_type="multipart/form-data",
    )

job_id = response.headers["Location"].rsplit("/", 1)[-1]
with app.db_connect() as connection:
    job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
assert '"artist_limit": 25' in job["payload"], job["payload"][:200]
assert "top 25 tracks per artist" in job["label"], job["label"]

# An unknown value must fall back to the default rather than being trusted.
with app.app.test_client() as client:
    nonce = "upload-route-bad-cap"
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    csrf = hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/import",
        data={
            "csrf": csrf,
            "artist_tracks": "99999",
            "file": (BytesIO(b"ABBA\n"), "artists.txt"),
        },
        content_type="multipart/form-data",
    )

job_id = response.headers["Location"].rsplit("/", 1)[-1]
with app.db_connect() as connection:
    job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
assert '"artist_limit": 0' in job["payload"], job["payload"][:200]
print("file_upload_route_artist_cap=passed")
