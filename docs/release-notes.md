Livestream requests now play immediately instead of hanging in the queue, and the
livestream title is pushed to Liquidsoap.

**Livestreams fail fast and never retry.** A livestream that is queued behind an
older job (or that can't be relayed) used to sit in the queue as "waiting for the
downloader" forever — the downloader kept trying to grab a stream that never ends.
Livestream downloads now raise a distinct `LiveStreamError` that fails the job
immediately with a clear message and is excluded from automatic retries and
requeues, so nothing blocks the queue.

**Livestream metadata is pushed to air.** When a livestream is relayed, the
stream's title and artist are pushed to Liquidsoap via `custom_metadata.insert`
right after the harbor switch, and refreshed periodically while it plays — so the
on-air display shows the actual stream title instead of a stale AutoDJ track.
Configured with `azuracast_live_metadata_url` (the telnet/API endpoint) and
`azuracast_live_metadata_key`, or on a server
`REQUESTCAST_AZURACAST_LIVE_METADATA_URL` / `REQUESTCAST_AZURACAST_LIVE_METADATA_KEY`.
