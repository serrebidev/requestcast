import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app, soulseek


# The backend module loads with or without aioslsk; availability is a plain flag.
assert isinstance(soulseek.available(), bool)
assert isinstance(soulseek.import_error(), str)
print("soulseek_optional_import=passed")


# Shared-folder safety: a path is only "inside" another on the same drive, and
# Windows drives never match by accident.
assert soulseek._path_is_within("C:\\music", "C:\\music\\artist\\track.flac")
assert not soulseek._path_is_within("C:\\music", "C:\\musicology\\track.flac")
assert not soulseek._path_is_within("C:\\music", "D:\\music\\track.flac")
print("soulseek_cross_drive_safety=passed")


# The settings snapshot keeps only the fields Soulseek reads, and normalizes paths.
snapshot = soulseek._config_snapshot({
    "soulseek_enabled": True,
    "soulseek_username": "  radio-listen  ",
    "soulseek_password": "secret",
    "soulseek_max_results": 120,
    "soulseek_share_downloads": False,
    "download_dir": "C:\\music",
    "share_dir": "C:\\radio-media",
})
assert snapshot["enabled"] is True
assert snapshot["username"] == "radio-listen"
assert snapshot["max_results"] == 120
assert snapshot["share_downloads"] is False
assert snapshot["download_dir"] == os.path.abspath("C:\\music")
assert snapshot["share_dir"] == os.path.abspath("C:\\radio-media")
# Without an explicit share folder the download folder is shared instead.
fallback = soulseek._config_snapshot({"download_dir": "C:\\music", "soulseek_enabled": True})
assert fallback["share_dir"] == os.path.abspath("C:\\music")
assert snapshot["block_leechers"] is True
print("soulseek_config_snapshot=passed")


# Byte sizing is only for display.
assert soulseek._format_size(0) == "0.0 B"
assert soulseek._format_size(1536) == "1.5 KB"
print("soulseek_size_format=passed")


# A Soulseek hit maps to the same downloadable shape as every other source.
hit = {
    "username": "peer1",
    "remote_path": "\\\\peer1\\music\\track.flac",
    "title": "track.flac",
    "artist": "Peer One",
    "duration_s": 200,
    "size_bytes": 1024,
    "format": "FLAC, 900 kbps",
    "file_size": "1.0 KB",
    "availability": "free slot",
}
result = app.format_soulseek_result(hit)
assert result is not None
assert result["source"] == "soulseek" and result["kind"] == "song"
assert result["id"] == "peer1::\\\\peer1\\music\\track.flac"
assert result["source_id"] == "\\\\peer1\\music\\track.flac"
assert result["username"] == "peer1"
assert result["remote_path"] == "\\\\peer1\\music\\track.flac"
assert result["title"] == "track.flac"
assert result["artist"] == "Peer One"
assert result["duration_seconds"] == 200
assert result["size_bytes"] == 1024
assert "FLAC" in result["detail"]
token_payload = app.signer.loads(result["token"])
assert token_payload["source"] == "soulseek" and token_payload["username"] == "peer1"
assert app.format_soulseek_result({}) is None
assert app.format_soulseek_result({"username": "x"}) is None
print("soulseek_result_format=passed")


# Expansion turns one peer file into exactly one track, and refuses a shapeless hit.
expanded = app.expand_soulseek(result)
assert len(expanded) == 1
assert expanded[0]["source"] == "soulseek"
assert expanded[0]["username"] == "peer1"
assert expanded[0]["remote_path"] == "\\\\peer1\\music\\track.flac"
try:
    app.expand_soulseek({})
    raise SystemExit("a Soulseek result without a peer/path must be rejected")
except RuntimeError:
    pass
print("soulseek_expand=passed")


# A disabled source never searches and never downloads.
with patch.object(app, "SOULSEEK_ENABLED", False):
    assert app.search_soulseek("ABBA", "song") == []
    try:
        app.download_via_soulseek({}, Path(tempfile.mkdtemp()))
        raise SystemExit("a disabled Soulseek source must refuse to download")
    except soulseek.SoulseekError as exc:
        assert "disabled" in str(exc)
print("soulseek_disabled=passed")


# The search-source list only advertises Soulseek when it is on.
with patch.object(app, "SOULSEEK_ENABLED", True), patch.object(app, "MUSICDL_ENABLED", False):
    assert ("soulseek", "Soulseek only") in app.search_sources()
with patch.object(app, "SOULSEEK_ENABLED", False):
    assert all(value != "soulseek" for value, _label in app.search_sources())
print("soulseek_search_source=passed")


# The shared folder defaults to the AzuraCast media library; the download folder
# stays the working directory for local mode and is only shared without AzuraCast.
with (
    patch.object(app, "AZURACAST_ENABLED", True),
    patch.object(app, "MEDIA_DIR", Path("C:\\radio-media")),
    patch.object(app, "DOWNLOAD_DIR", Path("C:\\downloads")),
):
    azuracast_cfg = app.soulseek_config()
assert azuracast_cfg["share_dir"] == "C:\\radio-media"
assert azuracast_cfg["download_dir"] == "C:\\downloads"
with (
    patch.object(app, "AZURACAST_ENABLED", False),
    patch.object(app, "MEDIA_DIR", Path("C:\\downloads")),
    patch.object(app, "DOWNLOAD_DIR", Path("C:\\downloads")),
):
    local_cfg = app.soulseek_config()
assert local_cfg["share_dir"] == "C:\\downloads"
print("soulseek_shares_azuracast_media_by_default=passed")


# The YouTube audio-format setting keeps the source untouched unless a real
# conversion is requested, and passes unknown values through unchanged.
root = Path(tempfile.mkdtemp())
source = root / "source.m4a"
source.write_bytes(b"original-audio")
assert app.convert_audio_format(source, root, "original") == source
assert app.convert_audio_format(source, root, "") == source
assert app.convert_audio_format(source, root, "wav") == source
same = root / "already.mp3"
same.write_bytes(b"already-mp3")
assert app.convert_audio_format(same, root, "mp3") == same


class Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def fake_ffmpeg(command, **kwargs):
    Path(command[-1]).write_bytes(b"converted-audio")
    return Completed()


with patch.object(app.subprocess, "run", side_effect=fake_ffmpeg), patch.object(app, "FFMPEG", "ffmpeg"):
    converted = app.convert_audio_format(source, root, "mp3")
assert converted.suffix == ".mp3"
assert converted.read_bytes() == b"converted-audio"
print("youtube_audio_format_conversion=passed")
