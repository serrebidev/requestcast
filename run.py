"""Start RequestCast and open the interface in a browser."""

from __future__ import annotations

import sys

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

    print("RequestCast is running at:")
    for url in urls:
        print(f"  {url}")
    if not config.is_configured(settings):
        print("First run: the browser will open the setup page.")
    if "--no-browser" not in sys.argv:
        print(f"Waiting for the web server before opening {launch_url}")
        server.start_browser_launcher(launch_url)

    from waitress import serve

    serve(
        app,
        threads=6,
        channel_timeout=300,
        send_bytes=18_000,
        **server.waitress_bind_options(host, port),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
