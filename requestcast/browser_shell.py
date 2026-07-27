"""Verify and open RequestCast's local web interface on Windows.

RequestCast verifies that its real HTML page works and then hands the address to
Windows, which opens whichever browser the person has chosen as their default. It
never installs, downloads, or copies a browser, and never creates a browser
profile of its own — earlier versions did, which quietly consumed hundreds of
megabytes of disk space.
"""

from __future__ import annotations

import ctypes
import http.client
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import config, server


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
LOCAL_WEB_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _request_page(url: str, timeout: float = 3.0) -> tuple[int, dict[str, str], bytes]:
    """Fetch one local HTTP page without using environment or system proxies."""
    parsed = urlparse(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError(f"Unsupported local web address: {url}")
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=max(timeout, 0.1))
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Host": parsed.netloc,
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "RequestCast-web-interface-check",
            },
        )
        response = connection.getresponse()
        headers = {name.casefold(): value for name, value in response.getheaders()}
        body = response.read(128 * 1024)
        return response.status, headers, body
    finally:
        connection.close()


def probe_web_interface(url: str, timeout: float = 3.0, max_redirects: int = 5) -> tuple[bool, str]:
    """Verify that the actual RequestCast HTML page works, following local redirects."""
    current = url
    trace: list[str] = []
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "http" or host not in LOCAL_WEB_HOSTS:
            return False, f"Refused non-local redirect to {current}"
        try:
            status, headers, body = _request_page(current, timeout=timeout)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return False, f"GET {current} failed: {exc}"
        path = parsed.path or "/"
        trace.append(f"{path} returned HTTP {status}")
        if status in REDIRECT_STATUSES:
            location = headers.get("location", "").strip()
            if not location:
                return False, "; ".join(trace + ["redirect had no Location header"])
            current = urljoin(current, location)
            continue
        content_type = headers.get("content-type", "").casefold()
        text = body.decode("utf-8", errors="replace")
        if status == 200 and "text/html" in content_type and "RequestCast" in text:
            return True, "; ".join(trace + ["RequestCast HTML verified"])
        snippet = " ".join(text[:300].split())
        detail = f"final Content-Type was {content_type or 'missing'}"
        if snippet:
            detail += f"; response began: {snippet}"
        return False, "; ".join(trace + [detail])
    return False, "; ".join(trace + ["too many redirects"])


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


def legacy_browser_profile_roots() -> list[Path]:
    """Return the private browser profiles that versions before 1.4 created."""
    names = ("requestcast-browser-profile", "requestcast-browser-profiles")
    roots: list[Path] = []
    seen: set[str] = set()
    for parent in (config.config_path().parent, config.app_dir()):
        for name in names:
            candidate = parent / name
            key = str(candidate).casefold()
            if key not in seen:
                seen.add(key)
                roots.append(candidate)
    return roots


def remove_legacy_browser_profiles() -> list[Path]:
    """Delete browser profiles an older RequestCast copied onto this machine.

    Those folders held a full Chromium user-data directory — frequently several
    hundred megabytes — for a browser the person never asked RequestCast to use.
    """
    removed: list[Path] = []
    for root in legacy_browser_profile_roots():
        if not root.is_dir():
            continue
        try:
            shutil.rmtree(root)
        except OSError as exc:
            server.log_startup(f"Could not remove the old browser profile {root}: {exc}")
            continue
        removed.append(root)
        server.log_startup(f"Removed an old RequestCast browser profile: {root}")
    return removed


def open_default_browser(url: str, shortcut: Path | None = None) -> bool:
    """Open RequestCast in whichever browser Windows is set to use by default."""
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
    """Wait for RequestCast, verify its HTML page, then open the default browser."""
    remove_legacy_browser_profiles()
    shortcut = server.create_browser_shortcut(url)
    server.log_startup(f"Waiting for RequestCast health endpoint: {url}")
    if not server.wait_for_server(url, timeout=timeout):
        message = f"RequestCast did not become reachable at {url}. Startup log: {server.startup_log_path()}"
        server.log_startup(message)
        print(message, flush=True)
        server.show_browser_failure(url, shortcut)
        return False

    server.log_startup(f"RequestCast health endpoint is ready: {url}")
    page_ok, page_detail = probe_web_interface(url)
    server.log_startup(f"RequestCast web page probe: {page_detail}")
    if not page_ok:
        message = f"RequestCast's health endpoint works, but its web page failed: {page_detail}"
        print(message, flush=True)
        server.show_browser_failure(url, shortcut)
        return False

    if open_default_browser(url, shortcut):
        extra = f" A manual shortcut is at {shortcut}." if shortcut else ""
        print(f"Your default browser was asked to open {url}.{extra}", flush=True)
        return True

    message = f"Windows could not open your default browser. Open {url} manually."
    if shortcut:
        message += f" You can also activate {shortcut}."
    server.log_startup(message)
    print(message, flush=True)
    server.show_browser_failure(url, shortcut)
    return False
