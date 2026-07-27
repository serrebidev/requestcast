"""Start RequestCast, open its interface, and collect support diagnostics."""

from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import traceback
from typing import Any, Callable

from requestcast import browser_shell, config, diagnostics, server
from requestcast.http_framing import BufferedClosingMiddleware, RequestCastHTTP10RequestHandler


class HttpServerController:
    """Small common interface for the Windows and non-Windows HTTP servers."""

    def __init__(
        self,
        backend: str,
        run: Callable[[], None],
        close: Callable[[], None],
    ) -> None:
        self.backend = backend
        self.run = run
        self.close = close


def selected_http_backend() -> str:
    """Use Werkzeug on Windows, with deterministic HTTP/1.0 framing."""
    return "werkzeug-threaded" if os.name == "nt" else "waitress"


def diagnostics_are_wanted(
    settings: dict[str, Any],
    argv: list[str],
    diagnostic_only: bool = False,
) -> bool:
    """Decide whether to collect the support bundle, which is off unless asked for.

    Diagnostics write tens of megabytes of Windows networking evidence beside the
    program, so they stay off until someone turns the preference on, passes
    ``--diagnose`` or ``--diagnostics``, or sets ``REQUESTCAST_DIAGNOSTICS=1``.
    ``--no-diagnostics`` and ``REQUESTCAST_DISABLE_DIAGNOSTICS=1`` always win.
    """
    if diagnostic_only:
        return True
    if "--no-diagnostics" in argv or os.environ.get("REQUESTCAST_DISABLE_DIAGNOSTICS") == "1":
        return False
    if "--diagnostics" in argv:
        return True
    return bool(settings.get("diagnostics_enabled"))


def tcp_port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Detect an existing listener even when its HTTP response body is stuck."""
    connect_host = "127.0.0.1" if server.is_loopback_host(host) or server.is_wildcard_host(host) else host
    try:
        connection = socket.create_connection((connect_host, port), timeout=max(timeout, 0.1))
    except OSError:
        return False
    try:
        return True
    finally:
        connection.close()


def show_startup_error(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    server.log_startup(message)
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "RequestCast", 0x10)
        except (AttributeError, OSError):
            pass


def create_http_server(app: Any, host: str, port: int) -> HttpServerController:
    """Create the dependable platform-specific HTTP server."""
    if os.name == "nt":
        from werkzeug.serving import make_server

        if server.is_loopback_host(host):
            bind_host = "127.0.0.1"
        elif server.is_wildcard_host(host):
            bind_host = "0.0.0.0"
        else:
            bind_host = host

        instance = make_server(
            bind_host,
            port,
            app,
            threaded=True,
            request_handler=RequestCastHTTP10RequestHandler,
        )

        def close_werkzeug() -> None:
            instance.shutdown()
            instance.server_close()

        server.log_startup(
            "Windows HTTP backend selected: Werkzeug threaded server; "
            f"bind={bind_host}:{port}; protocol=HTTP/1.0; framing=buffered-content-length; "
            "connection=close"
        )
        return HttpServerController(
            "werkzeug-threaded",
            instance.serve_forever,
            close_werkzeug,
        )

    from waitress import create_server

    bind_error: BaseException | None = None
    for bind_options in server.waitress_bind_candidates(host, port):
        try:
            instance = create_server(
                app,
                threads=6,
                channel_timeout=300,
                **bind_options,
            )
            server.log_startup(
                f"Waitress bind selected: {bind_options}; framing=buffered-content-length; "
                "connection=close"
            )
            return HttpServerController("waitress", instance.run, instance.close)
        except BaseException as exc:
            bind_error = exc
            server.log_startup(f"Waitress bind failed {bind_options}: {exc!r}")
    raise RuntimeError(f"Waitress could not bind: {bind_error}")


def main() -> int:
    if "--check-imports" in sys.argv:
        import openpyxl  # noqa: F401
        import pypdf  # noqa: F401
        import waitress  # noqa: F401
        import werkzeug.serving  # noqa: F401
        import requestcast.http_framing  # noqa: F401

        print("Packaged imports succeeded.")
        return 0

    settings = config.load()
    diagnostic_only = "--diagnose" in sys.argv
    diagnostics_enabled = diagnostics_are_wanted(settings, sys.argv, diagnostic_only)
    host = str(settings.get("bind_host") or config.DEFAULT_BIND_HOST)
    port = int(settings.get("bind_port") or config.DEFAULT_BIND_PORT)
    urls = server.browser_urls(host, port)
    launch_url = server.preferred_browser_url(host, port)

    port_open = tcp_port_is_open(host, port, timeout=0.5)
    if diagnostic_only and port_open:
        print(
            "A process is already listening on the RequestCast port. "
            "Collecting diagnostics without starting a second server.",
            flush=True,
        )
        diagnostics.collect_bundle(urls, duration=20.0, show_dialog=True)
        return 0

    diagnostics.install_exception_hooks()
    for profile in browser_shell.remove_legacy_browser_profiles():
        print(f"Reclaimed disk space from an old RequestCast browser profile: {profile}")
    if diagnostic_only:
        os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"

    from requestcast.app import app

    server.allow_loopback_http_sessions(app, host)
    if not getattr(app, "_requestcast_http_framing_wrapped", False):
        # Without diagnostics there is no request log, so nothing is written per request.
        app.wsgi_app = BufferedClosingMiddleware(
            app.wsgi_app,
            diagnostics.log_http if diagnostics_enabled else None,
            add_connection_close=os.name != "nt",
        )
        setattr(app, "_requestcast_http_framing_wrapped", True)

    print("RequestCast web interface:")
    for url in urls:
        print(f"  {url}")
    print(f"HTTP backend: {selected_http_backend()}")
    print("HTTP framing: HTTP/1.0, exact Content-Length, Connection: close")
    print(f"Startup diagnostics: {server.startup_log_path()}")
    if diagnostics_enabled:
        print(f"HTTP request log: {diagnostics.http_log_path()}")
        print(f"Full diagnostic ZIP: {diagnostics.diagnostics_zip_path()}")
    else:
        print("Diagnostics: off. Turn them on in Preferences, or start with --diagnostics.")

    if port_open:
        if server.requestcast_is_reachable(launch_url, timeout=1.5):
            print("RequestCast is already running. Opening the existing web interface.")
            if "--no-browser" not in sys.argv:
                browser_shell.launch_browser_when_ready(launch_url, timeout=2.0)
            if diagnostics_enabled:
                diagnostics.collect_bundle(urls, duration=10.0, show_dialog=False)
            return 0

        show_startup_error(
            f"Port {port} is already held by a process that accepts connections but does not "
            "complete RequestCast HTTP responses. Close every RequestCast.exe process in Task "
            "Manager, then start this copy again. RequestCast will not start a second server on "
            "the same port."
        )
        return 1

    try:
        http_server = create_http_server(app, host, port)
    except BaseException as exc:
        show_startup_error(f"RequestCast could not start its web server: {exc}")
        if diagnostics_enabled:
            diagnostics.collect_bundle(urls, duration=0.0, show_dialog=True)
        return 1

    server_errors: list[BaseException] = []

    def run_server() -> None:
        try:
            http_server.run()
        except BaseException as exc:
            server_errors.append(exc)
            server.log_startup(
                f"{http_server.backend} stopped with an error:\n" + traceback.format_exc()
            )

    server_thread = threading.Thread(
        target=run_server,
        name="requestcast-web-server",
        daemon=False,
    )
    server_thread.start()

    if diagnostic_only:
        print(
            f"A temporary RequestCast {http_server.backend} server was started for diagnostics.",
            flush=True,
        )
        diagnostics.collect_bundle(urls, duration=20.0, show_dialog=True)
        http_server.close()
        server_thread.join(timeout=5)
        return 0 if not server_errors else 1

    if not config.is_configured(settings):
        print("First run: the browser will open the setup page.")
    if "--no-browser" not in sys.argv:
        browser_shell.launch_browser_when_ready(launch_url)

    if diagnostics_enabled:
        diagnostics.start_background_collection(urls, duration=30.0)

    try:
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping RequestCast.")
        http_server.close()
        server_thread.join(timeout=5)

    if server_errors:
        show_startup_error(f"RequestCast's web server stopped: {server_errors[0]}")
        if diagnostics_enabled:
            diagnostics.collect_bundle(urls, duration=0.0, show_dialog=True)
        return 1
    server.log_startup(
        f"RequestCast {http_server.backend} server thread ended without a recorded exception."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
