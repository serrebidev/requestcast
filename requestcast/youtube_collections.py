"""Direct YouTube playlist and channel URL support for RequestCast."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse


YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}
CHANNEL_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}
CHANNEL_PREFIXES = {"channel", "c", "user"}
CHANNEL_TABS = {"videos", "shorts", "streams"}
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,100}")


def _parsed_url(value: str):
    return urlparse(value if "://" in value else f"https://{value}")


def _host(parsed) -> str:
    return (parsed.hostname or "").lower().removeprefix("www.")


def _segments(parsed) -> list[str]:
    return [segment for segment in parsed.path.split("/") if segment]


def _playlist_id(parsed) -> str:
    playlist_id = (parse_qs(parsed.query).get("list") or [""])[0]
    return playlist_id if PLAYLIST_ID_RE.fullmatch(playlist_id) else ""


def _channel_base(segments: list[str]) -> tuple[list[str], str] | None:
    if not segments:
        return None
    if segments[0].startswith("@") and len(segments[0]) > 1:
        base = segments[:1]
        next_index = 1
    elif segments[0] in CHANNEL_PREFIXES and len(segments) >= 2:
        base = segments[:2]
        next_index = 2
    else:
        return None
    tab = segments[next_index].lower() if len(segments) > next_index else "videos"
    if tab not in CHANNEL_TABS:
        tab = "videos"
    return base, tab


def collection_kind(value: str) -> str:
    parsed = _parsed_url(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return ""
    host = _host(parsed)
    if host not in YOUTUBE_HOSTS:
        return ""
    if _playlist_id(parsed):
        return "playlist"
    if host in CHANNEL_HOSTS and _channel_base(_segments(parsed)):
        return "channel"
    return ""


def canonical_collection_url(value: str) -> str:
    parsed = _parsed_url(value)
    kind = collection_kind(value)
    if kind == "playlist":
        return f"https://www.youtube.com/playlist?list={_playlist_id(parsed)}"
    if kind == "channel":
        base, tab = _channel_base(_segments(parsed)) or ([], "videos")
        return f"https://www.youtube.com/{'/'.join([*base, tab])}"
    raise RuntimeError("That YouTube URL is not a playlist or channel URL.")


def _video_id_from_value(value: Any) -> str:
    text = str(value or "").strip()
    if VIDEO_ID_RE.fullmatch(text):
        return text
    if text.startswith("/"):
        text = f"https://www.youtube.com{text}"
    parsed = _parsed_url(text)
    host = _host(parsed)
    if host == "youtu.be":
        segments = _segments(parsed)
        candidate = segments[0] if segments else ""
    elif host in YOUTUBE_HOSTS:
        segments = _segments(parsed)
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(segments) >= 2 and segments[0] in {"shorts", "embed", "live"}:
            candidate = segments[1]
        else:
            candidate = ""
    else:
        candidate = ""
    return candidate if VIDEO_ID_RE.fullmatch(candidate) else ""


def _entry_video_id(entry: dict[str, Any]) -> str:
    for key in ("id", "url", "webpage_url", "original_url"):
        video_id = _video_id_from_value(entry.get(key))
        if video_id:
            return video_id
    return ""


def _entry_collection_url(entry: dict[str, Any]) -> str:
    for key in ("webpage_url", "original_url", "url"):
        value = str(entry.get(key) or "")
        if value and collection_kind(value):
            return canonical_collection_url(value)
    entry_type = str(entry.get("_type") or "")
    entry_id = str(entry.get("id") or "")
    if entry_type in {"playlist", "multi_video"} and PLAYLIST_ID_RE.fullmatch(entry_id):
        return f"https://www.youtube.com/playlist?list={entry_id}"
    return ""


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def install() -> None:
    """Patch RequestCast's URL resolver and collection expander once app.py is loaded."""
    from . import app as app_module

    if getattr(app_module, "_youtube_collection_support_installed", False):
        return

    original_youtube_url_result = app_module.youtube_url_result
    original_expand_youtube = app_module.expand_youtube

    def is_youtube_collection_url(value: str) -> bool:
        return bool(collection_kind(value))

    def collection_result(value: str) -> dict[str, Any]:
        kind = collection_kind(value)
        collection_url = canonical_collection_url(value)
        info = app_module.ytdlp_json(collection_url, playlist_limit=1)
        entries = info.get("entries") or []
        artist = app_module.clean_text(
            str(info.get("uploader") or info.get("channel") or info.get("creator") or "")
        )
        cover = app_module.ytdlp_thumbnail(info)
        if not cover and entries and isinstance(entries[0], dict):
            cover = app_module.ytdlp_thumbnail(entries[0])
        count = _integer(info.get("playlist_count") or info.get("n_entries"))
        default_title = "YouTube channel" if kind == "channel" else "YouTube playlist"
        title = app_module.clean_text(str(info.get("title") or default_title)) or default_title
        payload = {
            "source": "youtube",
            "kind": kind,
            "id": _playlist_id(_parsed_url(collection_url)) or collection_url,
            "browse_id": _playlist_id(_parsed_url(collection_url)) or collection_url,
            "title": title,
            "artist": artist,
            "cover": cover,
            "collection_url": collection_url,
            "url_playlist": kind == "playlist",
        }
        count_label = f"{count} videos" if count else ""
        detail = " — ".join(part for part in (artist, count_label) if part)
        return {
            **payload,
            "detail": detail,
            "preview_type": "",
            "preview": "",
            "token": app_module.signer.dumps(payload),
        }

    def youtube_url_result(value: str) -> dict[str, Any]:
        if collection_kind(value):
            return collection_result(value)
        return original_youtube_url_result(value)

    def expand_collection(value: str) -> list[dict[str, Any]]:
        collection_url = canonical_collection_url(value)
        raw = app_module.ytdlp_json(
            collection_url, playlist_limit=app_module.MAX_COLLECTION_TRACKS
        )
        fallback_artist = app_module.clean_text(
            str(raw.get("uploader") or raw.get("channel") or raw.get("creator") or "")
        ) or "Unknown Artist"
        tracks: list[dict[str, Any]] = []
        seen: set[str] = set()
        expanded_collections: set[str] = {collection_url}

        def visit(container: dict[str, Any], depth: int = 0) -> None:
            local_artist = app_module.clean_text(
                str(container.get("uploader") or container.get("channel") or container.get("creator") or fallback_artist)
            ) or fallback_artist
            for entry in container.get("entries") or []:
                if len(tracks) >= app_module.MAX_COLLECTION_TRACKS:
                    return
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("entries"), list):
                    visit(entry, depth + 1)
                    if len(tracks) >= app_module.MAX_COLLECTION_TRACKS:
                        return
                video_id = _entry_video_id(entry)
                if video_id and video_id not in seen:
                    seen.add(video_id)
                    title = app_module.clean_youtube_title(
                        str(entry.get("track") or entry.get("title") or "Untitled")
                    )
                    artist = app_module.clean_text(
                        str(
                            entry.get("artist")
                            or entry.get("creator")
                            or entry.get("uploader")
                            or entry.get("channel")
                            or local_artist
                        )
                    ) or local_artist
                    tracks.append(
                        {
                            "source": "youtube",
                            "source_id": video_id,
                            "video_id": video_id,
                            "title": title,
                            "artist": artist,
                            "album": app_module.clean_text(str(entry.get("album") or "")),
                            "duration_seconds": _integer(entry.get("duration")),
                            "track_number": 0,
                            "disc_number": 0,
                            "year": "",
                            "isrc": "",
                            "cover": app_module.ytdlp_thumbnail(entry),
                        }
                    )
                    continue
                if depth >= 2:
                    continue
                nested_url = _entry_collection_url(entry)
                if not nested_url or nested_url in expanded_collections:
                    continue
                expanded_collections.add(nested_url)
                remaining = app_module.MAX_COLLECTION_TRACKS - len(tracks)
                nested = app_module.ytdlp_json(nested_url, playlist_limit=remaining)
                visit(nested, depth + 1)

        visit(raw)
        return tracks

    def expand_youtube_collection_url(value: str) -> list[dict[str, Any]]:
        return expand_collection(value)

    def expand_youtube(item: dict[str, Any]) -> list[dict[str, Any]]:
        collection_url = str(item.get("collection_url") or "")
        if collection_url:
            return expand_collection(collection_url)
        return original_expand_youtube(item)

    app_module.is_youtube_collection_url = is_youtube_collection_url
    app_module.expand_youtube_collection_url = expand_youtube_collection_url
    app_module.youtube_url_result = youtube_url_result
    app_module.expand_youtube = expand_youtube
    app_module._youtube_collection_support_installed = True
