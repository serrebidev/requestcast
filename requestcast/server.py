"""Server binding and browser URL helpers for RequestCast."""

from __future__ import annotations

import socket
from typing import Any


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
