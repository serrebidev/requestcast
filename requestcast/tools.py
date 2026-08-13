"""Locating, fetching, and updating the external programs this app needs.

yt-dlp retrieves the audio, ffmpeg remuxes it, and Deno runs the JavaScript YouTube now
demands. None is bundled: they are downloaded into a ``tools`` folder beside the program
on first run. ffmpeg is fetched on Windows only; every other platform is expected to
install it through its package manager. yt-dlp and Deno are fetched everywhere, because
both are single self-contained binaries their projects publish for each platform.

Deno matters more than its size suggests. YouTube answers with JavaScript challenges that
yt-dlp's built-in interpreter can no longer solve, so yt-dlp hands them to an external
JavaScript runtime and enables Deno by default. Without one, YouTube downloads lose
formats and fail with 403 — which looks exactly like rate limiting but never clears.

yt-dlp and musicdl both read sites that change under them, so a copy that is a few weeks
old starts failing with 403s and "video unavailable" on downloads that used to work.
Both therefore keep themselves current: :func:`update_all` checks the published version,
installs a newer one when there is one, and reports what it did in plain language.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from . import config


YTDLP_WINDOWS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_LINUX_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
YTDLP_MACOS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
YTDLP_RELEASE_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
PYPI_RELEASE_API = "https://pypi.org/pypi/{name}/json"
FFMPEG_WINDOWS_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
DENO_RELEASE_API = "https://api.github.com/repos/denoland/deno/releases/latest"
DENO_DOWNLOAD_URL = "https://github.com/denoland/deno/releases/latest/download/{asset}"
# Deno publishes one zip per platform, each holding a single self-contained binary.
DENO_ASSETS: dict[tuple[str, str], str] = {
    ("win32", "x86_64"): "deno-x86_64-pc-windows-msvc.zip",
    ("win32", "aarch64"): "deno-aarch64-pc-windows-msvc.zip",
    ("linux", "x86_64"): "deno-x86_64-unknown-linux-gnu.zip",
    ("linux", "aarch64"): "deno-aarch64-unknown-linux-gnu.zip",
    ("darwin", "x86_64"): "deno-x86_64-apple-darwin.zip",
    ("darwin", "aarch64"): "deno-aarch64-apple-darwin.zip",
}
DOWNLOAD_TIMEOUT = (10, 300)
VERSION_TIMEOUT = (10, 30)
# Upgrading a package can compile dependencies, so it gets longer than a plain download.
UPGRADE_TIMEOUT = 900
UPDATE_STAMP_NAME = "tool-updates.json"

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
    if not find_tool("ffprobe", settings.get("ffprobe_path", "")):
        missing.append("ffprobe")
    if not find_tool("deno", settings.get("deno_path", "")):
        missing.append("deno")
    return missing


def deno_asset() -> str:
    """The Deno release file for this machine, or an empty string if there is none."""
    system = "win32" if os.name == "nt" else sys.platform
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64", "i686", "x86"}:
        machine = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        machine = "aarch64"
    return DENO_ASSETS.get((system, machine), "")


def installable_tools(settings: dict) -> list[str]:
    """Which missing tools this machine can fetch on its own."""
    missing = missing_tools(settings)
    available = []
    for name in missing:
        if name in {"ffmpeg", "ffprobe"} and os.name != "nt":
            continue
        if name == "deno" and not deno_asset():
            continue
        available.append(name)
    return available


def can_auto_install() -> bool:
    """Automatic installation is offered where we know a reliable static build.

    ffmpeg is Windows-only here; yt-dlp and Deno publish a binary for every platform
    RequestCast runs on, so those can always be fetched.
    """
    return os.name == "nt" or bool(deno_asset())


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
    if os.name == "nt":
        url, name = YTDLP_WINDOWS_URL, "yt-dlp.exe"
    elif sys.platform == "darwin":
        url, name = YTDLP_MACOS_URL, "yt-dlp"
    else:
        url, name = YTDLP_LINUX_URL, "yt-dlp"
    return str(_download(url, tools_dir() / name, progress))


def install_deno(progress: ProgressCallback | None = None) -> str:
    """Fetch the Deno binary YouTube downloads now depend on."""
    asset = deno_asset()
    if not asset:
        raise RuntimeError(
            "Deno does not publish a build for this machine. Install it with your package "
            "manager, or from https://deno.com, and YouTube downloads will use it."
        )
    if progress:
        progress("Downloading Deno (about 40 MB)")
    response = requests.get(DENO_DOWNLOAD_URL.format(asset=asset), timeout=DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    if progress:
        progress("Extracting Deno")
    target = tools_dir()
    target.mkdir(parents=True, exist_ok=True)
    wanted = "deno.exe" if os.name == "nt" else "deno"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        member = next(
            (item for item in archive.infolist() if Path(item.filename).name.lower() == wanted),
            None,
        )
        if member is None:
            raise RuntimeError("The downloaded Deno archive did not contain the program.")
        destination = target / wanted
        with archive.open(member) as source, destination.open("wb") as handle:
            shutil.copyfileobj(source, handle)
    destination.chmod(destination.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(destination)


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


def _run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a helper program without letting a console window flash up on Windows."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout,
        check=False, creationflags=creation_flags,
    )


def ytdlp_version(path: str) -> str:
    """The version string of an installed yt-dlp, or an empty string if it cannot run."""
    if not path:
        return ""
    try:
        result = _run([path, "--version"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().splitlines()[0].strip() if result.returncode == 0 and result.stdout.strip() else ""


def latest_ytdlp_version() -> str:
    """The newest yt-dlp release tag on GitHub, or an empty string if it cannot be read."""
    try:
        response = requests.get(
            YTDLP_RELEASE_API, timeout=VERSION_TIMEOUT,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return str(response.json().get("tag_name") or "").strip()
    except (requests.RequestException, ValueError):
        return ""


def deno_version(path: str) -> str:
    """The version of an installed Deno, for example ``2.5.4``."""
    if not path:
        return ""
    try:
        result = _run([path, "--version"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    # The first line reads "deno 2.5.4 (stable, release, x86_64-pc-windows-msvc)".
    first = (result.stdout or "").strip().splitlines()
    parts = first[0].split() if first else []
    return parts[1].strip() if len(parts) > 1 else ""


def latest_deno_version() -> str:
    """The newest Deno release on GitHub, without its leading ``v``."""
    try:
        response = requests.get(
            DENO_RELEASE_API, timeout=VERSION_TIMEOUT,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return str(response.json().get("tag_name") or "").strip().lstrip("v")
    except (requests.RequestException, ValueError):
        return ""


def installed_package_version(name: str) -> str:
    from importlib import metadata

    try:
        return metadata.version(name)
    except Exception:
        return ""


def latest_package_version(name: str) -> str:
    """The newest release of a package on PyPI, or an empty string if it cannot be read."""
    try:
        response = requests.get(PYPI_RELEASE_API.format(name=name), timeout=VERSION_TIMEOUT)
        response.raise_for_status()
        return str(response.json().get("info", {}).get("version") or "").strip()
    except (requests.RequestException, ValueError):
        return ""


def _version_parts(text: str) -> tuple[int, ...]:
    """A version as numbers, for comparison. Empty when it is not purely numeric."""
    pieces = str(text).strip().lstrip("v").split(".")
    if not pieces or not all(piece.isdigit() for piece in pieces):
        return ()
    return tuple(int(piece) for piece in pieces)


def is_outdated(current: str, latest: str) -> bool:
    """True when ``current`` is genuinely older than ``latest``.

    Never "update" something newer. yt-dlp nightlies carry a longer, higher version than
    the newest stable release (``2026.07.23.234303`` against ``2026.07.04``), and pip
    would happily replace one with the other.
    """
    if not current or not latest:
        return False
    here, there = _version_parts(current), _version_parts(latest)
    if here and there:
        return here < there
    return current != latest


def _result(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    """One tool's update outcome. ``status`` is updated, current, skipped, or failed."""
    return {"name": name, "status": status, "message": message, **extra}


def is_managed(path: str) -> bool:
    """True when this is our own copy in the tools folder, so we may replace it."""
    if not path:
        return False
    try:
        return Path(path).resolve().parent == tools_dir().resolve()
    except OSError:
        return False


def update_ytdlp(settings: dict, progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Bring yt-dlp up to the newest release.

    A yt-dlp somewhere else on the machine is never modified: it may be shared with other
    programs that depend on the version they have. When one of those falls behind,
    RequestCast fetches its own current copy into the tools folder and uses that instead.
    """
    current_path = find_tool("yt-dlp", settings.get("ytdlp_path", ""))
    if not current_path:
        if progress:
            progress("Installing yt-dlp")
        try:
            installed = install_ytdlp(progress)
        except Exception as exc:
            return _result("yt-dlp", "failed", f"yt-dlp could not be installed: {exc}")
        return _result("yt-dlp", "updated", f"Installed yt-dlp {ytdlp_version(installed) or 'latest'}.", path=installed)

    current = ytdlp_version(current_path)
    latest = latest_ytdlp_version()
    if not latest:
        return _result("yt-dlp", "skipped", "The newest yt-dlp release could not be looked up. Keeping the installed copy.")
    if current and not is_outdated(current, latest):
        return _result("yt-dlp", "current", f"yt-dlp {current} is already as new as the newest release ({latest}).")

    if progress:
        progress(f"Updating yt-dlp {current or 'unknown'} to {latest}")
    own_copy = is_managed(current_path)
    try:
        installed = install_ytdlp(progress)
    except Exception as exc:
        return _result("yt-dlp", "failed", f"yt-dlp {latest} could not be downloaded: {exc}")
    if own_copy:
        return _result("yt-dlp", "updated", f"Updated yt-dlp to {ytdlp_version(installed) or latest}.", path=installed)
    return _result(
        "yt-dlp", "updated",
        f"The yt-dlp at {current_path} is {current or 'an unknown version'}, older than {latest}. "
        f"RequestCast installed its own copy and will use that; yours was left alone.",
        path=installed,
    )


def _pip_target_writable() -> bool:
    """Whether pip can write into the interpreter that is running us.

    A deployment venv is commonly root-owned (so the service user cannot write
    into it) or made read-only by systemd hardening. pip then cannot upgrade a
    package in place, and the attempt fails with a read-only-filesystem error on
    every update cycle. Such a venv belongs to the deployment, which upgrades it
    from requirements.txt on deploy, so RequestCast leaves it alone instead.
    """
    try:
        import sysconfig

        target = sysconfig.get_paths().get("purelib", "") or sys.prefix
    except Exception:
        target = sys.prefix
    try:
        return bool(target) and os.access(target, os.W_OK)
    except OSError:
        return False


_READ_ONLY_MARKERS = (
    "read-only file system",
    "readonly file system",
    "permission denied",
    "operation not permitted",
    "[errno 30]",
    "[errno 13]",
)


def _pip_upgrade(name: str) -> dict[str, Any]:
    """Upgrade one package in the interpreter that is running us."""
    if config.is_frozen():
        return _result(
            name, "skipped",
            f"{name} is built into this portable copy of RequestCast and updates with it.",
        )
    if not _pip_target_writable():
        return _result(
            name, "skipped",
            f"{name} is installed in a read-only environment, so it updates with the "
            "deployment. RequestCast left it alone.",
        )
    command = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        "--disable-pip-version-check", "--no-input", name,
    ]
    try:
        result = _run(command, timeout=UPGRADE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return _result(name, "failed", f"{name} could not be upgraded: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "pip failed"
        if any(marker in message.casefold() for marker in _READ_ONLY_MARKERS):
            return _result(
                name, "skipped",
                f"{name} is installed in a read-only environment, so it updates with the "
                "deployment. RequestCast left it alone.",
            )
        return _result(name, "failed", f"{name} could not be upgraded: {message}")
    return _result(name, "updated", f"Upgraded {name} to {installed_package_version(name) or 'the newest release'}.")


def update_deno(settings: dict, progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Install or refresh Deno, the JavaScript runtime YouTube downloads depend on."""
    current_path = find_tool("deno", settings.get("deno_path", ""))
    if not current_path:
        if not deno_asset():
            return _result(
                "deno", "skipped",
                "Deno is not installed and publishes no build for this machine. "
                "Install it from https://deno.com so YouTube downloads keep working.",
            )
        if progress:
            progress("Installing Deno")
        try:
            installed = install_deno(progress)
        except Exception as exc:
            return _result("deno", "failed", f"Deno could not be installed: {exc}")
        return _result(
            "deno", "updated",
            f"Installed Deno {deno_version(installed) or 'latest'}. YouTube downloads can now "
            "answer the JavaScript challenges YouTube asks for.",
            path=installed,
        )

    current = deno_version(current_path)
    latest = latest_deno_version()
    if not latest:
        return _result("deno", "skipped", "The newest Deno release could not be looked up. Keeping the installed copy.")
    if current and not is_outdated(current, latest):
        return _result("deno", "current", f"Deno {current} is already as new as the newest release ({latest}).")

    if progress:
        progress(f"Updating Deno {current or 'unknown'} to {latest}")
    own_copy = is_managed(current_path)
    try:
        installed = install_deno(progress)
    except Exception as exc:
        return _result("deno", "failed", f"Deno {latest} could not be downloaded: {exc}")
    if own_copy:
        return _result("deno", "updated", f"Updated Deno to {deno_version(installed) or latest}.", path=installed)
    # A Deno installed by hand or by a package manager may be shared with other programs
    # that expect the version they have, so it is left exactly as it is.
    return _result(
        "deno", "updated",
        f"The Deno at {current_path} is {current or 'an unknown version'}, older than {latest}. "
        f"RequestCast installed its own copy and will use that; yours was left alone.",
        path=installed,
    )


def update_musicdl(settings: dict, progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Bring musicdl up to the newest release published on PyPI."""
    if not settings.get("musicdl_enabled", True):
        return _result("musicdl", "skipped", "musicdl support is turned off.")
    current = installed_package_version("musicdl")
    if not current:
        return _result("musicdl", "skipped", "musicdl is not installed.")
    latest = latest_package_version("musicdl")
    if not latest:
        return _result("musicdl", "skipped", "The newest musicdl release could not be looked up. Keeping the installed copy.")
    if not is_outdated(current, latest):
        return _result("musicdl", "current", f"musicdl {current} is already as new as the newest release ({latest}).")
    if progress:
        progress(f"Updating musicdl {current} to {latest}")
    return _pip_upgrade("musicdl")


def update_all(settings: dict, progress: ProgressCallback | None = None) -> list[dict[str, Any]]:
    """Update every tool that can update itself, reporting one outcome per tool."""
    results = [update_ytdlp(settings, progress)]
    results.append(update_deno(settings, progress))
    results.append(update_musicdl(settings, progress))
    if progress:
        progress("Finished checking for updates")
    return results


def update_stamp_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / UPDATE_STAMP_NAME


def last_update_check(state_dir: Path | str) -> float:
    """When the tools were last checked, as a Unix time. Zero means never."""
    try:
        with update_stamp_path(state_dir).open("r", encoding="utf-8") as handle:
            return float(json.load(handle).get("checked_at") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return 0.0


def record_update_check(state_dir: Path | str, results: list[dict[str, Any]]) -> None:
    """Remember that the tools were checked, and what came of it."""
    payload = {
        "checked_at": time.time(),
        "results": [
            {"name": item.get("name", ""), "status": item.get("status", ""), "message": item.get("message", "")}
            for item in results
        ],
    }
    path = update_stamp_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except OSError:
        pass


def install_missing(settings: dict, progress: ProgressCallback | None = None) -> dict[str, str]:
    """Install whatever is missing and return the settings keys that should be updated."""
    updates: dict[str, str] = {}
    missing = installable_tools(settings)
    if "yt-dlp" in missing:
        updates["ytdlp_path"] = install_ytdlp(progress)
    if "ffmpeg" in missing or "ffprobe" in missing:
        ffmpeg_path, ffprobe_path = install_ffmpeg(progress)
        updates["ffmpeg_path"] = ffmpeg_path
        updates["ffprobe_path"] = ffprobe_path
    if "deno" in missing:
        updates["deno_path"] = install_deno(progress)
    if progress:
        progress("Finished" if updates else "Nothing to install")
    return updates
