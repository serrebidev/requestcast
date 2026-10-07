## 1.8.8

Maintenance release: charset-normalizer 3.5.1 -> 3.5.2 (patch bump), GitHub Actions updated to Node 24-compatible versions (checkout v5, setup-python v6).

Two ways a download could quietly stop making progress are fixed: it can no longer
pull the whole video behind a track, and it can no longer sit in "running" forever
with nothing working on it.

**Audio only, never video.** A YouTube download now asks for an audio stream and
nothing else. Some player clients answer with muxed streams only, and the old
`bestaudio/best` fallback then downloaded the entire video — a 56-minute talk came
down as a 754 MB file carrying an h264 stream, which then went into the request
library. Failing that client instead hands the same track to the next one in the
rotation, which does offer audio-only formats, so the download is smaller and
faster rather than merely successful.

**Interrupted jobs are taken back.** A job left in "running" by a worker that is
no longer processing it — a download thread that died, a worker killed mid-run, or
an exception path that never reached the failure handler — was invisible for the
life of the service: never downloaded, never requested, its status page spinning
until someone restarted RequestCast. The worker loop now notices that nothing is
holding such a job and queues it again, spending one of its retries; a job that
cannot survive its own download runs out of retries and is recorded as failed, so
it reads as broken instead of hanging.

