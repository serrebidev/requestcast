"""Settings storage, first-run detection, and tool discovery.

Settings come from three places, in order of decreasing priority:

1. Environment variables (``REQUESTCAST_*``, or the legacy ``ADDTO_*`` names), which is
   how a server deployment is expected to be configured;
2. a JSON settings file, which is what the first-run setup page writes;
3. built-in defaults.

Nothing here is required at import time. If the program has never been configured it
starts in setup mode and the web interface asks for what it needs.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any


APP_NAME = "requestcast"
DEFAULT_STATION_ID = "1"
DEFAULT_UPLOAD_DIRECTORY = "Requests"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8797
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Numeric settings are clamped to what the program can actually honour, so a hand-edited
# settings file or a stray environment variable cannot ask for 10,000 search results or a
# retry delay measured in days. (key: (minimum, maximum)).
NUMERIC_LIMITS: dict[str, tuple[int, int]] = {
    "search_result_limit": (5, 200),
    "download_retries": (0, 10),
    "download_retry_delay": (0, 600),
    "download_gap_seconds": (0, 120),
    "rate_limit_cooldown": (0, 3600),
    "job_retry_limit": (0, 10),
    "auto_update_interval_hours": (1, 720),
}

# Written by the setup page; read by everything else.
FIELDS: dict[str, Any] = {
    "download_dir": "",
    "state_dir": "",
    "azuracast_enabled": False,
    "azuracast_api_base": "",
    "azuracast_api_key": "",
    "azuracast_station_id": DEFAULT_STATION_ID,
    "azuracast_request_playlist_id": "",
    "azuracast_media_dir": "",
    "azuracast_upload_dir": DEFAULT_UPLOAD_DIRECTORY,
    "password_salt": "",
    "password_hash": "",
    "admin_password_salt": "",
    "admin_password_hash": "",
    "secret_key": "",
    "bind_host": DEFAULT_BIND_HOST,
    "bind_port": DEFAULT_BIND_PORT,
    "secure_cookies": True,
    "ytdlp_path": "",
    "ffmpeg_path": "",
    "ffprobe_path": "",
    # YouTube now answers with JavaScript challenges that yt-dlp cannot solve on its own,
    # so it hands them to an external JavaScript runtime. Deno is the one yt-dlp enables
    # by default; without it YouTube downloads lose formats and fail with 403.
    "deno_path": "",
    "deezer_arl": "",
    # musicdl is the fallback source between Deezer and YouTube, and also handles
    # the other platforms' URLs. Sources are musicdl client names, comma-separated.
    "musicdl_enabled": True,
    "musicdl_sources": "MiguMusicClient,NeteaseMusicClient,QQMusicClient,KuwoMusicClient,QianqianMusicClient",
    # How much a search brings back, per source and per result type. Each search page can
    # ask for a different number; this is the starting point the form offers.
    "search_result_limit": 50,
    # Searching musicdl as well as YouTube and Deezer finds far more, but every musicdl
    # source is queried in turn, so it is slower and stays opt-in.
    "search_musicdl": False,
    # Bulk downloads (a channel, a discography, a playlist import) are what trip YouTube's
    # rate limiting: a run of tracks fails with 403 or "video unavailable" even though the
    # same tracks download fine later. These four settings are the answer to that.
    "download_retries": 2,
    "download_retry_delay": 20,
    "download_gap_seconds": 2,
    "rate_limit_cooldown": 180,
    # A whole job that fails is put back in the queue this many times before it gives up.
    "job_retry_limit": 1,
    # yt-dlp and musicdl both break when they fall behind the sites they read, so they
    # keep themselves current unless someone turns that off.
    "auto_update_tools": True,
    "auto_update_interval_hours": 24,
    # Diagnostics write a large support bundle to disk, so they stay off until asked for.
    "diagnostics_enabled": False,
}

# Environment names are checked in order, so the modern name wins over the legacy one.
ENVIRONMENT_NAMES: dict[str, tuple[str, ...]] = {
    "download_dir": ("REQUESTCAST_DOWNLOAD_DIR",),
    "state_dir": ("REQUESTCAST_STATE_DIR", "ADDTO_STATE_DIR"),
    "azuracast_api_base": ("REQUESTCAST_AZURACAST_API_BASE", "ADDTO_AZURACAST_API_BASE"),
    "azuracast_api_key": ("REQUESTCAST_AZURACAST_API_KEY", "ADDTO_AZURACAST_API_KEY"),
    "azuracast_station_id": ("REQUESTCAST_STATION_ID",),
    "azuracast_request_playlist_id": ("REQUESTCAST_REQUEST_PLAYLIST_ID", "ADDTO_REQUEST_PLAYLIST_ID"),
    "azuracast_media_dir": ("REQUESTCAST_MEDIA_DIR", "ADDTO_MEDIA_DIR"),
    "azuracast_upload_dir": ("REQUESTCAST_UPLOAD_DIR",),
    "password_salt": ("REQUESTCAST_PASSWORD_SALT", "ADDTO_PASSWORD_SALT"),
    "password_hash": ("REQUESTCAST_PASSWORD_HASH", "ADDTO_PASSWORD_HASH"),
    "admin_password_salt": ("REQUESTCAST_ADMIN_PASSWORD_SALT",),
    "admin_password_hash": ("REQUESTCAST_ADMIN_PASSWORD_HASH",),
    "secret_key": ("REQUESTCAST_SECRET_KEY", "ADDTO_SECRET_KEY"),
    "bind_host": ("REQUESTCAST_BIND_HOST",),
    "bind_port": ("REQUESTCAST_BIND_PORT",),
    "secure_cookies": ("REQUESTCAST_SECURE_COOKIES",),
    "ytdlp_path": ("REQUESTCAST_YTDLP", "ADDTO_YTDLP"),
    "ffmpeg_path": ("REQUESTCAST_FFMPEG",),
    "ffprobe_path": ("REQUESTCAST_FFPROBE",),
    "deno_path": ("REQUESTCAST_DENO",),
    "deezer_arl": ("REQUESTCAST_DEEZER_ARL", "ADDTO_DEEZER_ARL"),
    "musicdl_enabled": ("REQUESTCAST_MUSICDL_ENABLED",),
    "musicdl_sources": ("REQUESTCAST_MUSICDL_SOURCES",),
    "search_result_limit": ("REQUESTCAST_SEARCH_LIMIT",),
    "search_musicdl": ("REQUESTCAST_SEARCH_MUSICDL",),
    "download_retries": ("REQUESTCAST_DOWNLOAD_RETRIES",),
    "download_retry_delay": ("REQUESTCAST_DOWNLOAD_RETRY_DELAY",),
    "download_gap_seconds": ("REQUESTCAST_DOWNLOAD_GAP",),
    "rate_limit_cooldown": ("REQUESTCAST_RATE_LIMIT_COOLDOWN",),
    "job_retry_limit": ("REQUESTCAST_JOB_RETRY_LIMIT",),
    "auto_update_tools": ("REQUESTCAST_AUTO_UPDATE_TOOLS",),
    "auto_update_interval_hours": ("REQUESTCAST_AUTO_UPDATE_HOURS",),
    "diagnostics_enabled": ("REQUESTCAST_DIAGNOSTICS",),
}

TRUTHY = {"1", "true", "yes", "on"}


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The folder the program lives in — the portable build keeps its data here."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _directory_is_writable(path: Path) -> bool:
    probe = path / f".{APP_NAME}-write-test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def user_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def config_path() -> Path:
    """Where settings live.

    An explicit path always wins. Otherwise a portable build keeps its settings beside
    the executable so the whole folder can be moved between machines, falling back to
    the per-user config directory when that folder is read-only.
    """
    override = os.environ.get("REQUESTCAST_CONFIG")
    if override:
        return Path(override).expanduser()
    beside_program = app_dir() / f"{APP_NAME}.json"
    if beside_program.exists():
        return beside_program
    if is_frozen() and _directory_is_writable(app_dir()):
        return beside_program
    return user_config_dir() / "config.json"


def default_download_dir() -> Path:
    if is_frozen() and _directory_is_writable(app_dir()):
        return app_dir() / "Downloads"
    return Path.home() / "Music" / "RequestCast"


def default_state_dir() -> Path:
    path = config_path().parent / "state"
    return path


def load_file(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _coerce(key: str, value: Any) -> Any:
    default = FIELDS[key]
    if isinstance(default, bool):
        return str(value).strip().lower() in TRUTHY if not isinstance(value, bool) else value
    if isinstance(default, int):
        try:
            return int(str(value).strip())
        except ValueError:
            return default
    return str(value).strip()


def clamp(key: str, value: Any) -> int:
    """Keep a numeric setting inside the range the program can honour."""
    lowest, highest = NUMERIC_LIMITS[key]
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        number = int(FIELDS[key])
    return max(lowest, min(highest, number))


def load() -> dict[str, Any]:
    """Merge defaults, the settings file, and the environment into one settings dict."""
    settings = dict(FIELDS)
    settings.update({key: value for key, value in load_file().items() if key in FIELDS})
    for key, names in ENVIRONMENT_NAMES.items():
        for name in names:
            if os.environ.get(name):
                settings[key] = os.environ[name]
                break
    # An API key supplied by the environment implies the server wants AzuraCast on.
    if settings["azuracast_api_key"] and not load_file().get("azuracast_enabled"):
        if any(os.environ.get(name) for name in ENVIRONMENT_NAMES["azuracast_api_key"]):
            settings["azuracast_enabled"] = True
    settings = {key: _coerce(key, value) for key, value in settings.items()}
    for key in NUMERIC_LIMITS:
        settings[key] = clamp(key, settings[key])
    if not settings["download_dir"]:
        settings["download_dir"] = str(default_download_dir())
    if not settings["state_dir"]:
        settings["state_dir"] = str(default_state_dir())
    if not settings["azuracast_station_id"]:
        settings["azuracast_station_id"] = DEFAULT_STATION_ID
    if not settings["azuracast_upload_dir"]:
        settings["azuracast_upload_dir"] = DEFAULT_UPLOAD_DIRECTORY
    return settings


def save(settings: dict[str, Any], path: Path | None = None) -> Path:
    """Write settings to disk, keeping the file readable only by this user."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: settings.get(key, default) for key, default in FIELDS.items()}
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    temporary.replace(path)
    return path


def is_configured(settings: dict[str, Any] | None = None) -> bool:
    """Setup is complete once we know where downloads go and have a session key."""
    settings = settings if settings is not None else load()
    return bool(settings.get("download_dir") and settings.get("secret_key"))


def new_secret_key() -> str:
    return secrets.token_urlsafe(48)
