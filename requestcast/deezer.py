"""Download full audio directly from Deezer using a subscriber session.

Deezer's public API only serves 30 second previews. Full tracks come from the private
gateway the web player uses, authenticated with the account's ``arl`` cookie, and the
CDN stream arrives Blowfish-encrypted in chunks. This module signs in with the ARL,
asks for the best quality the account is allowed — FLAC, then 320 kbps MP3, then
128 kbps MP3 — and decrypts the stream.

The ARL is runtime configuration (``REQUESTCAST_DEEZER_ARL`` or the settings file);
it must never be committed to the repository.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, BinaryIO

import requests
from Crypto.Cipher import Blowfish


GATEWAY_URL = "https://www.deezer.com/ajax/gw-light.php"
MEDIA_URL = "https://media.deezer.com/v1/get_url"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
BLOWFISH_SECRET = b"g4el58wc0zvf9na1"
BLOWFISH_IV = b"\x00\x01\x02\x03\x04\x05\x06\x07"
# The CDN encrypts every third 2048-byte chunk.
CHUNK_SIZE = 2048
QUALITY_FORMATS = ("FLAC", "MP3_320", "MP3_128")
QUALITY_EXTENSIONS = {"FLAC": ".flac", "MP3_320": ".mp3", "MP3_128": ".mp3"}
HTTP_TIMEOUT = (10, 60)


class DeezerError(RuntimeError):
    """The Deezer session or download failed; the caller falls back to YouTube."""


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    """Read up to ``size`` bytes, tolerating the short reads a network stream gives."""
    parts = []
    remaining = size
    while remaining > 0:
        piece = stream.read(remaining)
        if not piece:
            break
        parts.append(piece)
        remaining -= len(piece)
    return b"".join(parts)


class DeezerClient:
    """A signed-in Deezer session able to fetch and decrypt full tracks."""

    def __init__(self, arl: str):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._session.cookies.set("arl", arl, domain=".deezer.com", path="/")
        self._api_token = "null"
        self._license_token = ""
        self.login()

    def login(self) -> None:
        """Validate the ARL and pick up fresh gateway and licence tokens."""
        data = self._gateway("deezer.getUserData")
        results = data.get("results") or {}
        user = results.get("USER") or {}
        if not int(user.get("USER_ID") or 0):
            raise DeezerError("The Deezer ARL did not sign in; it has probably expired.")
        options = user.get("OPTIONS") or {}
        self._license_token = str(options.get("license_token") or "")
        self._api_token = str(results.get("checkForm") or "null")
        if not self._license_token:
            raise DeezerError("The Deezer account did not return a licence token.")

    def _post_json(self, url: str, body: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._session.post(url, params=params, json=body, timeout=HTTP_TIMEOUT)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DeezerError(f"A Deezer request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise DeezerError("Deezer returned an unexpected response.")
        return data

    def _gateway(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self._post_json(
            GATEWAY_URL,
            params or {},
            params={
                "api_version": "1.0",
                "api_token": self._api_token or "null",
                "input": "3",
                "method": method,
            },
        )
        if data.get("error"):
            raise DeezerError(f"Deezer {method} failed: {data['error']}")
        return data

    def _track_token(self, track_id: str) -> tuple[str, str]:
        """The track's download token, plus a replacement ID for geo-blocked songs."""
        data = self._gateway("song.getData", {"SNG_ID": str(track_id)})
        results = data.get("results") or {}
        token = str(results.get("TRACK_TOKEN") or "")
        fallback = str((results.get("FALLBACK") or {}).get("SNG_ID") or "")
        if not token and not fallback:
            raise DeezerError(f"Deezer gave no download token for track {track_id}.")
        return token, fallback

    def _stream_url(self, track_token: str) -> tuple[str, str]:
        """The first (URL, format) the account is allowed, best quality first."""
        if not self._license_token:
            raise DeezerError("The Deezer session has no licence token.")
        for quality in QUALITY_FORMATS:
            body = {
                "license_token": self._license_token,
                "media": [{"type": "FULL", "formats": [{"cipher": "BF_CBC_STRIPE", "format": quality}]}],
                "track_tokens": [track_token],
            }
            data = self._post_json(MEDIA_URL, body)
            entries = data.get("data") or []
            medias = (entries[0] if entries else {}).get("media") or []
            for media in medias:
                for source in media.get("sources") or []:
                    url = str(source.get("url") or "")
                    if url.startswith("https://"):
                        return url, str(media.get("format") or quality)
        raise DeezerError("Deezer offered no downloadable quality for this track.")

    @staticmethod
    def blowfish_key(track_id: str) -> bytes:
        digest = hashlib.md5(str(track_id).encode("ascii")).hexdigest()
        return bytes(
            ord(digest[index]) ^ ord(digest[index + 16]) ^ BLOWFISH_SECRET[index]
            for index in range(16)
        )

    @classmethod
    def decrypt_stream(cls, track_id: str, source: BinaryIO, destination: BinaryIO) -> None:
        key = cls.blowfish_key(track_id)
        index = 0
        while True:
            chunk = _read_exact(source, CHUNK_SIZE)
            if not chunk:
                break
            if index % 3 == 0 and len(chunk) == CHUNK_SIZE:
                chunk = Blowfish.new(key, Blowfish.MODE_CBC, BLOWFISH_IV).decrypt(chunk)
            destination.write(chunk)
            index += 1

    def _open_stream(self, url: str) -> requests.Response:
        response = self._session.get(url, timeout=HTTP_TIMEOUT, stream=True)
        response.raise_for_status()
        response.raw.decode_content = True
        return response

    def _fetch(self, track_id: str, destination_dir: Path, depth: int = 0) -> tuple[Path, str]:
        token, fallback_id = self._track_token(track_id)
        if not token:
            if fallback_id and fallback_id != str(track_id) and depth < 1:
                return self._fetch(fallback_id, destination_dir, depth + 1)
            raise DeezerError(f"Deezer gave no download token for track {track_id}.")
        try:
            url, quality = self._stream_url(token)
        except DeezerError:
            if fallback_id and fallback_id != str(track_id) and depth < 1:
                return self._fetch(fallback_id, destination_dir, depth + 1)
            raise
        target = destination_dir / f"deezer-{track_id}{QUALITY_EXTENSIONS.get(quality, '.mp3')}"
        partial = target.with_suffix(target.suffix + ".part")
        try:
            with self._open_stream(url) as response:
                with partial.open("wb") as handle:
                    self.decrypt_stream(str(track_id), response.raw, handle)
        except (requests.RequestException, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise DeezerError(f"The Deezer stream download failed: {exc}") from exc
        if partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            raise DeezerError("Deezer delivered an empty stream.")
        partial.replace(target)
        return target, quality

    def download(self, track_id: str, destination_dir: Path) -> tuple[Path, str]:
        """Download the best available quality; returns ``(file, format)``.

        Gateway and licence tokens expire, so one re-login and retry is allowed.
        """
        try:
            return self._fetch(str(track_id), destination_dir)
        except DeezerError:
            self.login()
            return self._fetch(str(track_id), destination_dir)
