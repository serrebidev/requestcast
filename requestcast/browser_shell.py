"""Launch RequestCast through Windows URL associations.

Windows Store and MSIX browsers must be activated through the Windows shell.
Launching their packaged executable directly can start a process without opening
or navigating a browser window.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import webbrowser
from pathlib import Path

from . import server


def _startfile(target: str | Path) -> bool:
    """Ask Windows to open a URL or shortcut with its registered handler."""
    if os.name != "nt":
        return False
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        return False
    try:
        startfile(str(target), "open")
        return True
    except TypeError:
        try:
            startfile(str(target))
            return True
        except OSError:
            return False
    except OSError:
        return False


def _shell_execute(target: str | Path) -> bool:
    """Open a target through ShellExecuteW, including packaged applications."""
    if os.name != "nt":
        return False
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "open",
            str(target),
            None,
            None,
            1,
        )
        return int(result) > 32
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _rundll32_url(url: str) -> bool:
    """Use Windows' FileProtocolHandler as a shell-association fallback."""
    if os.name != "nt":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.run(
            ["rundll32.exe", "url.dll,FileProtocolHandler", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=flags,
        )
        return process.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _explorer_open(target: str | Path) -> bool:
    """Ask Explorer to activate a URL or Internet shortcut."""
    if os.name != "nt":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["explorer.exe", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def open_default_browser(url: str, shortcut: Path | None = None) -> bool:
    """Open a URL without launching a registered browser executable directly."""
    if os.name == "nt":
        attempts: list[tuple[str, object]] = [
            ("Windows startfile URL association", lambda: _startfile(url)),
            ("Windows ShellExecuteW URL association", lambda: _shell_execute(url)),
            ("Windows FileProtocolHandler", lambda: _rundll32_url(url)),
        ]
        if shortcut is not None:
            attempts.extend(
                [
                    ("Explorer Internet shortcut", lambda: _explorer_open(shortcut)),
                    ("ShellExecuteW Internet shortcut", lambda: _shell_execute(shortcut)),
                ]
            )
        attempts.append(("Explorer URL association", lambda: _explorer_open(url)))

        for description, attempt in attempts:
            try:
                opened = bool(attempt())  # type: ignore[operator]
            except Exception:
                opened = False
            if opened:
                server.log_startup(f"Browser launch requested through {description}: {url}")
                return True

    try:
        opened = bool(webbrowser.open(url, new=2))
    except Exception:
        opened = False
    if opened:
        server.log_startup(f"Browser launch requested through Python webbrowser: {url}")
    return opened


def launch_browser_when_ready(url: str, timeout: float = 30.0) -> bool:
    """Wait for RequestCast, then activate the registered URL handler."""
    shortcut = server.create_browser_shortcut(url)
    server.log_startup(f"Waiting for RequestCast health endpoint: {url}")
    if not server.wait_for_server(url, timeout=timeout):
        message = f"RequestCast did not become reachable at {url}. Startup log: {server.startup_log_path()}"
        server.log_startup(message)
        print(message, flush=True)
        server.show_browser_failure(url, shortcut)
        return False

    server.log_startup(f"RequestCast health endpoint is ready: {url}")
    if open_default_browser(url, shortcut):
        extra = f" A manual shortcut is at {shortcut}." if shortcut else ""
        print(f"Windows URL handler launch requested for {url}.{extra}", flush=True)
        return True

    message = f"Windows could not open a browser. Open {url} manually."
    if shortcut:
        message += f" You can also activate {shortcut}."
    server.log_startup(message)
    print(message, flush=True)
    server.show_browser_failure(url, shortcut)
    return False
