"""Check the YouTube preview iframe against a running instance.

Set REQUESTCAST_BASE_URL and REQUESTCAST_PASSWORD to point this at a running instance.
"""

import os

BASE = os.environ.get("REQUESTCAST_BASE_URL", "http://127.0.0.1:8797").rstrip("/")
PASSWORD = os.environ.get("REQUESTCAST_PASSWORD", "")

import html
import re

import requests


target = "https://www.youtube.com/watch?v=8QzLiHvt_EA"
client = requests.Session()
if PASSWORD:
    client.post(BASE + "/login", data={"password": PASSWORD}, timeout=30).raise_for_status()
page = client.get(BASE + "/", params={"q": target}, timeout=150)
page.raise_for_status()
assert page.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
iframe = re.search(r'<iframe[^>]+referrerpolicy="strict-origin-when-cross-origin"[^>]+src="([^"]+)"', page.text)
assert iframe
iframe_url = html.unescape(iframe.group(1))
assert "8QzLiHvt_EA" in iframe_url
embed = requests.get(iframe_url, headers={"Referer": BASE + "/"}, timeout=30)
embed.raise_for_status()
assert '"errorCode":153' not in embed.text
print(f"referrer_policy={page.headers['Referrer-Policy']}")
print(f"iframe_status={embed.status_code}")
print("error_153_present=false")
print("youtube_preview_http_test=passed")
