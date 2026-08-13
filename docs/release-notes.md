YouTube livestreams now play live instead of trying to download.

**"Add & request" on a livestream puts it on the air.** A YouTube livestream never
ends, so there was nothing sensible to download — the old behaviour was a download
that hung or failed. Now, when live playback is enabled in Preferences, choosing
"Add & request" on a live URL relays the stream straight into the station's live
input, and AzuraCast's live-fallback switch takes it on air a few seconds later.
The home page shows the live stream while it is playing, with a Stop button that
hands the air back to AutoDJ.

**Live playback is opt-in and configurable.** Two new settings control it:
`azuracast_live_enabled` (off by default) and `azuracast_live_url`, the icecast://
URL of the station's live/DJ harbor. On a server they can also be set with
`REQUESTCAST_AZURACAST_LIVE_ENABLED=1` and `REQUESTCAST_AZURACAST_LIVE_URL`. A
livestream that is added but not requested, or one that appears inside a playlist,
is reported clearly as a livestream rather than being silently attempted.
