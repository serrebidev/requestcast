"""Locating and, on Windows, fetching the two external programs this app needs.

yt-dlp retrieves the audio and ffmpeg remuxes it. Neither is bundled: the Windows build
downloads them into a ``tools`` folder beside the executable on first run, and every other
platform is expected to install them through its package manager.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import requests

from . import config


YTDLP_WINDOWS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_LINUX_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
FFMPEG_WINDOWS_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
DOWNLOAD_TIMEOUT = (10, 300)

ProgressCallback = Callable[[str], None]


def tools_dir() -> Path:
    return config.app_dir() / "tools"


def _executable_names(name: str) -> Iterable[str]:
    if os.name == "nt":
        yield f"{name}.exe"
    yield name


def find_tool(name: str, configured: str = "") -> str:
    """Return a usable path for a tool: the configured one, our own copy, or one on PATH."""
    if configured and Path(configured).exists():
        return str(Path(configured))
    for filename in _executable_names(name):
        candidate = tools_dir() / filename
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    return found or ""


def missing_tools(settings: dict) -> list[str]:
    missing = []
    if not find_tool("yt-dlp", settings.get("ytdlp_path", "")):
        missing.append("yt-dlp")
    if not find_tool("ffmpeg", settings.get("ffmpeg_path", "")):
        missing.append("ffmpeg")
    return missing


def can_auto_install() -> bool:
    """Automatic installation is only offered where we know a reliable static build."""
    return os.name == "nt"


def _download(url: str, destination: Path, progress: ProgressCallback | None = None) -> Path:
    if progress:
        progress(f"Downloading {url.rsplit('/', 1)[-1]}")
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            handle.write(chunk)
    temporary.replace(destination)
    destination.chmod(destination.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return destination


def install_ytdlp(progress: ProgressCallback | None = None) -> str:
    url = YTDLP_WINDOWS_URL if os.name == "nt" else YTDLP_LINUX_URL
    name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    return str(_download(url, tools_dir() / name, progress))


def install_ffmpeg(progress: ProgressCallback | None = None) -> tuple[str, str]:
    """Fetch a static ffmpeg build and keep only ffmpeg and ffprobe from it."""
    if os.name != "nt":
        raise RuntimeError(
            "Install ffmpeg with your system package manager, for example "
            "'apt install ffmpeg' or 'brew install ffmpeg'."
        )
    if progress:
        progress("Downloading ffmpeg (about 80 MB)")
    response = requests.get(FFMPEG_WINDOWS_URL, timeout=DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    if progress:
        progress("Extracting ffmpeg and ffprobe")
    target = tools_dir()
    target.mkdir(parents=True, exist_ok=True)
    extracted: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for member in archive.infolist():
            filename = Path(member.filename).name.lower()
            if filename not in {"ffmpeg.exe", "ffprobe.exe"}:
                continue
            destination = target / filename
            with archive.open(member) as source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            extracted[filename] = str(destination)
    if "ffmpeg.exe" not in extracted or "ffprobe.exe" not in extracted:
        raise RuntimeError("The downloaded ffmpeg archive did not contain the expected programs.")
    return extracted["ffmpeg.exe"], extracted["ffprobe.exe"]


def install_missing(settings: dict, progress: ProgressCallback | None = None) -> dict[str, str]:
    """Install whatever is missing and return the settings keys that should be updated."""
    updates: dict[str, str] = {}
    missing = missing_tools(settings)
    if "yt-dlp" in missing:
        updates["ytdlp_path"] = install_ytdlp(progress)
    if "ffmpeg" in missing:
        ffmpeg_path, ffprobe_path = install_ffmpeg(progress)
        updates["ffmpeg_path"] = ffmpeg_path
        updates["ffprobe_path"] = ffprobe_path
    if progress:
        progress("Finished" if updates else "Nothing to install")
    return updates
