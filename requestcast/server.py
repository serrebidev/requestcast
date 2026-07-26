"""Server binding and browser-launch helpers for RequestCast."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import webbrowser
from typing import Any
from urllib.parse import urlparse


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def normalize_host(host: str) -> str:
    """Return a normalized host name suitable for comparisons."""
    return str(host or "").strip().lower()


def is_loopback_host(host: str) -> bool:
    """True when the configured bind is limited to this computer."""
    return normalize_host(host) in LOOPBACK_HOSTS


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


def browser_urls(host: str, port: int) -> list[str]:
    """Return the addresses a person can use to open RequestCast."""
    if is_loopback_host(host):
        return [
            f"http://localhost:{port}/",
            f"http://127.0.0.1:{port}/",
        ]
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return [f"http://{display_host}:{port}/"]


def preferred_browser_url(host: str, port: int) -> str:
    """Return the most dependable address for automatic browser launching."""
    if is_loopback_host(host) or normalize_host(host) in {"0.0.0.0", "::"}:
        return f"http://127.0.0.1:{port}/"
    return browser_urls(host, port)[0]


def waitress_bind_options(host: str, port: int) -> dict[str, Any]:
    """Build Waitress arguments, using both loopback address families when possible."""
    if not is_loopback_host(host):
        return {"host": host, "port": port}

    listeners = [f"127.0.0.1:{port}"]
    if ipv6_loopback_available():
        listeners.append(f"[::1]:{port}")
    return {"listen": " ".join(listeners)}


def allow_loopback_http_sessions(flask_app: Any, host: str) -> None:
    """Disable Secure cookies for local HTTP, including older saved configurations."""
    if is_loopback_host(host):
        flask_app.config["SESSION_COOKIE_SECURE"] = False


def wait_for_server(url: str, timeout: float = 30.0, interval: float = 0.2) -> bool:
    """Wait until the web server accepts a TCP connection."""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + max(timeout, 0.0)

    while time.monotonic() < deadline:
        try:
            connection = socket.create_connection((host, port), timeout=1.0)
        except OSError:
            time.sleep(max(interval, 0.01))
            continue
        connection.close()
        return True
    return False


def open_default_browser(url: str) -> bool:
    """Open a URL with the operating system's registered default browser."""
    if os.name == "nt":
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            try:
                startfile(url)
                return True
            except OSError:
                pass

        try:
            subprocess.Popen(
                ["rundll32.exe", "url.dll,FileProtocolHandler", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass

    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass

    if os.name == "nt":
        try:
            subprocess.Popen(
                ["explorer.exe", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    return False


def start_browser_launcher(url: str, timeout: float = 30.0) -> threading.Thread:
    """Open the browser after RequestCast is reachable, without blocking startup."""

    def launch() -> None:
        if not wait_for_server(url, timeout=timeout):
            print(
                f"The web server did not become reachable. Open {url} manually after checking the error above.",
                flush=True,
            )
            return
        if open_default_browser(url):
            print(f"Opened the default browser at {url}", flush=True)
            return
        print(f"Windows could not open the default browser. Open {url} manually.", flush=True)

    thread = threading.Thread(target=launch, name="browser-launcher", daemon=True)
    thread.start()
    return thread
