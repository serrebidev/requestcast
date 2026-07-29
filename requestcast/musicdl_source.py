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
import os
import re
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests


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


def prepare_environment(base_dir: Path | str) -> None:
    """Point the XDG directories musicdl's logger uses at somewhere writable.

    musicdl creates its log directory (``platformdirs.user_log_dir``) the moment a
    client is constructed. On a hardened Linux service the account's home may be
    read-only, which turns that into ``[Errno 30] Read-only file system``. Existing
    XDG settings always win.
    """
    base = Path(base_dir) / "musicdl"
    for name, sub in (
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
    ):
        os.environ.setdefault(name, str(base / sub))


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


PAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = (10, 30)


def _meta_contents(html: str, *names: str) -> list[str]:
    """Content attributes of matching <meta> tags, in either attribute order."""
    found = []
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.IGNORECASE):
        name = re.search(r'(?:property|name)="([^"]+)"', tag, flags=re.IGNORECASE)
        content = re.search(r'content="([^"]*)"', tag, flags=re.IGNORECASE)
        if name and content and name.group(1).lower() in names:
            found.append(unescape(content.group(1)))
    return found


def artist_title_from_page(html: str) -> tuple[str, str]:
    """Best-effort ``(artist, title)`` from a track page's title and meta tags.

    Handles the common shapes, for example SoundCloud's
    ``Stream <title> by <artist> | Listen online for free on SoundCloud`` and its
    ``Listen to <title> by <artist> #np on #SoundCloud`` description.
    """
    candidates = (
        _meta_contents(html, "og:description")
        + _meta_contents(html, "twitter:description")
        + _meta_contents(html, "description")
    )
    title_tag = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_tag:
        candidates.append(unescape(title_tag.group(1)))
    for candidate in candidates:
        text = candidate.split("|")[0]
        text = re.sub(r"^(stream|listen to)\s+", "", text.strip(), flags=re.IGNORECASE)
        text = re.sub(r"#\w+.*$", "", text)
        text = re.sub(r"\s+on desktop and mobile\..*$", "", text, flags=re.IGNORECASE).strip()
        match = re.match(r"(?P<title>.+?)\s+by\s+(?P<artist>[^#|]+)$", text, flags=re.IGNORECASE)
        if match:
            return match.group("artist").strip(), match.group("title").strip()
    og_titles = _meta_contents(html, "og:title", "twitter:title")
    return "", og_titles[0].strip() if og_titles else ""


def _track_from_page(url: str, client_name: str, work_dir: str, match_key: Callable[[Any], str]) -> list[Any]:
    """A single-track page parseplaylist cannot handle: read the page, then search."""
    try:
        response = requests.get(url, headers={"User-Agent": PAGE_USER_AGENT}, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException:
        return []
    artist, title = artist_title_from_page(response.text)
    if not title:
        return []
    client = get_music_client([client_name], work_dir)
    with _quiet():
        results = client.search(keyword=" ".join(part for part in (artist, title) if part))
    best = _best_match(results, artist, title, 0, match_key)
    return [best] if best is not None else []


def parse_url(url: str, client_name: str, work_dir: str, match_key: Callable[[Any], str] | None = None) -> list[Any]:
    """Parse a platform URL into musicdl SongInfo objects."""
    client = get_music_client([client_name], work_dir)
    with _quiet():
        song_infos = client.parseplaylist(url)
    if not song_infos and match_key is not None:
        song_infos = _track_from_page(url, client_name, work_dir, match_key)
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


def search(keyword: str, sources: list[str], work_dir: str) -> dict[str, list[Any]]:
    """Search the configured platforms by name, as ``{source: [SongInfo, ...]}``.

    Used by the search page when musicdl is included as a source. Every configured
    platform is queried, so this is slower than the YouTube or Deezer searches.
    """
    keyword = keyword.strip()
    if not keyword:
        return {}
    client = get_music_client(sources, work_dir)
    try:
        with _quiet():
            results = client.search(keyword=keyword)
    except Exception as exc:
        raise MusicdlError(f"musicdl could not search: {exc}") from exc
    return {
        str(source): list(songs or [])
        for source, songs in (results or {}).items()
    }


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
    best = _best_match(results, artist, title, duration_s, match_key)
    if best is None:
        raise MusicdlError("No musicdl source had a clean match for this track.")
    return _download_song_info(best, temp_dir)


def _best_match(
    results: dict[str, list[Any]],
    artist: str,
    title: str,
    duration_s: int,
    match_key: Callable[[Any], str],
) -> Any | None:
    """The highest-scoring SongInfo across all sources, following the same clean
    title-match and artist-containment rules as the Deezer matcher."""
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
    return best[1] if best is not None else None
