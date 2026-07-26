"""Verify and open RequestCast's local web interface on Windows.

The default browser can be unable to reach loopback addresses when it is a Store
application, uses a forced proxy, has an HTTPS-only policy, or has an extension
that intercepts local requests. RequestCast therefore verifies the real HTML
page and then opens it in a clean Chromium window with proxying and HTTPS
upgrades disabled. Windows shell handlers remain as fallbacks.
"""

from __future__ import annotations

import ctypes
import http.client
import os
import shutil
import subprocess
import time
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


def _chromium_browser_paths() -> list[Path]:
    """Return installed Chromium browsers, preferring Microsoft Edge."""
    names = ("msedge.exe", "chrome.exe", "brave.exe", "vivaldi.exe")
    relatives = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
        Path("Vivaldi/Application/vivaldi.exe"),
    )
    candidates: list[Path] = []
    for name in names:
        located = shutil.which(name)
        if located:
            candidates.append(Path(located))
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        root = Path(base)
        candidates.extend(root / relative for relative in relatives)

    output: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        output.append(path)
    return output


def _launch_clean_chromium(url: str) -> Path | None:
    """Open a separate browser profile that cannot proxy or HTTPS-upgrade localhost."""
    if os.name != "nt":
        return None
    profile_root = config.config_path().parent / "requestcast-browser-profile"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    for executable in _chromium_browser_paths():
        profile = profile_root / executable.stem.casefold()
        try:
            profile.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                [
                    str(executable),
                    f"--user-data-dir={profile}",
                    "--no-proxy-server",
                    "--proxy-bypass-list=*",
                    "--disable-extensions",
                    "--no-first-run",
                    "--disable-first-run-ui",
                    "--disable-background-mode",
                    "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
                    "--new-window",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError:
            continue
        time.sleep(1.25)
        if process.poll() is None:
            return executable
    return None


def open_default_browser(url: str, shortcut: Path | None = None) -> bool:
    """Open RequestCast using a clean local-capable browser, then shell fallbacks."""
    if os.name == "nt":
        executable = _launch_clean_chromium(url)
        if executable is not None:
            server.log_startup(f"Clean no-proxy browser launched: {executable}; URL: {url}")
            return True

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
    """Wait for RequestCast, verify its HTML page, then open a clean browser."""
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
        print(f"A clean local browser launch was requested for {url}.{extra}", flush=True)
        return True

    message = f"Windows could not open a browser. Open {url} manually."
    if shortcut:
        message += f" You can also activate {shortcut}."
    server.log_startup(message)
    print(message, flush=True)
    server.show_browser_failure(url, shortcut)
    return False
