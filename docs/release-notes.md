Two fixes for the musicdl integration from 1.6.0.

**Single-track pages now work on every musicdl platform.** Pasting a bare track URL —
`soundcloud.com/artist/song`, not just a playlist or set — used to fail with "That URL
did not produce any tracks musicdl recognizes", because musicdl's URL parser only
understands collections. RequestCast now reads the page for an artist and title and
resolves the track by searching that platform's own musicdl client. Tracks whose
streams cannot be stored up front (HLS streams, short-lived URLs) are re-resolved the
same way when the download runs instead of being dropped.

**musicdl no longer fails on hardened Linux services.** A service running with a
read-only home directory (systemd `ProtectSystem=strict`) got
`[Errno 30] Read-only file system` on any musicdl lookup, because musicdl creates its
log directory under the account's home. RequestCast now points the XDG directories
musicdl uses at its own state directory; XDG values you set yourself still win.
