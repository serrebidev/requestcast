RequestCast gains an optional Soulseek source, finer control over Deezer and
YouTube audio quality, and richer Deezer metadata and lyrics.

**A Soulseek source for the tracks nothing else has.** Enable it in Preferences
with a username and password, and a separate "Soulseek only" search appears alongside
YouTube, Deezer, and musicdl. Results list peers, formats, and availability, and a
download fetches the file straight from the chosen peer. Soulseek results have no
catalog fallback — a peer that goes offline fails that one track rather than silently
substituting another source. The download folder can be shared back to keep the network
alive, and is off by default. Requests from peers who share nothing are refused, and
folders split across drives no longer crash the share scanner.

**Deezer quality is now your choice.** The ARL setting previously always asked for
FLAC and then dropped to 320 kbps MP3. Preferences now has a quality option: FLAC with
a 320 kbps fallback (the default), or 320 kbps MP3 only. There is no silent drop to
128 kbps — if the account cannot serve what you asked for, the track falls through to
the next source.

**Deezer's own metadata, plus synced lyrics.** Deezer downloads now read the track's
authoritative metadata from Deezer's gateway — title, artist, album, ISRC, release year,
track and disc numbers, and cover art — instead of guessing from the search result.
Deezer tracks also fetch synced lyrics from LRCLIB when available and tag them into the
file next to the cover art.

**YouTube audio format control.** A new setting decides what YouTube audio is delivered
as. The default is "original", which keeps the source codec untouched — the
quality-preserving behaviour RequestCast has always had. Converting to FLAC, MP3, or
Opus is now an option for stations that prefer one container; converting to FLAC never
adds quality, it only re-containers the audio.
