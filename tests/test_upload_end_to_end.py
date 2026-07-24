"""Upload a small list through the live portal and confirm it lands in the Requests library."""

from io import BytesIO
import os
import re
import time

import requests


BASE = os.environ.get("REQUESTCAST_BASE_URL", "http://127.0.0.1:8797").rstrip("/")
PASSWORD = os.environ.get("REQUESTCAST_PASSWORD", "")
LIST = b"ABBA - Dancing Queen\nABBA - Dancing Queen\n"

client = requests.Session()
if PASSWORD:
    client.post(BASE + "/login", data={"password": PASSWORD}, timeout=30).raise_for_status()
home = client.get(BASE + "/", timeout=30)
home.raise_for_status()

csrf = re.search(r'name="csrf" value="([0-9a-f]{64})"', home.text)
assert csrf, "CSRF token was not present on the search page"

uploaded = client.post(
    BASE + "/import",
    data={"csrf": csrf.group(1)},
    files={"file": ("smoke-list.txt", BytesIO(LIST), "text/plain")},
    timeout=300,
)
uploaded.raise_for_status()
job_url = uploaded.url
assert "/jobs/" in job_url, f"Upload did not redirect to a job: {job_url}"

page = client.get(job_url, timeout=30)
assert "1 indexed entries" in page.text, "The duplicate line should have been de-duplicated"

state = ""
detail = ""
for _ in range(60):
    page = client.get(job_url, timeout=30)
    page.raise_for_status()
    match = re.search(r"<dt>Status</dt><dd>([^<]+)</dd>", page.text)
    state = match.group(1).strip() if match else "unknown"
    detail_match = re.search(r"<dt>Detail</dt><dd>([^<]*)</dd>", page.text)
    detail = detail_match.group(1).strip() if detail_match else ""
    if state in {"completed", "failed"}:
        break
    time.sleep(5)

assert state == "completed", f"Import job ended in state {state}: {job_url}"

api_base = os.environ.get("REQUESTCAST_AZURACAST_API_BASE", "http://127.0.0.1:12000/api")
headers = {"X-API-Key": os.environ["REQUESTCAST_AZURACAST_API_KEY"]}
listing = requests.get(
    f"{api_base}/station/1/files",
    headers=headers,
    params={"searchPhrase": "Dancing Queen", "rowCount": 50},
    timeout=60,
)
listing.raise_for_status()
rows = listing.json().get("rows", [])
matched = [
    row for row in rows
    if str(row.get("path") or "").startswith("Requests/")
    and "dancing queen" in str(row.get("title") or "").casefold()
]
assert matched, f"The imported track is not in the Requests folder (searched {len(rows)} rows)"
playlists = [str(item.get("id")) for item in (matched[0].get("playlists") or [])]
request_playlist = os.environ.get("REQUESTCAST_REQUEST_PLAYLIST_ID", "10")
assert request_playlist in playlists, f"Track is not on the request playlist: {playlists}"

print(f"job_url={job_url}")
print(f"job_state={state}")
print(f"job_detail={detail}")
print(f"requests_folder_match={matched[0].get('path')}")
print(f"track_metadata={matched[0].get('artist')} / {matched[0].get('title')} / {matched[0].get('album')}")
print("upload_end_to_end=passed")
