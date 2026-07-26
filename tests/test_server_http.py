"""Exercise the real HTTP server and require complete, readable UI responses."""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def unused_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def fetch(url: str) -> tuple[bytes, object]:
    with urlopen(url, timeout=5) as response:
        body = response.read()
        declared = response.headers.get("Content-Length")
        if declared is not None:
            assert len(body) == int(declared), (
                f"incomplete response for {url}: received {len(body)} of {declared} bytes"
            )
        assert response.status == 200, (url, response.status)
        return body, response.headers


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(colour: str) -> float:
        channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


with tempfile.TemporaryDirectory(prefix="requestcast-http-") as temp_name:
    temp = Path(temp_name)
    port = unused_loopback_port()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("REQUESTCAST_", "ADDTO_"))
    }
    environment.update(
        {
            "REQUESTCAST_BIND_HOST": "127.0.0.1",
            "REQUESTCAST_BIND_PORT": str(port),
            "REQUESTCAST_CONFIG": str(temp / "config.json"),
            "REQUESTCAST_DISABLE_WORKER": "1",
            "REQUESTCAST_DOWNLOAD_DIR": str(temp / "downloads"),
            "REQUESTCAST_STATE_DIR": str(temp / "state"),
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "run.py"), "--no-browser"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"RequestCast stopped during startup: {output}")
            try:
                health, _ = fetch(base_url + "/healthz")
                if health == b'{"status":"ok"}\n':
                    break
            except (OSError, TimeoutError):
                time.sleep(0.1)
        else:
            raise AssertionError("RequestCast did not become ready")

        page, page_headers = fetch(base_url + "/setup")
        html = page.decode("utf-8")
        assert 'href="/assets/style.css"' in html
        assert '<meta name="viewport"' in html
        assert '<a class="skip-link" href="#main">' in html
        assert '<main id="main">' in html
        assert re.search(r"<h1>[^<]+</h1>", html), "the setup page needs one visible H1"
        input_ids = set(re.findall(r"<input[^>]+id=\"([^\"]+)\"", html))
        labelled_ids = set(re.findall(r"<label[^>]+for=\"([^\"]+)\"", html))
        assert input_ids <= labelled_ids, f"unlabelled inputs: {sorted(input_ids - labelled_ids)}"
        assert "default-src 'self'" in page_headers["Content-Security-Policy"]

        css_body, css_headers = fetch(base_url + "/assets/style.css")
        css = css_body.decode("utf-8")
        assert css_headers.get_content_type() == "text/css"
        assert len(css_body) > 2_000
        assert "color: #f4f7fb" in css
        assert "background: #10141a" in css
        assert "outline: 3px solid #ffd75e" in css
        assert "prefers-reduced-motion: reduce" in css
        assert 'input:not([type="checkbox"])' in css
        assert 'input[type="checkbox"]' in css
        assert contrast_ratio("#f4f7fb", "#10141a") >= 4.5
        assert contrast_ratio("#8fc7ff", "#10141a") >= 4.5
        assert contrast_ratio("#ffffff", "#1769aa") >= 4.5
        assert contrast_ratio("#ffd75e", "#10141a") >= 3.0
        print("complete_http_bodies=passed")
        print("readable_stylesheet=passed")
        print("colour_contrast=passed")
        print("accessible_page_structure=passed")
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
