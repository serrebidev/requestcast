import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app, deezer
from requestcast import config


# The Blowfish key derivation and chunk cipher must round-trip a stream the way the
# Deezer CDN encrypts it: every third 2048-byte chunk, CBC with a fixed IV per chunk.
key = deezer.DeezerClient.blowfish_key("3135556")
assert len(key) == 16
from Crypto.Cipher import Blowfish

plain = bytes((index * 37) % 256 for index in range(deezer.CHUNK_SIZE * 7 + 100))
encrypted = bytearray()
for index in range(0, len(plain), deezer.CHUNK_SIZE):
    chunk = plain[index:index + deezer.CHUNK_SIZE]
    number = index // deezer.CHUNK_SIZE
    if number % 3 == 0 and len(chunk) == deezer.CHUNK_SIZE:
        chunk = Blowfish.new(key, Blowfish.MODE_CBC, deezer.BLOWFISH_IV).encrypt(chunk)
    encrypted.extend(chunk)

import io

output = io.BytesIO()
deezer.DeezerClient.decrypt_stream("3135556", io.BytesIO(bytes(encrypted)), output)
assert output.getvalue() == plain, "the decrypted stream must match the original audio"
print("blowfish_roundtrip=passed")


# The media endpoint is queried in lossless-to-lossy order and stops at the first
# quality the subscriber account is allowed to download.
client = object.__new__(deezer.DeezerClient)
client._license_token = "test-license"
qualities = []


def fake_media_response(url, body, params=None):
    quality = body["media"][0]["formats"][0]["format"]
    qualities.append(quality)
    if quality == "MP3_320":
        return {"data": [{"media": [{"format": quality, "sources": [{"url": "https://cdn.test/audio"}]}]}]}
    return {"data": [{"media": []}]}


client._post_json = fake_media_response
url, quality = client._stream_url("track-token")
assert url == "https://cdn.test/audio"
assert quality == "MP3_320"
assert qualities == ["FLAC", "MP3_320"]
print("quality_fallback_order=passed")


# The modern environment name wins, while the old ADDTO name remains supported.
with patch.dict(os.environ, {"ADDTO_DEEZER_ARL": "legacy-arl"}, clear=False):
    os.environ.pop("REQUESTCAST_DEEZER_ARL", None)
    assert config.load()["deezer_arl"] == "legacy-arl"
with patch.dict(
    os.environ,
    {"REQUESTCAST_DEEZER_ARL": "modern-arl", "ADDTO_DEEZER_ARL": "legacy-arl"},
    clear=False,
):
    assert config.load()["deezer_arl"] == "modern-arl"
print("deezer_environment_config=passed")


# Track ID resolution: a Deezer result carries its ID; anything else is looked up.
assert app.deezer_track_id({"source": "deezer", "source_id": "3135556"}) == "3135556"
with patch.object(app, "find_deezer_song", return_value={"id": 42}):
    assert app.deezer_track_id({"source": "youtube", "artist": "ABBA", "title": "Dancing Queen"}) == "42"
with patch.object(app, "find_deezer_song", return_value=None):
    assert app.deezer_track_id({"source": "youtube", "artist": "X", "title": "Y"}) == ""
print("deezer_track_id=passed")


class FakeDeezer:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def download(self, track_id, destination_dir):
        self.calls.append(track_id)
        if self.fail:
            raise deezer.DeezerError("stream refused")
        path = Path(destination_dir) / f"deezer-{track_id}.flac"
        path.write_bytes(b"fLaC-fake-audio")
        return path, "FLAC"


def run_download(track, fake, ytdlp_side_effect=None, found_song=None):
    temp_root = tempfile.TemporaryDirectory()
    root = Path(temp_root.name)
    state_dir = root / "state"
    state_dir.mkdir()
    download_dir = root / "downloads"
    patches = [
        patch.object(app, "DEEZER", fake),
        patch.object(app, "AZURACAST_ENABLED", False),
        patch.object(app, "STATE_DIR", state_dir),
        patch.object(app, "DOWNLOAD_DIR", download_dir),
        patch.object(app, "tag_audio", lambda path, item: None),
        patch.object(app, "probe_audio", lambda path: {"codec_name": "mp3"}),
        patch.object(app, "find_deezer_song", return_value=found_song),
    ]
    if ytdlp_side_effect is not None:
        patches.append(patch.object(app.subprocess, "run", side_effect=ytdlp_side_effect))
    for patcher in patches:
        patcher.start()
    try:
        status, path, media = app.download_one(dict(track))
    finally:
        for patcher in reversed(patches):
            patcher.stop()
    return temp_root, status, path, media


# With a Deezer session the audio comes from Deezer, and yt-dlp is never involved.
fake = FakeDeezer()
temp_root, status, path, media = run_download(
    {"source": "deezer", "source_id": "3135556", "artist": "ABBA", "title": "Dancing Queen"},
    fake,
    ytdlp_side_effect=AssertionError("yt-dlp must not run when Deezer delivers"),
)
assert fake.calls == ["3135556"], fake.calls
assert status == "downloaded"
assert path.suffix == ".flac", path
assert path.read_bytes() == b"fLaC-fake-audio"
assert path.parent == Path(temp_root.name) / "downloads"
temp_root.cleanup()
print("download_prefers_deezer=passed")


# When Deezer cannot supply the track, the YouTube/yt-dlp path takes over.
class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_ytdlp(command, **kwargs):
    output_dir = Path(command[command.index("-o") + 1]).parent
    (output_dir / "abc123XYZ00.mp3").write_bytes(b"youtube-audio")
    return Completed()


fake = FakeDeezer(fail=True)
temp_root, status, path, media = run_download(
    {
        "source": "youtube", "source_id": "abc123XYZ00", "video_id": "abc123XYZ00",
        "artist": "Test Artist", "title": "Test Track",
    },
    fake,
    ytdlp_side_effect=fake_ytdlp,
    found_song={"id": 3135556},
)
assert fake.calls, "Deezer should have been tried first"
assert status == "downloaded"
assert path.suffix == ".mp3", path
assert path.read_bytes() == b"youtube-audio"
temp_root.cleanup()
print("download_falls_back_to_youtube=passed")


# Without a Deezer session the YouTube path runs directly.
temp_root, status, path, media = run_download(
    {
        "source": "youtube", "source_id": "abc123XYZ00", "video_id": "abc123XYZ00",
        "artist": "Test Artist", "title": "Test Track",
    },
    None,
    ytdlp_side_effect=fake_ytdlp,
)
assert status == "downloaded" and path.suffix == ".mp3"
temp_root.cleanup()
print("download_without_deezer=passed")


# The setup page saves the ARL instead of dropping it.
saved = {}
client = app.app.test_client()
with (
    patch.object(app.config, "save", side_effect=lambda settings: saved.update(settings)),
    patch.object(app, "apply_settings"),
    patch.object(app, "SETTINGS", {"state_dir": "", "download_dir": "", "secret_key": "x"}),
    patch.object(app.config, "is_configured", return_value=False),
):
    response = client.post("/setup", data={
        "download_dir": tempfile.gettempdir(),
        "bind_host": "127.0.0.1",
        "bind_port": "8797",
        "deezer_arl": "a" * 192,
    })
assert response.status_code == 302, response.status_code
assert saved.get("deezer_arl") == "a" * 192, "the setup form must keep the ARL"
print("setup_saves_deezer_arl=passed")
