# RequestCast

Search for music, download it, tag it properly, and file it into a library — either a folder
on your own machine or an [AzuraCast](https://www.azuracast.com/) station's request playlist.

It runs two ways from the same code: a **portable Windows program** you double-click, and a
**server deployment** behind a reverse proxy.

## What it does

- Search YouTube and Deezer from one box, or paste a URL to a track, album, playlist, or artist
- Upload a **TXT, XLSX, or PDF list** of music and it indexes the whole thing, then works through it
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

Download the release, unzip it anywhere, and run `RequestCast.exe`. The setup page opens in your
browser. Everything it writes — settings, the job database, downloaded music — stays inside that
folder, so the whole thing can live on a USB stick.

On first run it asks for:

1. **A download folder.** The only required answer.
2. **AzuraCast details** — optional. Leave the box unticked and RequestCast is a plain music
   downloader that saves tagged files to your download folder.
3. **A password** — required only if you let it listen on a network address rather than
   `127.0.0.1`.

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
```

Setting `REQUESTCAST_AZURACAST_API_KEY` turns the AzuraCast integration on. Serve it with
gunicorn or waitress behind nginx, and give it a generous request timeout — indexing a
7,000-row PDF takes about twenty seconds and happens inside the upload request.

`systemd` unit and nginx examples are in [`docs/deployment.md`](docs/deployment.md).

## Where the audio comes from

Audio is fetched with yt-dlp from YouTube. Deezer is used for **metadata only**, through its
public API — no account, no ARL, no credentials of any kind. RequestCast does not decrypt
protected streams and will not be extended to do so.

You are responsible for having the right to download and broadcast whatever you point it at.

## Accessibility

The interface is plain HTML with real headings, labelled form controls, and no scripted
widgets. It is built to be usable with a screen reader.

## Licence

MIT — see [LICENSE](LICENSE).

yt-dlp and ffmpeg are separate programs under their own licences; RequestCast runs them, and
does not include them.
