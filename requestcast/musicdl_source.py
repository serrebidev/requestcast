"""Download full audio through musicdl (https://github.com/CharlesPikachu/musicdl).

musicdl searches and downloads from dozens of music platforms (NetEase, QQ, Kugou,
Kuwo, Migu, Spotify, SoundCloud, TIDAL, Qobuz, Apple Music, and more). RequestCast
uses it in two places:

1. as the fallback download source between Deezer and YouTube — a track Deezer
   cannot supply is searched by artist/title across the configured sources;
2. as a URL handler — platform URLs the native handlers do not cover are parsed
   with the matching musicdl client and downloaded directly.

musicdl is a heavy optional dependency, so every import of it is lazy: nothing here
touches the network or the musicdl package until a download or URL parse actually
asks for it, and any failure raises ``MusicdlError`` so the caller can fall back.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


class MusicdlError(RuntimeError):
    """musicdl is unavailable or the download failed; the caller falls back."""


# The platforms musicdl can parse URLs for, as (host suffix, musicdl client name).
# More specific suffixes come first so 5sing.kugou.com does not match kugou.com.
HOST_CLIENTS: tuple[tuple[str, str], ...] = (
    ("5sing.kugou.com", "FiveSingMusicClient"),
    ("h5app.kuwo.cn", "BodianMusicClient"),
    ("music.163.com", "NeteaseMusicClient"),
    ("163cn.tv", "NeteaseMusicClient"),
    ("y.qq.com", "QQMusicClient"),
    ("kugou.com", "KugouMusicClient"),
    ("kuwo.cn", "KuwoMusicClient"),
    ("music.migu.cn", "MiguMusicClient"),
    ("migu.cn", "MiguMusicClient"),
    ("music.91q.com", "QianqianMusicClient"),
    ("91q.com", "QianqianMusicClient"),
    ("open.spotify.com", "SpotifyMusicClient"),
    ("spotify.com", "SpotifyMusicClient"),
    ("soundcloud.com", "SoundCloudMusicClient"),
    ("tidal.com", "TIDALMusicClient"),
    ("qobuz.com", "QobuzMusicClient"),
    ("music.apple.com", "AppleMusicClient"),
    ("joox.com", "JooxMusicClient"),
    ("jiosaavn.com", "JioSaavnMusicClient"),
    ("jamendo.com", "JamendoMusicClient"),
    ("qishui.douyin.com", "SodaMusicClient"),
    ("streetvoice.cn", "StreetVoiceMusicClient"),
    ("streetvoice.com", "StreetVoiceMusicClient"),
    ("freemusicarchive.org", "FMAMusicClient"),
    ("suno.com", "SunoMusicClient"),
    ("suno.ai", "SunoMusicClient"),
    ("moov.hk", "MOOVMusicClient"),
    ("bilibili.com", "BilibiliMusicClient"),
    ("b23.tv", "BilibiliMusicClient"),
)
URL_HOST_PREFIXES: tuple[str, ...] = tuple(dict.fromkeys(suffix for suffix, _ in HOST_CLIENTS))

# musicdl's own default sources — the ones upstream keeps most reliable.
DEFAULT_SOURCES = "MiguMusicClient,NeteaseMusicClient,QQMusicClient,KuwoMusicClient,QianqianMusicClient"

LOSSLESS_EXTENSIONS = {"flac", "wav", "alac", "ape", "wv"}

# Fields that must not cross a JSON boundary: bulky raw API data, in-memory
# download buffers, nested episodes, and the cached save path (recomputed later).
PAYLOAD_SKIP_FIELDS = {"raw_data", "downloaded_contents", "episodes", "_save_path"}


def _quiet() -> contextlib.ExitStack:
    """musicdl draws rich progress bars; keep them out of the server console."""
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


@functools.lru_cache(maxsize=1)
def availability() -> str:
    """Empty string when musicdl can be imported, otherwise the reason it cannot."""
    try:
        import musicdl  # noqa: F401
    except Exception as exc:
        return f"musicdl is not available: {exc}"
    return ""


def client_name_for_url(value: str) -> str | None:
    """The musicdl client that handles this URL's platform, if any."""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    for suffix, client_name in HOST_CLIENTS:
        if host == suffix or host.endswith(f".{suffix}"):
            return client_name
    return None


@functools.lru_cache(maxsize=8)
def _music_client(sources: tuple[str, ...], work_dir: str) -> Any:
    from musicdl import musicdl

    with _quiet():
        return musicdl.MusicClient(
            music_sources=list(sources),
            init_music_clients_cfg={source: {"work_dir": work_dir} for source in sources},
        )


def get_music_client(sources: list[str], work_dir: str) -> Any:
    """A cached musicdl client for these sources, or MusicdlError when unusable."""
    problem = availability()
    if problem:
        raise MusicdlError(problem)
    names = [name.strip() for name in sources if name and name.strip()]
    if not names:
        raise MusicdlError("No musicdl sources are configured.")
    try:
        return _music_client(tuple(names), work_dir)
    except Exception as exc:
        raise MusicdlError(f"musicdl could not start: {exc}") from exc


def parse_url(url: str, client_name: str, work_dir: str) -> list[Any]:
    """Parse a platform URL into musicdl SongInfo objects."""
    client = get_music_client([client_name], work_dir)
    with _quiet():
        song_infos = client.parseplaylist(url)
    if not song_infos:
        raise MusicdlError("That URL did not produce any tracks musicdl recognizes.")
    return list(song_infos)


def song_info_to_payload(song_info: Any) -> dict[str, Any]:
    """Reduce a SongInfo to a JSON-safe dict that survives the signed result token."""
    download_url = song_info.get("download_url")
    if not isinstance(download_url, str) or not download_url.startswith("http"):
        raise MusicdlError("This track has no direct download URL to store.")
    if str(song_info.get("protocol") or "HTTP").upper() != "HTTP":
        raise MusicdlError("This track needs a streaming protocol RequestCast cannot store.")
    payload = {
        key: value
        for key, value in song_info.todict().items()
        if key not in PAYLOAD_SKIP_FIELDS
    }
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise MusicdlError("This track's download data cannot be stored.") from exc
    return payload


def _download_song_info(song_info: Any, temp_dir: Path) -> Path:
    client = get_music_client([str(song_info.get("source") or "")], str(temp_dir))
    song_info["work_dir"] = str(temp_dir)
    song_info["_save_path"] = None
    with _quiet():
        downloaded = client.download(song_infos=[song_info])
    if not downloaded:
        raise MusicdlError("musicdl did not download the track.")
    path = Path(str(downloaded[0].save_path))
    if not path.is_file() or path.stat().st_size == 0:
        raise MusicdlError("musicdl finished but produced no audio file.")
    return path


def download_payload(payload: dict[str, Any], temp_dir: Path) -> Path:
    """Download a track from a payload stored by ``song_info_to_payload``."""
    problem = availability()
    if problem:
        raise MusicdlError(problem)
    from musicdl.modules.utils import SongInfo

    try:
        song_info = SongInfo.fromdict(payload)
    except Exception as exc:
        raise MusicdlError(f"The stored musicdl track data is unusable: {exc}") from exc
    return _download_song_info(song_info, temp_dir)


def _candidate_score(song_info: Any, wanted_title: str, duration_s: int, match_key: Callable[[Any], str]) -> tuple | None:
    title_key = match_key(song_info.get("song_name"))
    if not title_key or not wanted_title:
        return None
    if wanted_title != title_key and wanted_title not in title_key and title_key not in wanted_title:
        return None
    exact = wanted_title == title_key
    lossless = str(song_info.get("ext") or "").lower().lstrip(".") in LOSSLESS_EXTENSIONS
    try:
        duration_gap = abs(int(song_info.get("duration_s") or 0) - duration_s) if duration_s else 0
    except (TypeError, ValueError):
        duration_gap = 0
    return (exact, lossless, -min(duration_gap, 3600))


def search_and_download(
    artist: str,
    title: str,
    duration_s: int,
    sources: list[str],
    temp_dir: Path,
    match_key: Callable[[Any], str],
) -> Path:
    """Search the configured musicdl sources for this artist/title and download the best hit."""
    client = get_music_client(sources, str(temp_dir))
    keyword = " ".join(part for part in (artist.strip(), title.strip()) if part)
    if not keyword:
        raise MusicdlError("Nothing to search for.")
    with _quiet():
        results = client.search(keyword=keyword)
    wanted_title = match_key(title)
    wanted_artist = match_key(artist)
    best: tuple[tuple, Any] | None = None
    for per_source in results.values():
        for song_info in per_source:
            try:
                if not song_info.with_valid_download_url or not isinstance(song_info.download_url, str):
                    continue
            except Exception:
                continue
            candidate_artist = match_key(song_info.singers)
            if wanted_artist and not (
                wanted_artist == candidate_artist
                or wanted_artist in candidate_artist
                or (candidate_artist and candidate_artist in wanted_artist)
            ):
                continue
            score = _candidate_score(song_info, wanted_title, duration_s, match_key)
            if score is not None and (best is None or score > best[0]):
                best = (score, song_info)
    if best is None:
        raise MusicdlError("No musicdl source had a clean match for this track.")
    return _download_song_info(best[1], temp_dir)
