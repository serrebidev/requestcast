"""Start RequestCast and open the interface in a browser."""

from __future__ import annotations

import sys
import threading
import webbrowser

from requestcast import config
from requestcast.app import app


def main() -> int:
    settings = config.load()
    host = str(settings.get("bind_host") or config.DEFAULT_BIND_HOST)
    port = int(settings.get("bind_port") or config.DEFAULT_BIND_PORT)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"

    print(f"RequestCast is running at {url}")
    if not config.is_configured(settings):
        print("First run: the browser will open the setup page.")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    from waitress import serve

    serve(app, host=host, port=port, threads=6, channel_timeout=300)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
