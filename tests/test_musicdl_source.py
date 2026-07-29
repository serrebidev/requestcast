import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app, deezer, musicdl_source


# URL host mapping: every musicdl platform resolves to its client, the more
# specific suffix wins, and unrelated sites stay unsupported.
assert musicdl_source.client_name_for_url("https://music.163.com/#/playlist?id=1") == "NeteaseMusicClient"
assert musicdl_source.client_name_for_url("https://5sing.kugou.com/x/dj/abc.html") == "FiveSingMusicClient"
assert musicdl_source.client_name_for_url("https://www.kugou.com/yy/special/single/1.html") == "KugouMusicClient"
assert musicdl_source.client_name_for_url("https://h5app.kuwo.cn/m/bodian/collection.html?uid=1") == "BodianMusicClient"
assert musicdl_source.client_name_for_url("https://www.kuwo.cn/playlist_detail/1") == "KuwoMusicClient"
assert musicdl_source.client_name_for_url("https://open.spotify.com/playlist/37i9dQZF1E8NWHOpySOxQd") == "SpotifyMusicClient"
assert musicdl_source.client_name_for_url("soundcloud.com/pandadub/sets/the-lost-ship") == "SoundCloudMusicClient"
assert musicdl_source.client_name_for_url("https://tidal.com/playlist/a94e7dce") == "TIDALMusicClient"
assert musicdl_source.client_name_for_url("https://example.com/track/1") is None
assert musicdl_source.client_name_for_url("not a url at all") is None
print("host_client_mapping=passed")


# musicdl's logger needs a writable XDG home; prepare_environment supplies one.
with patch.dict(os.environ, {}, clear=False):
    for name in ("XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        os.environ.pop(name, None)
    musicdl_source.prepare_environment("/tmp/requestcast-xdg")
    assert os.environ["XDG_STATE_HOME"] == str(Path("/tmp/requestcast-xdg/musicdl/state"))
    # An admin-provided XDG setting always wins.
    os.environ["XDG_CACHE_HOME"] = "/custom/cache"
    musicdl_source.prepare_environment("/tmp/requestcast-xdg-other")
    assert os.environ["XDG_CACHE_HOME"] == "/custom/cache"
print("xdg_environment=passed")


# Song payloads must survive JSON so they can live inside the signed result token.
from musicdl.modules.utils import SongInfo

song = SongInfo(
    source="NeteaseMusicClient", song_name="Dancing Queen", singers="ABBA",
    album="Arrival", ext="mp3", duration_s=231, identifier="123",
    download_url="https://cdn.test/song.mp3", download_url_status={"ok": True},
    cover_url="https://cdn.test/cover.jpg",
)
payload = musicdl_source.song_info_to_payload(song)
import json

restored = SongInfo.fromdict(json.loads(json.dumps(payload)))
assert restored.song_name == "Dancing Queen"
assert restored.with_valid_download_url
print("song_payload_roundtrip=passed")

# Streaming-only tracks cannot be stored and are reported instead of crashing.
hls = SongInfo(source="X", song_name="s", download_url="https://cdn.test/a.m3u8", protocol="HLS")
for bad in (hls, SongInfo(source="X", song_name="s", download_url={"stream": 1})):
    try:
        musicdl_source.song_info_to_payload(bad)
        raise SystemExit("a non-HTTP track must not produce a payload")
    except musicdl_source.MusicdlError:
        pass
print("song_payload_rejection=passed")


class FakeDeezer:
    def __init__(self):
        self.calls = []

    def download(self, track_id, destination_dir):
        self.calls.append(track_id)
        raise deezer.DeezerError("stream refused")


def fake_musicdl_file(temp_dir, name="musicdl-hit.flac", data=b"musicdl-audio"):
    path = Path(temp_dir) / name
    path.write_bytes(data)
    return path


def run_download(track, deezer_client, musicdl_enabled=True, musicdl_side_effect=None, ytdlp_side_effect=None):
    temp_root = tempfile.TemporaryDirectory()
    root = Path(temp_root.name)
    state_dir = root / "state"
    state_dir.mkdir()
    patches = [
        patch.object(app, "DEEZER", deezer_client),
        patch.object(app, "MUSICDL_ENABLED", musicdl_enabled),
        patch.object(app, "MUSICDL_SOURCES", ["NeteaseMusicClient"]),
        patch.object(app, "AZURACAST_ENABLED", False),
        patch.object(app, "STATE_DIR", state_dir),
        patch.object(app, "DOWNLOAD_DIR", root / "downloads"),
        patch.object(app, "tag_audio", lambda path, item: None),
        patch.object(app, "probe_audio", lambda path: {"codec_name": "flac"}),
        patch.object(app, "find_deezer_song", return_value={"id": 42}),
    ]
    if musicdl_side_effect is not None:
        patches.append(patch.object(app.musicdl_source, "search_and_download", side_effect=musicdl_side_effect))
    if ytdlp_side_effect is not None:
        # The CI runner has no yt-dlp on its PATH, so the path check is patched too.
        patches.append(patch.object(app, "YTDLP", "yt-dlp"))
        patches.append(patch.object(app.subprocess, "run", side_effect=ytdlp_side_effect))
    for patcher in patches:
        patcher.start()
    try:
        result = app.download_one(dict(track))
    finally:
        for patcher in reversed(patches):
            patcher.stop()
    return (temp_root, *result)


# Deezer failing hands the track to musicdl, and yt-dlp never runs.
fake = FakeDeezer()


def musicdl_delivers(artist, title, duration_s, sources, temp_dir, match_key):
    assert artist == "ABBA" and title == "Dancing Queen"
    assert sources == ["NeteaseMusicClient"]
    return fake_musicdl_file(temp_dir)


temp_root, status, path, media = run_download(
    {"source": "youtube", "source_id": "abc123XYZ00", "video_id": "abc123XYZ00",
     "artist": "ABBA", "title": "Dancing Queen"},
    fake,
    musicdl_side_effect=musicdl_delivers,
    ytdlp_side_effect=AssertionError("yt-dlp must not run when musicdl delivers"),
)
assert fake.calls == ["42"], fake.calls
assert status == "downloaded"
assert path.suffix == ".flac", path
assert path.read_bytes() == b"musicdl-audio"
temp_root.cleanup()
print("download_falls_back_to_musicdl=passed")


# When musicdl cannot supply the track either, the YouTube/yt-dlp path still runs.
class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_ytdlp(command, **kwargs):
    output_dir = Path(command[command.index("-o") + 1]).parent
    (output_dir / "abc123XYZ00.mp3").write_bytes(b"youtube-audio")
    return Completed()


def musicdl_fails(*args, **kwargs):
    raise musicdl_source.MusicdlError("no match")


temp_root, status, path, media = run_download(
    {"source": "youtube", "source_id": "abc123XYZ00", "video_id": "abc123XYZ00",
     "artist": "Test Artist", "title": "Test Track"},
    FakeDeezer(),
    musicdl_side_effect=musicdl_fails,
    ytdlp_side_effect=fake_ytdlp,
)
assert status == "downloaded"
assert path.suffix == ".mp3"
assert path.read_bytes() == b"youtube-audio"
temp_root.cleanup()
print("musicdl_failure_keeps_youtube=passed")


# With musicdl disabled the search is never attempted and yt-dlp runs directly.
def musicdl_must_not_run(*args, **kwargs):
    raise AssertionError("musicdl must not run while disabled")


temp_root, status, path, media = run_download(
    {"source": "youtube", "source_id": "abc123XYZ00", "video_id": "abc123XYZ00",
     "artist": "Test Artist", "title": "Test Track"},
    None,
    musicdl_enabled=False,
    musicdl_side_effect=musicdl_must_not_run,
    ytdlp_side_effect=fake_ytdlp,
)
assert status == "downloaded" and path.suffix == ".mp3"
temp_root.cleanup()
print("musicdl_disabled_goes_to_youtube=passed")


# A musicdl URL result downloads through its stored payload, never through Deezer.
class DeezerMustNotRun:
    def download(self, track_id, destination_dir):
        raise AssertionError("Deezer must not handle a musicdl track")


def payload_delivers(payload, temp_dir):
    assert payload["identifier"] == "123"
    return fake_musicdl_file(temp_dir, name="stored-hit.mp3", data=b"stored-audio")


temp_root_holder = tempfile.TemporaryDirectory()
root = Path(temp_root_holder.name)
state_dir = root / "state"
state_dir.mkdir()
track = app.musicdl_track_entry(song)
with (
    patch.object(app, "DEEZER", DeezerMustNotRun()),
    patch.object(app, "MUSICDL_ENABLED", True),
    patch.object(app, "AZURACAST_ENABLED", False),
    patch.object(app, "STATE_DIR", state_dir),
    patch.object(app, "DOWNLOAD_DIR", root / "downloads"),
    patch.object(app, "tag_audio", lambda path, item: None),
    patch.object(app, "probe_audio", lambda path: {"codec_name": "mp3"}),
    patch.object(app.musicdl_source, "download_payload", side_effect=payload_delivers),
):
    status, path, media = app.download_one(dict(track))
assert status == "downloaded"
assert path.read_bytes() == b"stored-audio"
assert "[musicdl-123]" in path.name, path.name
temp_root_holder.cleanup()
print("musicdl_track_uses_stored_payload=passed")


# The track entry mirrors the shape other sources produce.
assert track["source"] == "musicdl" and track["kind"] == "song"
assert track["title"] == "Dancing Queen" and track["artist"] == "ABBA"
assert track["album"] == "Arrival" and track["duration_seconds"] == 231
assert track["video_id"] == "" and track["source_id"] == "123"
print("musicdl_track_entry=passed")


# resolve_media_url routes musicdl platforms and still rejects unknown sites.
with (
    patch.object(app, "MUSICDL_ENABLED", True),
    patch.object(app.musicdl_source, "parse_url", return_value=[song]),
):
    result = app.resolve_media_url("https://music.163.com/song?id=123")
assert result["source"] == "musicdl" and result["kind"] == "song"
assert result["title"] == "Dancing Queen"
loaded_single = app.signer.loads(result["token"])
assert loaded_single["musicdl"]["identifier"] == "123"

with (
    patch.object(app, "MUSICDL_ENABLED", True),
    patch.object(app.musicdl_source, "parse_url", return_value=[song, song]),
):
    result = app.resolve_media_url("https://music.163.com/playlist?id=1")
assert result["kind"] == "playlist"
assert "2 tracks" in result["detail"]
loaded = app.signer.loads(result["token"])
assert loaded["url"] == "https://music.163.com/playlist?id=1"
assert loaded["client"] == "NeteaseMusicClient"

try:
    app.resolve_media_url("https://example.com/track/1")
    raise SystemExit("an unsupported site must be rejected")
except RuntimeError as exc:
    assert "not supported" in str(exc)
print("resolve_media_url_musicdl=passed")


# Disabled musicdl makes platform URLs fail with a clear message.
with patch.object(app, "MUSICDL_ENABLED", False):
    try:
        app.resolve_media_url("https://music.163.com/song?id=123")
        raise SystemExit("a musicdl URL must fail while musicdl is disabled")
    except RuntimeError as exc:
        assert "turned off" in str(exc)
print("resolve_media_url_disabled=passed")


# Expansion: a single track comes straight from the payload; a collection re-parses.
expanded = app.expand_musicdl(loaded_single)
assert len(expanded) == 1 and expanded[0]["title"] == "Dancing Queen"
with patch.object(app.musicdl_source, "parse_url", return_value=[song]) as parse:
    expanded = app.expand_musicdl({"kind": "playlist", "url": "https://music.163.com/playlist?id=1", "client": "NeteaseMusicClient"})
assert parse.call_count == 1
assert len(expanded) == 1 and expanded[0]["source"] == "musicdl"
print("expand_musicdl=passed")


# Single-track pages (a SoundCloud song, not a set) are read and searched.
SOUNDCLOUD_PAGE = """
<html><head>
<title>Stream Somebody by Kurokatu | Listen online for free on SoundCloud</title>
<meta property="og:title" content="Somebody">
<meta property="og:description" content="Listen to Somebody by Kurokatu #np on #SoundCloud">
<meta name="twitter:description" content="Listen to Somebody by Kurokatu #np on #SoundCloud">
<meta name="description" content="Stream Somebody by Kurokatu on desktop and mobile. Play over 320 million tracks for free on SoundCloud.">
</head><body></body></html>
"""
assert musicdl_source.artist_title_from_page(SOUNDCLOUD_PAGE) == ("Kurokatu", "Somebody")
assert musicdl_source.artist_title_from_page("<html><head></head></html>") == ("", "")
print("artist_title_from_page=passed")


class FakePageResponse:
    text = SOUNDCLOUD_PAGE

    def raise_for_status(self):
        pass


class FakeSearchClient:
    def __init__(self, results):
        self.results = results
        self.keywords = []

    def search(self, keyword):
        self.keywords.append(keyword)
        return self.results


soundcloud_song = SongInfo(
    source="SoundCloudMusicClient", song_name="Somebody", singers="Kurokatu",
    ext="mp3", duration_s=200, identifier="sc-1", protocol="HLS",
    download_url="https://cf-media.sndcdn.com/somebody.m3u8", download_url_status={"ok": True},
)
fake_client = FakeSearchClient({"SoundCloudMusicClient": [soundcloud_song]})
with (
    patch.object(musicdl_source.requests, "get", return_value=FakePageResponse()),
    patch.object(musicdl_source, "get_music_client", return_value=fake_client),
):
    found = musicdl_source._track_from_page(
        "https://soundcloud.com/kuro-katu/somebody", "SoundCloudMusicClient", "/tmp", app.match_key
    )
assert fake_client.keywords == ["Kurokatu Somebody"]
assert len(found) == 1 and found[0].song_name == "Somebody"
print("track_from_page=passed")


# HLS tracks cannot store a payload, so the entry keeps the platform for a later search.
hls_entry = app.musicdl_track_entry(soundcloud_song)
assert "musicdl" not in hls_entry
assert hls_entry["client"] == "SoundCloudMusicClient"
assert hls_entry["title"] == "Somebody" and hls_entry["artist"] == "Kurokatu"
print("hls_entry_keeps_client=passed")


# Without a stored payload the download searches the track's own platform.
def search_records_sources(artist, title, duration_s, sources, temp_dir, match_key):
    assert sources == ["SoundCloudMusicClient"], sources
    return fake_musicdl_file(temp_dir, name="sc-hit.mp3", data=b"sc-audio")


with (
    patch.object(app, "MUSICDL_ENABLED", True),
    patch.object(app, "MUSICDL_SOURCES", ["NeteaseMusicClient"]),
    patch.object(app.musicdl_source, "search_and_download", side_effect=search_records_sources),
):
    downloaded = app.download_via_musicdl(dict(hls_entry), Path(tempfile.mkdtemp()))
assert downloaded.read_bytes() == b"sc-audio"
print("download_via_musicdl_prefers_track_platform=passed")
