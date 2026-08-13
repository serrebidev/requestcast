import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app, config


# The two live settings exist, are off by default, and read their env names.
assert config.FIELDS["azuracast_live_enabled"] is False
assert config.FIELDS["azuracast_live_url"] == ""
assert "REQUESTCAST_AZURACAST_LIVE_ENABLED" in config.ENVIRONMENT_NAMES["azuracast_live_enabled"]
assert "REQUESTCAST_AZURACAST_LIVE_URL" in config.ENVIRONMENT_NAMES["azuracast_live_url"]
print("live_settings_exist=passed")


# A live YouTube URL is recognised and the flag survives the signed token.
with patch.object(app, "ytdlp_json", return_value={
    "id": "BkoZnfez9Y0", "title": "Test Live", "channel": "The Channel",
    "live_status": "is_live", "is_live": True, "duration": None,
}):
    live_result = app.youtube_url_result("https://www.youtube.com/watch?v=BkoZnfez9Y0")
assert live_result["is_live"] is True
assert live_result["live_status"] == "is_live"
assert app.signer.loads(live_result["token"])["is_live"] is True

with patch.object(app, "ytdlp_json", return_value={
    "id": "abc123XYZ00", "title": "A Recording", "channel": "Artist",
    "live_status": "not_live", "duration": 200,
}):
    recording = app.youtube_url_result("https://www.youtube.com/watch?v=abc123XYZ00")
assert recording["is_live"] is False
print("livestream_detection=passed")


# A live track is never downloaded: the downloader explains the livestream instead.
try:
    app.download_one({
        "source": "youtube", "video_id": "BkoZnfez9Y0", "source_id": "BkoZnfez9Y0",
        "is_live": True, "title": "Test Live", "artist": "Channel",
    })
    raise SystemExit("a live track must not download")
except RuntimeError as exc:
    assert "livestream" in str(exc).lower(), str(exc)
print("live_track_refuses_download=passed")


# The relay starts a yt-dlp -> ffmpeg -> harbor pipeline and reports on air.
class FakeStream:
    def close(self):
        pass


class FakeProc:
    def __init__(self):
        self.stdout = FakeStream()
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


popen_calls = []


def fake_popen(command, **kwargs):
    popen_calls.append((list(command), kwargs))
    return FakeProc()


with (
    patch.object(app, "AZURACAST_LIVE_URL", "icecast://automation:pass@127.0.0.1:8005/live"),
    patch.object(app, "YTDLP", "yt-dlp"),
    patch.object(app, "FFMPEG", "ffmpeg"),
    patch.object(app.subprocess, "Popen", side_effect=fake_popen),
):
    detail = app.play_live_stream({
        "video_id": "BkoZnfez9Y0", "title": "Test Live", "artist": "The Channel",
    })
assert "on air" in detail and "Test Live" in detail
assert len(popen_calls) == 2, popen_calls
ytdlp_command, ffmpeg_command = popen_calls[0][0], popen_calls[1][0]
assert ytdlp_command[0] == "yt-dlp"
assert "-o" in ytdlp_command and "-" in ytdlp_command
assert any("BkoZnfez9Y0" in part for part in ytdlp_command)
assert ffmpeg_command[0] == "ffmpeg"
assert "-re" in ffmpeg_command
assert any("libmp3lame" in part for part in ffmpeg_command)
assert ffmpeg_command[-1] == "icecast://automation:pass@127.0.0.1:8005/live"

now = app.live_now()
assert now is not None and now["title"] == "Test Live", now
assert now["video_id"] == "BkoZnfez9Y0"
assert app.stop_live_relay() is True
assert app.live_now() is None
print("livestream_relay=passed")


# Starting a second relay stops the first, and a missing harbor URL fails clearly.
popen_calls.clear()
with (
    patch.object(app, "AZURACAST_LIVE_URL", "icecast://automation:pass@127.0.0.1:8005/live"),
    patch.object(app, "YTDLP", "yt-dlp"),
    patch.object(app, "FFMPEG", "ffmpeg"),
    patch.object(app.subprocess, "Popen", side_effect=fake_popen),
):
    app.play_live_stream({"video_id": "BkoZnfez9Y0", "title": "First", "artist": ""})
    first_procs = app.live_now()
    app.play_live_stream({"video_id": "BkoZnfez9Y0", "title": "Second", "artist": ""})
assert first_procs is not None and app.live_now()["title"] == "Second"
app.stop_live_relay()

with (
    patch.object(app, "AZURACAST_LIVE_URL", ""),
    patch.object(app, "YTDLP", "yt-dlp"),
    patch.object(app, "FFMPEG", "ffmpeg"),
    patch.object(app.subprocess, "Popen") as never_spawned,
):
    try:
        app.play_live_stream({"video_id": "BkoZnfez9Y0", "title": "X", "artist": ""})
        raise SystemExit("a missing harbor URL must refuse to relay")
    except RuntimeError as exc:
        assert "harbor" in str(exc), str(exc)
never_spawned.assert_not_called()
print("livestream_relay_replacement_and_guard=passed")
