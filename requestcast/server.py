"""Server binding, health checks, diagnostics, and browser launching."""

from __future__ import annotations

import ctypes
import http.client
import os
import re
import shutil
import socket
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from . import config


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}


def normalize_host(host: str) -> str:
    """Return a normalized host name suitable for comparisons."""
    return str(host or "").strip().lower()


def is_loopback_host(host: str) -> bool:
    """True when the configured bind is limited to this computer."""
    return normalize_host(host) in LOOPBACK_HOSTS


def is_wildcard_host(host: str) -> bool:
    """True when the configured bind listens on network interfaces."""
    return normalize_host(host) in WILDCARD_HOSTS


def ipv6_loopback_available() -> bool:
    """Check whether this computer can bind the IPv6 loopback address."""
    if not socket.has_ipv6:
        return False
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(("::1", 0))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def ipv6_wildcard_available() -> bool:
    """Check whether this computer can bind an IPv6 wildcard socket."""
    if not socket.has_ipv6:
        return False
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(("::", 0))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def browser_urls(host: str, port: int) -> list[str]:
    """Return the addresses a person can use to open RequestCast."""
    if is_loopback_host(host) or is_wildcard_host(host):
        return [
            f"http://localhost:{port}/",
            f"http://127.0.0.1:{port}/",
        ]
    display_host = normalize_host(host)
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return [f"http://{display_host}:{port}/"]


def preferred_browser_url(host: str, port: int) -> str:
    """Return the most dependable address for automatic browser launching."""
    if is_loopback_host(host) or is_wildcard_host(host):
        return f"http://127.0.0.1:{port}/"
    return browser_urls(host, port)[0]


def waitress_bind_candidates(host: str, port: int) -> list[dict[str, Any]]:
    """Return Waitress bind choices in preferred-to-fallback order."""
    normalized = normalize_host(host)
    if is_loopback_host(normalized):
        candidates: list[dict[str, Any]] = []
        if ipv6_loopback_available():
            candidates.append({"listen": f"127.0.0.1:{port} [::1]:{port}"})
        candidates.append({"host": "127.0.0.1", "port": port})
        return candidates
    if is_wildcard_host(normalized):
        candidates = []
        if ipv6_wildcard_available():
            candidates.append({"listen": f"0.0.0.0:{port} [::]:{port}"})
        candidates.append({"host": "0.0.0.0", "port": port})
        return candidates
    return [{"host": host, "port": port}]


def waitress_bind_options(host: str, port: int) -> dict[str, Any]:
    """Return the preferred Waitress bind arguments."""
    return waitress_bind_candidates(host, port)[0]


def allow_loopback_http_sessions(flask_app: Any, host: str) -> None:
    """Disable Secure cookies for local HTTP, including older saved configurations."""
    if is_loopback_host(host):
        flask_app.config["SESSION_COOKIE_SECURE"] = False


def startup_log_path() -> Path:
    """Return the persistent startup diagnostic log path."""
    return config.config_path().parent / "requestcast-startup.log"


def log_startup(message: str) -> None:
    """Append one timestamped line to the startup diagnostic log."""
    path = startup_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _health_path(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/healthz" if base_path else "/healthz"
    return host, port, path


def requestcast_is_reachable(url: str, timeout: float = 1.5) -> bool:
    """Return True only when RequestCast's HTTP health endpoint responds successfully."""
    host, port, path = _health_path(url)
    connection = http.client.HTTPConnection(host, port, timeout=max(timeout, 0.1))
    try:
        connection.request("GET", path, headers={"Host": host, "User-Agent": "RequestCast-startup"})
        response = connection.getresponse()
        body = response.read(4096).decode("utf-8", errors="replace")
        return response.status == 200 and '"status"' in body and '"ok"' in body
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_for_server(url: str, timeout: float = 30.0, interval: float = 0.2) -> bool:
    """Wait until RequestCast's HTTP health endpoint responds."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        if requestcast_is_reachable(url, timeout=1.0):
            return True
        time.sleep(max(interval, 0.01))
    return False


def _extract_executable(command: str) -> Path | None:
    """Extract an executable path from a Windows shell-open command."""
    value = os.path.expandvars(str(command or "").strip())
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        executable = value[1:end] if end > 1 else ""
    else:
        match = re.match(r"(?i)^(.+?\.exe)(?:\s|$)", value)
        executable = match.group(1) if match else ""
    if not executable:
        return None
    path = Path(executable)
    return path if path.is_file() else None


def _registry_browser_commands() -> list[str]:
    """Read the current Windows HTTP browser association commands."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    commands: list[str] = []
    prog_ids: list[str] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
            if prog_id:
                prog_ids.append(str(prog_id))
    except OSError:
        pass

    prog_ids.extend(["http", "https"])
    for prog_id in prog_ids:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
                command, _ = winreg.QueryValueEx(key, "")
                if command:
                    commands.append(str(command))
        except OSError:
            continue
    return commands


def _known_browser_paths() -> Iterable[Path]:
    """Yield common browser executable locations on Windows."""
    names = ("msedge.exe", "chrome.exe", "brave.exe", "firefox.exe", "vivaldi.exe")
    for name in names:
        located = shutil.which(name)
        if located:
            yield Path(located)

    program_files = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relatives = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
        Path("Mozilla Firefox/firefox.exe"),
        Path("Vivaldi/Application/vivaldi.exe"),
    )
    for base in program_files:
        if not base:
            continue
        root = Path(base)
        for relative in relatives:
            yield root / relative


def browser_executables() -> list[Path]:
    """Return unique browser executables, starting with the registered default."""
    output: list[Path] = []
    seen: set[str] = set()
    for command in _registry_browser_commands():
        path = _extract_executable(command)
        if path is not None:
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                output.append(path)
    for path in _known_browser_paths():
        if path.is_file():
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                output.append(path)
    return output


def _launch_executable(executable: Path, url: str) -> bool:
    try:
        process = subprocess.Popen(
            [str(executable), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    time.sleep(0.5)
    return process.poll() in {None, 0}


def _shell_execute(url: str) -> bool:
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        return int(result) > 32
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _cmd_start(url: str) -> bool:
    if os.name != "nt":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["cmd.exe", "/d", "/s", "/c", "start", "", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=flags,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_default_browser(url: str) -> bool:
    """Open a URL through a real browser process, then use shell fallbacks."""
    if os.name == "nt":
        for executable in browser_executables():
            if _launch_executable(executable, url):
                log_startup(f"Browser launched directly: {executable}")
                return True
        if _shell_execute(url):
            log_startup("Browser launch requested through ShellExecuteW")
            return True
        if _cmd_start(url):
            log_startup("Browser launch requested through cmd.exe start")
            return True
    try:
        opened = bool(webbrowser.open(url, new=2))
    except Exception:
        opened = False
    if opened:
        log_startup("Browser launch requested through Python webbrowser")
    return opened


def create_browser_shortcut(url: str) -> Path | None:
    """Create an accessible Internet shortcut beside the app or its configuration."""
    content = f"[InternetShortcut]\nURL={url}\n"
    candidates = [config.app_dir(), config.config_path().parent]
    seen: set[str] = set()
    for directory in candidates:
        key = str(directory.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        path = directory / "Open RequestCast.url"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def show_browser_failure(url: str, shortcut: Path | None) -> None:
    """Show an accessible Windows error dialog when every browser launcher fails."""
    if os.name != "nt":
        return
    location = f"\n\nYou can also activate:\n{shortcut}" if shortcut else ""
    message = f"RequestCast is running, but Windows did not open a browser.\n\nOpen this address:\n{url}{location}"
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "RequestCast", 0x10)
    except (AttributeError, OSError):
        pass


def launch_browser_when_ready(url: str, timeout: float = 30.0) -> bool:
    """Wait for RequestCast, then launch a browser from the calling thread."""
    shortcut = create_browser_shortcut(url)
    log_startup(f"Waiting for RequestCast health endpoint: {url}")
    if not wait_for_server(url, timeout=timeout):
        message = f"RequestCast did not become reachable at {url}. Startup log: {startup_log_path()}"
        log_startup(message)
        print(message, flush=True)
        show_browser_failure(url, shortcut)
        return False
    log_startup(f"RequestCast health endpoint is ready: {url}")
    if open_default_browser(url):
        extra = f" A manual shortcut is at {shortcut}." if shortcut else ""
        print(f"Browser launch requested for {url}.{extra}", flush=True)
        return True
    message = f"Windows could not open a browser. Open {url} manually."
    if shortcut:
        message += f" You can also activate {shortcut}."
    log_startup(message)
    print(message, flush=True)
    show_browser_failure(url, shortcut)
    return False
