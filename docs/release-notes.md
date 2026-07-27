Uses your own default browser, stops wasting disk space, and lets you choose what to download.

**Your default browser, and no browser downloads.** Earlier builds launched a separate
Chromium window against a private user-data directory, which copied a whole Microsoft Edge
profile — frequently hundreds of megabytes — into RequestCast's own folder. That is gone.
RequestCast now hands the address to Windows, and whichever browser you have set as your
default opens it. On startup it also deletes any browser profile folder an older version
left behind, so the space comes back.

**Diagnostics are off by default.** The full support bundle used to write a request log and
collect tens of megabytes of Windows networking evidence every time the program started. It
is now a preference, off unless you turn it on: tick it under Preferences, start with
`RequestCast.exe --diagnostics`, or set `REQUESTCAST_DIAGNOSTICS=1`. `--diagnose` still
collects one bundle on demand when someone asks you for it.

**Downloads are readable by everyone.** Finished files kept the private permissions of the
temporary folder they were built in, so they had to be fixed by hand every time. RequestCast
now resets each finished file, and the download folder itself, to be readable by every
account on the machine.

**Clear your download history.** Clear finished downloads, or the whole history, from the
main page, and remove a single entry from its status page. Downloads still in progress are
kept, and no music files are ever deleted.

**Pick what you want from an artist or channel.** Enter an artist, or paste a YouTube
channel, and open it to see its releases, albums, singles, songs, and videos. Take all of it
with one button, or tick only the parts you want.

**Upload the playlists you already have.** M3U, M3U8, PLS, XSPF, WPL, ASX, CUE, and
foobar2000 FPL are read alongside TXT, XLSX, and PDF lists. RequestCast reads the artist and
title each playlist names and fetches those tracks, so the original files do not need to be
present on this machine.
