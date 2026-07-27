"""An artist or channel can be taken whole, or picked apart release by release."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app
from requestcast.youtube_collections import collection_tab_url


def signed_in_client(nonce: str):
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["nonce"] = nonce
    return client, hmac.new(app.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def job_payload(job_id: str) -> dict:
    with app.db_connect() as connection:
        row = connection.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    assert row is not None, "the job was not queued"
    return json.loads(row["payload"])


# A channel's own tabs are addressed without losing the channel handle.
assert collection_tab_url("https://www.youtube.com/@artist/videos", "releases") == (
    "https://www.youtube.com/@artist/releases"
)
assert collection_tab_url("https://www.youtube.com/@artist", "playlists") == (
    "https://www.youtube.com/@artist/playlists"
)
assert collection_tab_url("https://www.youtube.com/channel/UC123/videos", "playlists") == (
    "https://www.youtube.com/channel/UC123/playlists"
)
# An unknown tab falls back to the videos the channel published.
assert collection_tab_url("https://www.youtube.com/@artist", "../../etc") == (
    "https://www.youtube.com/@artist/videos"
)
print("channel_tab_url=passed")


DEEZER_ALBUMS = {
    "data": [
        {"id": "11", "title": "First Album", "release_date": "1999-04-01",
         "record_type": "album", "nb_tracks": 12},
        {"id": "12", "title": "A Single", "release_date": "2001-01-01", "record_type": "single"},
    ]
}
DEEZER_TOP = {
    "data": [
        {"id": "21", "title": "Best Known Song", "duration": 200,
         "artist": {"name": "Test Artist"}, "album": {"title": "First Album"}},
    ]
}


def fake_deezer_api(url: str, params=None):
    if "/albums" in url:
        return DEEZER_ALBUMS
    if "/top" in url:
        return DEEZER_TOP
    raise AssertionError(f"unexpected Deezer call: {url}")


artist = {"source": "deezer", "kind": "artist", "id": "7", "title": "Test Artist",
          "artist": "Test Artist", "cover": ""}
artist_token = app.signer.dumps(artist)

with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    sections = app.browse_sections(artist)

headings = [section["heading"] for section in sections]
assert headings == ["Releases", "Popular tracks"], headings
releases = sections[0]["items"]
assert [entry["label"] for entry in releases] == ["First Album", "A Single"], releases
assert "Album" in releases[0]["detail"] and "1999" in releases[0]["detail"], releases[0]
assert sections[1]["items"][0]["label"] == "Best Known Song"
print("deezer_artist_sections=passed")


# A YouTube channel lists its releases, playlists, and videos from three separate tabs.
CHANNEL_TABS = {
    "https://www.youtube.com/@band/releases": {
        "entries": [{"id": "OLAK5uy_ABCDEFGHIJ", "title": "Debut Album", "playlist_count": 9}]
    },
    "https://www.youtube.com/@band/playlists": {
        "entries": [{"id": "PLabcdefghij", "title": "Live Sessions"}]
    },
    "https://www.youtube.com/@band/videos": {
        "entries": [
            {"id": "vvvvvvvvvvv", "title": "First Song (Official Video)", "duration": 210},
            {"id": "wwwwwwwwwww", "title": "Second Song", "duration": 180},
        ]
    },
}
channel = {
    "source": "youtube", "kind": "channel", "id": "https://www.youtube.com/@band/videos",
    "title": "The Band", "artist": "The Band",
    "collection_url": "https://www.youtube.com/@band/videos",
}

with patch("requestcast.app.ytdlp_json", side_effect=lambda url, **kw: CHANNEL_TABS[url]):
    channel_sections = app.browse_sections(channel)

assert [section["heading"] for section in channel_sections] == [
    "Releases", "Playlists", "Videos"
], channel_sections
assert channel_sections[0]["items"][0]["label"] == "Debut Album"
assert "9 tracks" in channel_sections[0]["items"][0]["detail"]
assert channel_sections[1]["items"][0]["label"] == "Live Sessions"
video_labels = [entry["label"] for entry in channel_sections[2]["items"]]
# The "(Official Video)" suffix is not part of the song's name.
assert video_labels == ["First Song", "Second Song"], video_labels
first_video = app.signer.loads(channel_sections[2]["items"][0]["token"])
assert first_video["kind"] == "video" and first_video["video_id"] == "vvvvvvvvvvv", first_video
first_release = app.signer.loads(channel_sections[0]["items"][0]["token"])
assert first_release["kind"] == "playlist" and first_release["url_playlist"] is True, first_release
print("channel_sections=passed")


# A tab yt-dlp cannot read is skipped rather than failing the whole page.
def one_tab_fails(url, **kw):
    if url.endswith("/releases"):
        raise RuntimeError("that tab does not exist")
    return CHANNEL_TABS[url]


with patch("requestcast.app.ytdlp_json", side_effect=one_tab_fails):
    partial = app.browse_sections(channel)
assert [section["heading"] for section in partial] == ["Playlists", "Videos"], partial
print("channel_missing_tab=passed")


# Picking two items queues exactly those two, as album and song payloads.
client, csrf = signed_in_client("browse-selected")
chosen = [releases[0]["token"], sections[1]["items"][0]["token"]]
with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    response = client.post(
        "/browse",
        data={"csrf": csrf, "token": artist_token, "scope": "selected", "select": chosen},
    )
assert response.status_code == 302, response.status_code
payload = job_payload(response.headers["Location"].rsplit("/", 1)[-1])
assert payload["source"] == "selection", payload
assert [item["kind"] for item in payload["items"]] == ["album", "song"], payload["items"]
assert [item["id"] for item in payload["items"]] == ["11", "21"], payload["items"]
print("browse_selected_items=passed")


# "Download everything" takes every listed release and track without ticking anything.
client, csrf = signed_in_client("browse-everything")
with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    response = client.post(
        "/browse", data={"csrf": csrf, "token": artist_token, "scope": "everything"}
    )
assert response.status_code == 302, response.status_code
payload = job_payload(response.headers["Location"].rsplit("/", 1)[-1])
assert len(payload["items"]) == 3, payload["items"]
assert payload["title"] == "Test Artist", payload["title"]
assert [item["kind"] for item in payload["items"]] == ["album", "album", "song"], payload["items"]
print("browse_everything=passed")


# Ticking nothing is a mistake worth reporting rather than an empty job.
client, csrf = signed_in_client("browse-empty")
with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    response = client.post(
        "/browse", data={"csrf": csrf, "token": artist_token, "scope": "selected"}
    )
assert response.status_code == 302, response.status_code
assert "/browse" in response.headers["Location"], response.headers["Location"]
print("browse_empty_selection=passed")


# Only artists and channels can be browsed, and only with a signature we made.
song_token = app.signer.dumps({"source": "deezer", "kind": "song", "id": "1", "title": "One"})
client, csrf = signed_in_client("browse-song")
response = client.get(f"/browse?token={song_token}")
assert response.status_code == 302 and response.headers["Location"].endswith("/")
response = client.get("/browse?token=not-a-real-token")
assert response.status_code == 302, response.status_code
print("browse_rejects_other_results=passed")


# A forged item token cannot smuggle work into a selection job.
client, csrf = signed_in_client("browse-forged")
with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    response = client.post(
        "/browse",
        data={"csrf": csrf, "token": artist_token, "scope": "selected",
              "select": ["forged.token.value"]},
    )
assert response.status_code == 302 and "/browse" in response.headers["Location"]
print("browse_rejects_forged_tokens=passed")


# The worker turns a selection job into the tracks of each chosen release.
selection = {
    "source": "selection", "kind": "selection", "title": "Test Artist",
    "items": [
        {"source": "deezer", "kind": "album", "id": "11", "title": "First Album"},
        {"source": "youtube", "kind": "video", "id": "abcdefghijk",
         "video_id": "abcdefghijk", "title": "A Video", "artist": "Test Artist"},
    ],
}
with (
    patch("requestcast.app.expand_deezer", return_value=[{"artist": "Test Artist", "title": "One"}]),
    patch("requestcast.app.expand_youtube", return_value=[{"artist": "Test Artist", "title": "Two"}]),
    patch("requestcast.app.update_job"),
):
    tracks, errors = app.expand_selection(selection, "0" * 32)
assert [track["title"] for track in tracks] == ["One", "Two"], tracks
assert not errors, errors

# One release failing must not lose the rest of the selection.
with (
    patch("requestcast.app.expand_deezer", side_effect=RuntimeError("Deezer said no")),
    patch("requestcast.app.expand_youtube", return_value=[{"artist": "Test Artist", "title": "Two"}]),
    patch("requestcast.app.update_job"),
):
    tracks, errors = app.expand_selection(selection, "0" * 32)
assert [track["title"] for track in tracks] == ["Two"], tracks
assert errors and "Deezer said no" in errors[0], errors
print("expand_selection=passed")


# The page itself must render, with a label on every checkbox for screen reader use.
import re  # noqa: E402

client, _csrf = signed_in_client("browse-render")
with patch("requestcast.app.api_json", side_effect=fake_deezer_api):
    response = client.get(f"/browse?token={artist_token}")
assert response.status_code == 200, response.status_code
html = response.get_data(as_text=True)
for needle in (
    "<h1>Test Artist</h1>",
    "Download everything",
    "<legend>Releases (2)</legend>",
    "<legend>Popular tracks (1)</legend>",
    "First Album",
    "Best Known Song",
):
    assert needle in html, needle
identifiers = set(re.findall(r'id="(pick-[^"]+)"', html))
labelled = set(re.findall(r'<label for="(pick-[^"]+)"', html))
assert len(identifiers) == 3, identifiers
assert identifiers == labelled, (identifiers, labelled)
print("browse_page_renders=passed")
