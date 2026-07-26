"""Start RequestCast, open its interface, and collect support diagnostics."""

from __future__ import annotations

import sys
import threading
import traceback

from requestcast import browser_shell, config, diagnostics, server


def main() -> int:
    if "--check-imports" in sys.argv:
        import openpyxl  # noqa: F401
        import pypdf  # noqa: F401
        import waitress  # noqa: F401

        print("Packaged imports succeeded.")
        return 0

    diagnostic_only = "--diagnose" in sys.argv
    settings = config.load()
    host = str(settings.get("bind_host") or config.DEFAULT_BIND_HOST)
    port = int(settings.get("bind_port") or config.DEFAULT_BIND_PORT)
    urls = server.browser_urls(host, port)
    launch_url = server.preferred_browser_url(host, port)

    if diagnostic_only and server.requestcast_is_reachable(launch_url, timeout=1.0):
        print("Collecting diagnostics from the running RequestCast server.", flush=True)
        diagnostics.collect_bundle(urls, duration=20.0, show_dialog=True)
        return 0

    diagnostics.install_exception_hooks()

    from requestcast.app import app

    server.allow_loopback_http_sessions(app, host)
    if not getattr(app, "_requestcast_diagnostics_wrapped", False):
        app.wsgi_app = diagnostics.RequestLoggingMiddleware(app.wsgi_app)
        app._requestcast_diagnostics_wrapped = True

    print("RequestCast web interface:")
    for url in urls:
        print(f"  {url}")
    print(f"Startup diagnostics: {server.startup_log_path()}")
    print(f"HTTP request log: {diagnostics.http_log_path()}")
    print(f"Full diagnostic ZIP: {diagnostics.diagnostics_zip_path()}")

    if server.requestcast_is_reachable(launch_url, timeout=0.75):
        print("RequestCast is already running. Opening the existing web interface.")
        if "--no-browser" not in sys.argv:
            browser_shell.launch_browser_when_ready(launch_url, timeout=2.0)
        diagnostics.collect_bundle(urls, duration=10.0, show_dialog=False)
        return 0

    from waitress import create_server

    http_server = None
    bind_error: BaseException | None = None
    for bind_options in server.waitress_bind_candidates(host, port):
        try:
            http_server = create_server(
                app,
                threads=6,
                channel_timeout=300,
                send_bytes=18_000,
                **bind_options,
            )
            server.log_startup(f"Waitress bind selected: {bind_options}")
            break
        except BaseException as exc:
            bind_error = exc
            server.log_startup(f"Waitress bind failed {bind_options}: {exc!r}")

    if http_server is None:
        print(f"RequestCast could not start its web server: {bind_error}", file=sys.stderr)
        print(f"See {server.startup_log_path()} for startup details.", file=sys.stderr)
        diagnostics.collect_bundle(urls, duration=0.0, show_dialog=True)
        return 1

    server_errors: list[BaseException] = []

    def run_server() -> None:
        try:
            http_server.run()
        except BaseException as exc:
            server_errors.append(exc)
            server.log_startup("Waitress stopped with an error:\n" + traceback.format_exc())

    server_thread = threading.Thread(target=run_server, name="requestcast-web-server", daemon=False)
    server_thread.start()

    if diagnostic_only:
        print("A temporary RequestCast server was started for full diagnostics.", flush=True)
        diagnostics.collect_bundle(urls, duration=20.0, show_dialog=True)
        http_server.close()
        server_thread.join(timeout=5)
        return 0 if not server_errors else 1

    diagnostics.start_background_collection(urls, duration=30.0)

    if not config.is_configured(settings):
        print("First run: the browser will open the setup page.")
    if "--no-browser" not in sys.argv:
        browser_shell.launch_browser_when_ready(launch_url)

    try:
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping RequestCast.")
        http_server.close()
        server_thread.join(timeout=5)

    if server_errors:
        print(f"RequestCast's web server stopped: {server_errors[0]}", file=sys.stderr)
        print(f"See {server.startup_log_path()} for startup details.", file=sys.stderr)
        diagnostics.collect_bundle(urls, duration=0.0, show_dialog=True)
        return 1
    server.log_startup("RequestCast server thread ended without a recorded exception.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
