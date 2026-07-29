"""Searches bring back as much as they are asked for, from every configured source."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch


for name in [key for key in os.environ if key.startswith(("REQUESTCAST_", "ADDTO_"))]:
    del os.environ[name]

WORKSPACE = Path(tempfile.mkdtemp(prefix="requestcast-search-"))
os.environ["REQUESTCAST_CONFIG"] = str(WORKSPACE / "requestcast.json")
os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
os.environ["REQUESTCAST_DOWNLOAD_DIR"] = str(WORKSPACE / "downloads")
os.environ["REQUESTCAST_STATE_DIR"] = str(WORKSPACE / "state")
os.environ["REQUESTCAST_SECRET_KEY"] = "search-limit-test-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requestcast import app as appmod  # noqa: E402
from requestcast import config  # noqa: E402


class FakeSongInfo(dict):
    """Enough of a musicdl SongInfo for the search formatter."""

    def __init__(self, index: int, source: str) -> None:
        super().__init__(
            identifier=f"{source}-{index}", song_name=f"Track {index}",
            singers="Test Artist", album="Test Album", duration_s=210,
            cover_url="", source=source, download_url=f"https://example.invalid/{index}.mp3",
            protocol="HTTP", ext=".mp3",
        )
        self.with_valid_download_url = True
        self.download_url = self["download_url"]

    def todict(self) -> dict:
        return dict(self)


def deezer_page(url: str, params: dict | None = None) -> dict:
    """A Deezer search endpoint that always has another page to give."""
    params = params or {}
    index = int(params.get("index", 0))
    size = int(params.get("limit", 25))
    rows = [
        {
            "id": index + number, "title": f"Song {index + number}",
            "artist": {"name": "Test Artist"}, "album": {"title": "Test Album"},
            "duration": 200, "track_position": 1, "disk_number": 1, "isrc": "",
        }
        for number in range(size)
    ]
    return {"data": rows, "next": "https://api.deezer.com/next"}


try:
    # A limit larger than one Deezer response is paged, not truncated.
    with patch.object(appmod, "api_json", side_effect=deezer_page) as paged:
        results = appmod.search_deezer_type("test", "song", 150)
    assert len(results) == 150, len(results)
    assert paged.call_count == 2, paged.call_count
    assert paged.call_args_list[0].args[1]["limit"] == 100
    assert paged.call_args_list[1].args[1]["index"] == 100
    print("deezer_search_pages_past_one_response=passed")

    # A short limit asks for exactly that many and stops after one call.
    with patch.object(appmod, "api_json", side_effect=deezer_page) as single:
        results = appmod.search_deezer_type("test", "song", 12)
    assert len(results) == 12, len(results)
    assert single.call_count == 1, single.call_count
    print("deezer_search_honours_small_limit=passed")

    # YouTube asks ytmusicapi for the requested number rather than a fixed 25.
    raw = [
        {"resultType": "song", "videoId": f"video{number:04d}", "title": f"Song {number}",
         "artists": [{"name": "Test Artist"}], "duration": "3:20", "duration_seconds": 200}
        for number in range(120)
    ]

    class FakeYTMusic:
        def search(self, query, filter=None, limit=None):  # noqa: A002
            FakeYTMusic.asked_for = limit
            return raw[:limit]

    with patch.object(appmod, "YTMusic", FakeYTMusic):
        results = appmod.search_youtube("test", "song", 100)
    assert FakeYTMusic.asked_for == 100, FakeYTMusic.asked_for
    assert len(results) == 100, len(results)
    print("youtube_search_uses_requested_limit=passed")

    # musicdl results are interleaved across platforms so one cannot fill the page.
    found = {
        "NeteaseMusicClient": [FakeSongInfo(number, "NeteaseMusicClient") for number in range(20)],
        "QQMusicClient": [FakeSongInfo(number, "QQMusicClient") for number in range(20)],
    }
    with (
        patch.object(appmod, "MUSICDL_ENABLED", True),
        patch.object(appmod.musicdl_source, "availability", return_value=""),
        patch.object(appmod.musicdl_source, "search", return_value=found),
    ):
        results = appmod.search_musicdl("test", "all", 6)
    assert len(results) == 6, len(results)
    sources_seen = [item["client"] for item in results]
    assert sources_seen.count("NeteaseMusicClient") == 3, sources_seen
    assert sources_seen.count("QQMusicClient") == 3, sources_seen
    assert all(item["source"] == "musicdl" and item["token"] for item in results)
    print("musicdl_search_interleaves_platforms=passed")

    # musicdl only supplies tracks, so other result types skip it entirely.
    with patch.object(appmod, "MUSICDL_ENABLED", True):
        assert appmod.search_musicdl("test", "album", 10) == []
    with patch.object(appmod, "MUSICDL_ENABLED", False):
        assert appmod.search_musicdl("test", "song", 10) == []
    print("musicdl_search_skipped_when_not_applicable=passed")

    # The requested size is clamped, never trusted.
    assert appmod.requested_search_limit("100") == 100
    assert appmod.requested_search_limit("99999") == config.NUMERIC_LIMITS["search_result_limit"][1]
    assert appmod.requested_search_limit("0") == config.NUMERIC_LIMITS["search_result_limit"][0]
    assert appmod.requested_search_limit("nonsense") == appmod.SEARCH_RESULT_LIMIT
    assert appmod.requested_search_limit("") == appmod.SEARCH_RESULT_LIMIT
    print("search_limit_is_clamped=passed")

    # The search page offers the size control and every source it can use.
    client = appmod.app.test_client()
    with patch.object(appmod, "MUSICDL_ENABLED", True):
        page = client.get("/").data.decode()
    assert 'name="limit"' in page
    assert "musicdl" in page
    assert 'value="200"' in page
    with patch.object(appmod, "MUSICDL_ENABLED", False):
        without_musicdl = client.get("/").data.decode()
    assert "musicdl only" not in without_musicdl
    print("search_form_offers_size_and_sources=passed")

    # A search asks each source for the size the page requested.
    with (
        patch.object(appmod, "search_youtube", return_value=[]) as youtube,
        patch.object(appmod, "search_deezer", return_value=[]) as deezer,
    ):
        client.get("/?q=test&limit=75&source=both")
    assert youtube.call_args.args[2] == 75, youtube.call_args
    assert deezer.call_args.args[2] == 75, deezer.call_args
    print("search_page_passes_limit_through=passed")

    # Saved settings drive the defaults, including whether musicdl joins a search.
    stored = config.load()
    stored.update({"search_result_limit": 125, "search_musicdl": True, "musicdl_enabled": True})
    config.save(stored)
    appmod.apply_settings(config.load())
    assert appmod.SEARCH_RESULT_LIMIT == 125
    assert appmod.SEARCH_MUSICDL is True
    with (
        patch.object(appmod, "search_youtube", return_value=[]) as youtube,
        patch.object(appmod, "search_deezer", return_value=[]),
        patch.object(appmod, "search_musicdl", return_value=[]) as musicdl,
    ):
        client.get("/?q=test")
    assert youtube.call_args.args[2] == 125, youtube.call_args
    musicdl.assert_called_once()
    print("saved_search_preferences_apply=passed")

    # An out-of-range stored limit is clamped when settings load.
    stored = config.load()
    stored["search_result_limit"] = 100000
    config.save(stored)
    assert config.load()["search_result_limit"] == config.NUMERIC_LIMITS["search_result_limit"][1]
    print("stored_search_limit_is_clamped=passed")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print("search_limits=passed")
