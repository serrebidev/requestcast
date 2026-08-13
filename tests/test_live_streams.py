import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app, config


# The live settings exist, are off by default, and read their env names.
assert config.FIELDS["azuracast_live_enabled"] is False
assert config.FIELDS["azuracast_live_url"] == ""
assert config.FIELDS["azuracast_live_metadata_url"] == ""
assert config.FIELDS["azuracast_live_metadata_key"] == ""
assert "REQUESTCAST_AZURACAST_LIVE_ENABLED" in config.ENVIRONMENT_NAMES["azuracast_live_enabled"]
assert "REQUESTCAST_AZURACAST_LIVE_URL" in config.ENVIRONMENT_NAMES["azuracast_live_url"]
assert "REQUESTCAST_AZURACAST_LIVE_METADATA_URL" in config.ENVIRONMENT_NAMES["azuracast_live_metadata_url"]
assert "REQUESTCAST_AZURACAST_LIVE_METADATA_KEY" in config.ENVIRONMENT_NAMES["azuracast_live_metadata_key"]
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


# A live track is never downloaded: the downloader explains the livestream instead,
# with a specific exception the retry machinery refuses to retry.
try:
    app.download_one({
        "source": "youtube", "video_id": "BkoZnfez9Y0", "source_id": "BkoZnfez9Y0",
        "is_live": True, "title": "Test Live", "artist": "Channel",
    })
    raise SystemExit("a live track must not download")
except app.LiveStreamError as exc:
    assert "livestream" in str(exc).lower(), str(exc)
print("live_track_refuses_download=passed")


# A livestream failure is never retried track-by-track or requeued as a job.
with (
    patch.object(app, "download_one", side_effect=app.LiveStreamError("live")),
    patch.object(app, "pause_job") as pause,
):
    try:
        app.download_track({"source": "youtube"}, "0" * 32, "label")
        raise SystemExit("a live failure must not be retried")
    except app.LiveStreamError:
        pass
pause.assert_not_called()


class FakeJobRow:
    def __init__(self, job_id):
        self.id = job_id

    def __getitem__(self, key):
        return getattr(self, key)


with (
    patch.object(app, "job_attempts", return_value=1),
    patch.object(app, "describe_download_error", side_effect=lambda message: message),
    patch.object(app, "update_job") as update,
    patch.object(app, "pause_job") as requeued,
):
    app.fail_or_requeue(FakeJobRow("0" * 32), app.LiveStreamError("live"))
assert update.call_args[1]["state"] == "failed", update.call_args
requeued.assert_not_called()
print("livestream_failures_do_not_retry=passed")


# The relay starts a yt-dlp -> ffmpeg -> harbor pipeline and reports on air.
class FakeStream:
    def close(self):
        pass


class FakeProc:
    def __init__(self, exit_after_wait=False):
        self.stdout = FakeStream()
        self.terminated = False
        self.killed = False
        self.exit_after_wait = exit_after_wait
        self._done = threading.Event()

    def poll(self):
        return 0 if (self.terminated or self.killed) else None

    def terminate(self):
        self.terminated = True
        self._done.set()

    def wait(self, timeout=None):
        # Blocks like a real child process: returns only once the process ends
        # (terminate/kill here) or, for the watchdog test, once the source exits.
        if self.exit_after_wait:
            self._done.set()
        self._done.wait(timeout)
        return 0

    def kill(self):
        self.killed = True
        self._done.set()


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


# When the source ends, the watchdog stops the lingering encoder, clears the
# on-air record, and marks the live over so AutoDJ resumes.
source_proc = FakeProc(exit_after_wait=True)  # stream ended: yt-dlp exits
encoder_proc = FakeProc()  # encoder still feeding the harbor
app._live_relay["procs"] = [source_proc, encoder_proc]
app._live_relay["title"] = "Ending Live"
app._live_relay["artist"] = "Channel"
app._live_relay["video_id"] = "BkoZnfez9Y0"
app._live_relay["started_at"] = int(app.time.time())
with patch.object(app, "_push_live_ended") as ended:
    app._live_watchdog(source_proc, encoder_proc)
assert encoder_proc.terminated, "the encoder must stop when the source ends"
assert app.live_now() is None
ended.assert_called_once()
print("livestream_end_returns_to_autodj=passed")


# A stale watchdog (from a re-requested stream) never tears down the new relay.
old_source, old_encoder = FakeProc(exit_after_wait=True), FakeProc()
new_source, new_encoder = FakeProc(), FakeProc()
app._live_relay["procs"] = [new_source, new_encoder]
app._live_relay["video_id"] = "BkoZnfez9Y0"
with patch.object(app, "_push_live_ended") as never_ended:
    app._live_watchdog(old_source, old_encoder)
assert not new_source.terminated and not new_encoder.terminated
assert app.live_now() is not None, "the new relay must stay on air"
never_ended.assert_not_called()
app.stop_live_relay()
print("stale_watchdog_leaves_new_relay_alone=passed")


# The livestream title is pushed to Liquidsoap with the API key, escaped for the
# telnet command.
with (
    patch.object(app, "AZURACAST_LIVE_METADATA_URL", "http://liquid:8004/telnet"),
    patch.object(app, "AZURACAST_LIVE_METADATA_KEY", "api-secret"),
    patch.object(app.requests, "post") as post,
):
    app._push_live_metadata('Making Scammers Angry \"Live\"', "Kitboga")
post.assert_called_once()
args, kwargs = post.call_args
assert args[0] == "http://liquid:8004/telnet"
body = kwargs["data"]
assert b"custom_metadata.insert" in body
assert b'title="Making Scammers Angry \\"Live\\""' in body
assert b'artist="Kitboga"' in body and b'is_live="true"' in body
assert kwargs["headers"]["x-liquidsoap-api-key"] == "api-secret"
# Without a configured URL the push is a silent no-op.
with patch.object(app.requests, "post") as never:
    app._push_live_metadata("X", "Y")
never.assert_not_called()
print("livestream_metadata_push=passed")


# The end-of-live push clears the live flag so the on-air status stops claiming live.
with (
    patch.object(app, "AZURACAST_LIVE_METADATA_URL", "http://liquid:8004/telnet"),
    patch.object(app, "AZURACAST_LIVE_METADATA_KEY", "api-secret"),
    patch.object(app.requests, "post") as ended_post,
):
    app._push_live_ended()
ended_post.assert_called_once()
assert b'custom_metadata.insert is_live="false"' in ended_post.call_args.kwargs["data"]
with patch.object(app.requests, "post") as never_ended:
    app._push_live_ended()
never_ended.assert_not_called()
print("livestream_ended_push=passed")


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


# A single livestream that does not reach the relay branch fails the job fast.
import json  # noqa: E402

with (
    patch.object(app, "expand_youtube", return_value=[{
        "source": "youtube", "is_live": True, "video_id": "BkoZnfez9Y0",
        "title": "Test Live", "artist": "Channel",
    }]),
    patch.object(app, "update_job"),
    patch.object(app, "AZURACAST_ENABLED", True),
    patch.object(app, "AZURACAST_LIVE_ENABLED", False),
):
    job = {
        "id": "0" * 32,
        "payload": json.dumps({
            "source": "youtube", "kind": "video", "video_id": "BkoZnfez9Y0",
            "is_live": True, "_request_after_add": True,
        }),
    }
    try:
        app.process_job(job)
        raise SystemExit("an unrelayed livestream must fail the job")
    except app.LiveStreamError:
        pass
print("unrelayed_livestream_fails_fast=passed")
