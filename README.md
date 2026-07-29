# RequestCast

Search for music, download it, tag it properly, and file it into a library — either a folder
on your own machine or an [AzuraCast](https://www.azuracast.com/) station's request playlist.

It runs two ways from the same code: a **portable Windows program** you double-click, and a
**server deployment** behind a reverse proxy.

## What it does

- Search YouTube and Deezer from one box, or paste a URL to a track, album, playlist, or artist
- Open any **artist or YouTube channel** to take everything, or tick only the releases,
  albums, and videos you actually want
- Upload a **TXT, XLSX, or PDF list** of music and it indexes the whole thing, then works through it
- Upload a **playlist you already have** — M3U, M3U8, PLS, XSPF, WPL, ASX, CUE, or
  foobar2000 FPL — and it fetches the tracks the playlist names, without needing the
  original files
- Finished files are **readable by every account** on the machine, with no permission
  fixing afterwards
- **Clear the download history** whenever you like; the music files are left alone
- With a **Deezer subscriber ARL** configured, downloads come from Deezer first — **FLAC**, or
  **320 kbps MP3** when FLAC is not offered — and YouTube is only the fallback
- Prefers **Deezer's copy of a track's metadata** when one matches cleanly, so artist, title,
  album, year, and ISRC come out right instead of guessed from a video title
- Artist-only lines take that artist's **entire catalogue** by default, or a cap you choose
- **Preserves audio quality** — the source stream is remuxed, never re-encoded, and the tests
  verify the audio packets come out bit-identical
- Writes proper tags and cover art for MP3, MP4/M4A, FLAC, Ogg, and WAV/AIFF
- Optionally uploads each finished file to AzuraCast and adds it to a request playlist

## Requirements

- Python 3.11 or newer, if running from source
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org/) — on Windows the
  setup page offers to download both into a `tools` folder beside the program

## Portable Windows program

Download the release, unzip it anywhere, and run `RequestCast.exe`. The setup page opens in
**your own default browser** — RequestCast never downloads, installs, or copies a browser of
its own. Everything it writes — settings, the job database, downloaded music — stays inside
that folder, so the whole thing can live on a USB stick.

On first run it asks for:

1. **A download folder.** The only required answer.
2. **AzuraCast details** — optional. Leave the box unticked and RequestCast is a plain music
   downloader that saves tagged files to your download folder.
3. **A password** — opens RequestCast.
4. **An admin password** — protects Preferences.

Use **Preferences** to change these later.

### Diagnostics

Diagnostics are **off**. When they are on, RequestCast writes a request log and collects a
support ZIP of Windows networking evidence beside the program — tens of megabytes — so it
only does that when someone has asked you for one. Turn it on in **Preferences**, or start
with `RequestCast.exe --diagnostics`. `--diagnose` collects one bundle and exits.

### Building it yourself

```
pip install -r requirements.txt pyinstaller
python scripts/build_windows.py
```

The result is `dist/RequestCast/`.

## Running from source

```
pip install -r requirements.txt
python run.py
```

## Server deployment

On a server, configure it with environment variables instead of the setup page — any variable
that is set overrides the settings file, so the setup page never has to be exposed.

```
REQUESTCAST_DOWNLOAD_DIR=/var/lib/requestcast/downloads
REQUESTCAST_STATE_DIR=/var/lib/requestcast/state
REQUESTCAST_AZURACAST_API_BASE=http://127.0.0.1:12000/api
REQUESTCAST_AZURACAST_API_KEY=...
REQUESTCAST_STATION_ID=1
REQUESTCAST_REQUEST_PLAYLIST_ID=10
REQUESTCAST_SECRET_KEY=...
REQUESTCAST_PASSWORD_SALT=...   # 32 bytes, hex
REQUESTCAST_PASSWORD_HASH=...   # scrypt, hex
REQUESTCAST_ADMIN_PASSWORD_SALT=...
REQUESTCAST_ADMIN_PASSWORD_HASH=...
REQUESTCAST_DEEZER_ARL=...      # optional Deezer subscriber ARL
REQUESTCAST_MUSICDL_ENABLED=1   # optional; musicdl fallback between Deezer and YouTube
REQUESTCAST_MUSICDL_SOURCES=MiguMusicClient,NeteaseMusicClient,QQMusicClient,KuwoMusicClient,QianqianMusicClient
REQUESTCAST_DIAGNOSTICS=1       # optional; collects a support bundle, off by default
```

Setting `REQUESTCAST_AZURACAST_API_KEY` turns the AzuraCast integration on. Serve it with
gunicorn or waitress behind nginx, and give it a generous request timeout and body size —
indexing happens inside the upload request, and uploads are accepted up to 64 MB.

`systemd` unit and nginx examples are in [`docs/deployment.md`](docs/deployment.md).

## Where the audio comes from

With a Deezer subscriber ARL configured (the settings page, or `REQUESTCAST_DEEZER_ARL`),
audio comes from the Deezer account first: FLAC when the account is offered it, then
320 kbps MP3, then 128 kbps MP3. Tracks the account cannot supply fall back to
**musicdl** — which searches the configured platforms (NetEase, QQ, Kugou, Kuwo, and
Migu by default; `REQUESTCAST_MUSICDL_SOURCES` changes the list, and
`REQUESTCAST_MUSICDL_ENABLED=0` turns the fallback off) — and finally to YouTube,
fetched with yt-dlp. Without an ARL, YouTube is the last source and Deezer is used for
**metadata only**, through its public API.

musicdl also widens URL input: pasting a track or playlist URL from any platform it
supports (NetEase, QQ Music, Kugou, Kuwo, Migu, Qianqian, Spotify, SoundCloud, TIDAL,
Qobuz, Apple Music, JOOX, JioSaavn, Jamendo, and more) downloads it through musicdl
directly.

The ARL is runtime configuration. It lives in the settings file or the environment, and
must never be committed to the repository.

You are responsible for having the right to download and broadcast whatever you point it at.

## Testing

Run the self-contained test suite from the repository root:

```bash
python tests/run_all.py
```

This includes the Deezer quality, decryption, source-preference, YouTube-fallback, setup,
configuration, import, playlist-format, artist-browsing, job-history, download-permission,
default-browser, diagnostics, request, upload, audio-preservation, and URL-input checks. The
remaining scripts under `tests/` are manual integration checks: they require either the private
playlist fixtures named in the script or a live RequestCast/AzuraCast deployment.

## Accessibility

The interface is plain HTML with real headings, labelled form controls, and no scripted
widgets. It is built to be usable with a screen reader.

## Licence

MIT — see [LICENSE](LICENSE).

yt-dlp and ffmpeg are separate programs under their own licences; RequestCast runs them, and
does not include them.
