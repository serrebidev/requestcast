Livestreams now hand the air back to AutoDJ by themselves when they end, and
ffprobe is tracked as a required tool.

**Auto-return to AutoDJ.** When a relayed livestream ends, yt-dlp exits but the
ffmpeg encoder can linger on the harbor feeding silence, which keeps the station
stuck on a dead live source. A watchdog now waits on the source process and, the
moment it ends, stops the encoder, clears the on-air record, and pushes
`is_live="false"` to Liquidsoap — so AutoDJ resumes automatically and the
now-playing display stops claiming a live stream. The watcher is keyed on the
actual process handles, so re-requesting the same stream can never let a stale
watcher tear down the new relay.

**ffprobe is a required tool.** The missing-tools check (setup page, diagnostics,
and automatic install) now includes ffprobe alongside ffmpeg, so a station with
ffmpeg but no ffprobe is reported clearly and, on Windows, both are installed
together.
