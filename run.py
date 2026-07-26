"""Start RequestCast and open the interface in a browser."""

from __future__ import annotations

import sys
import threading
import traceback

from requestcast import config, server
from requestcast.app import app


def main() -> int:
    if "--check-imports" in sys.argv:
        import openpyxl  # noqa: F401
        import pypdf  # noqa: F401
        import waitress  # noqa: F401

        print("Packaged imports succeeded.")
        return 0

    settings = config.load()
    host = str(settings.get("bind_host") or config.DEFAULT_BIND_HOST)
    port = int(settings.get("bind_port") or config.DEFAULT_BIND_PORT)
    urls = server.browser_urls(host, port)
    launch_url = server.preferred_browser_url(host, port)

    server.allow_loopback_http_sessions(app, host)

    print("RequestCast web interface:")
    for url in urls:
        print(f"  {url}")
    print(f"Startup diagnostics: {server.startup_log_path()}")

    if server.requestcast_is_reachable(launch_url, timeout=0.75):
        print("RequestCast is already running. Opening the existing web interface.")
        if "--no-browser" not in sys.argv:
            server.launch_browser_when_ready(launch_url, timeout=2.0)
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

    if not config.is_configured(settings):
        print("First run: the browser will open the setup page.")
    if "--no-browser" not in sys.argv:
        server.launch_browser_when_ready(launch_url)

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
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
