# Copyright (c) serrebidev and contributors
# This file is part of RequestCast.
# SPDX-License-Identifier: MIT

"""Soulseek search and download for RequestCast.

A focused port of blindDL's Soulseek backend: one private asyncio loop owns a
single ``aioslsk`` client for the whole process, and the small synchronous
functions below bridge RequestCast's worker threads into that loop. Peer
searches, remote queues, progress, and the library share are handled here;
chat, browsing, and friends are blindDL GUI features and are deliberately not
carried across.

aioslsk is optional: everything degrades to a clear ``SoulseekError`` when the
package is not installed, so a RequestCast install without it still works.
"""

from __future__ import annotations

import asyncio
import functools
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import config

_IMPORT_ERROR: Exception | None = None

try:
    from aioslsk.client import SoulSeekClient
    from aioslsk.commands import GetUserStatsCommand
    from aioslsk.protocol.messages import (
        PeerTransferQueueFailed,
        PeerTransferReply,
    )
    from aioslsk.protocol.primitives import AttributeKey
    from aioslsk.settings import (
        CredentialsSettings,
        Settings,
        SharedDirectorySettingEntry,
    )
    from aioslsk.shares.cache import SharesShelveCache
    from aioslsk.shares.model import SharedDirectory
    from aioslsk.transfer.cache import TransferShelveCache
    from aioslsk.transfer.manager import TransferManager
    from aioslsk.transfer.model import FailReason, TransferDirection
    from aioslsk.transfer.state import TransferState
except Exception as exc:  # pragma: no cover - dependency is optional
    _IMPORT_ERROR = exc
    SoulSeekClient = None


SOURCE = "Soulseek"
HTTP_TIMEOUT_S = 30
SETTINGS_CHANGED_MESSAGE = "Soulseek settings changed during this transfer."


def available() -> bool:
    """True when aioslsk can be imported and Soulseek can be used at all."""
    return SoulSeekClient is not None


def import_error() -> str:
    return str(_IMPORT_ERROR) if _IMPORT_ERROR else ""


AUDIO_EXTENSIONS = frozenset(
    {
        "aac", "aiff", "alac", "ape", "flac", "m4a", "m4b", "mp3", "mpc",
        "ogg", "opus", "wav", "wma",
    }
)


class SoulseekError(RuntimeError):
    """A user-facing Soulseek connection, search, or transfer failure."""


class SoulseekDownloadCancelled(SoulseekError):
    """Raised when a cancellation aborts an aioslsk transfer."""


class SoulseekSettingsChanged(SoulseekError):
    """Raised when a client restart interrupts an in-flight transfer."""


# -- aioslsk cross-drive safety ---------------------------------------------


def _path_is_within(parent: str, candidate: str) -> bool:
    try:
        return os.path.commonpath((candidate, parent)) == parent
    except ValueError:
        return False


def _shared_directory_is_parent_of(self, directory) -> bool:
    path = directory if isinstance(directory, str) else directory.absolute_path
    return _path_is_within(self.absolute_path, path)


def _shared_directory_is_child_of(self, directory) -> bool:
    path = directory if isinstance(directory, str) else directory.absolute_path
    return _path_is_within(path, self.absolute_path)


def _shared_directory_items_for(self, directory) -> set:
    return {
        item
        for item in self.items
        if _path_is_within(directory.absolute_path, item.get_absolute_path())
    }


def _install_aioslsk_cross_drive_fix() -> None:
    """Stop aioslsk crashing when shared folders sit on different drives."""
    if not available():
        return
    SharedDirectory.is_parent_of = _shared_directory_is_parent_of
    SharedDirectory.is_child_of = _shared_directory_is_child_of
    SharedDirectory.get_items_for_directory = _shared_directory_items_for


# -- refusing uploads to peers who share nothing -----------------------------

_LEECHER_CACHE_SECONDS = 600.0
_leecher_guard: dict[str, Any] = {"enabled": True, "allowed": frozenset()}
_leecher_counts: dict[str, tuple[float, int]] = {}


def _set_leecher_guard(snapshot: dict[str, Any]) -> None:
    allowed = {str(name).casefold() for name in snapshot.get("friends", [])}
    allowed |= {str(name).casefold() for name in snapshot.get("priority_users", [])}
    _leecher_guard["enabled"] = bool(snapshot.get("block_leechers", True))
    _leecher_guard["allowed"] = frozenset(allowed)


async def _shared_file_count(username: str) -> int | None:
    key = username.casefold()
    cached = _leecher_counts.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _LEECHER_CACHE_SECONDS:
        return cached[1]
    client = _SERVICE._client
    if client is None:
        return None
    try:
        stats = await asyncio.wait_for(
            client(GetUserStatsCommand(username), response=True), timeout=15
        )
    except Exception:
        return None
    count = int(getattr(stats, "shared_file_count", 0) or 0)
    _leecher_counts[key] = (now, count)
    return count


async def _refuses_upload(connection) -> bool:
    username = getattr(connection, "username", "") or ""
    if not username or not _leecher_guard["enabled"]:
        return False
    if username.casefold() in _leecher_guard["allowed"]:
        return False
    count = await _shared_file_count(username)
    return count == 0


def _guarded_queue_handler(original):
    @functools.wraps(original)
    async def guarded_queue(self, message, connection):
        if await _refuses_upload(connection):
            connection.queue_message(
                PeerTransferQueueFailed.Request(
                    filename=message.filename,
                    reason=FailReason.FILE_NOT_SHARED,
                )
            )
            return None
        return await original(self, message, connection)

    guarded_queue._requestcast_guarded = True
    return guarded_queue


def _guarded_request_handler(original):
    @functools.wraps(original)
    async def guarded_request(self, message, connection):
        if message.direction == TransferDirection.UPLOAD.value and await (
            _refuses_upload(connection)
        ):
            connection.queue_message(
                PeerTransferReply.Request(
                    ticket=message.ticket,
                    allowed=False,
                    reason=FailReason.FILE_NOT_SHARED,
                )
            )
            return None
        return await original(self, message, connection)

    guarded_request._requestcast_guarded = True
    return guarded_request


def _install_leecher_guard() -> None:
    if not available():
        return
    if getattr(TransferManager._on_peer_transfer_queue, "_requestcast_guarded", False):
        return
    TransferManager._on_peer_transfer_queue = _guarded_queue_handler(
        TransferManager._on_peer_transfer_queue
    )
    TransferManager._on_peer_transfer_request = _guarded_request_handler(
        TransferManager._on_peer_transfer_request
    )


_install_aioslsk_cross_drive_fix()
_install_leecher_guard()


def _cache_dir(name: str = "soulseek") -> str:
    """A shelve cache directory private to the running interpreter.

    aioslsk stores its share index and transfer list with ``shelve``, whose
    ``dbm`` backend differs between Python versions. Each version gets its
    own directory so they never try to read each other's database.
    """
    version = f"py{sys.version_info.major}{sys.version_info.minor}"
    path = os.path.join(config.user_config_dir(), f"{name}-{version}")
    os.makedirs(path, exist_ok=True)
    return path


def _normal_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _config_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    """Copy only the Soulseek-relevant settings out of the full settings dict."""
    download_dir = str(settings.get("download_dir", "") or "").strip()
    return {
        "enabled": bool(settings.get("soulseek_enabled", False)),
        "username": str(settings.get("soulseek_username", "") or "").strip(),
        "password": str(settings.get("soulseek_password", "") or ""),
        "download_dir": os.path.abspath(download_dir) if download_dir else "",
        "share_downloads": bool(settings.get("soulseek_share_downloads", True)),
        "max_results": int(settings.get("soulseek_max_results", 500) or 500),
        "block_leechers": True,
        "friends": [],
        "priority_users": [],
    }


def _signature(snapshot: dict[str, Any]) -> tuple:
    return tuple((key, value) for key, value in sorted(snapshot.items()))


def _build_settings(snapshot: dict[str, Any]):
    if not available():
        raise SoulseekError(
            "The Soulseek backend could not be loaded"
            + (f" ({_IMPORT_ERROR})" if _IMPORT_ERROR else "")
            + ". Install aioslsk to use Soulseek."
        )
    if not snapshot["username"] or not snapshot["password"]:
        raise SoulseekError("Enter a Soulseek username and password in Preferences.")
    if not snapshot["download_dir"]:
        raise SoulseekError("Choose a download folder before enabling Soulseek.")

    os.makedirs(snapshot["download_dir"], exist_ok=True)
    settings = Settings(
        credentials=CredentialsSettings(
            username=snapshot["username"],
            password=snapshot["password"],
        )
    )
    settings.shares.download = snapshot["download_dir"]
    settings.shares.scan_on_start = False
    if snapshot["share_downloads"]:
        settings.shares.directories = [
            SharedDirectorySettingEntry(path=snapshot["download_dir"])
        ]
        settings.shares.scan_on_start = True
    settings.network.server.reconnect.auto = True
    settings.transfers.report_interval = 0.25
    return settings


def _format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return ""


def _format_speed(speed: int) -> str:
    return f"{_format_size(speed)}/s" if speed else ""


def _file_extension(file_data) -> str:
    extension = str(file_data.extension or "").strip().lstrip(".").lower()
    if not extension:
        extension = Path(os.path.basename(str(file_data.filename))).suffix.lstrip(".").lower()
    return extension


def _result_item(result, file_data) -> dict[str, Any]:
    attributes = file_data.get_attribute_map()
    duration = attributes.get(AttributeKey.DURATION)
    bitrate = attributes.get(AttributeKey.BITRATE)
    sample_rate = attributes.get(AttributeKey.SAMPLE_RATE)
    bit_depth = attributes.get(AttributeKey.BIT_DEPTH)
    extension = _file_extension(file_data)

    quality = extension.upper()
    details = []
    if bitrate:
        details.append(f"{bitrate} kbps")
    if bit_depth:
        details.append(f"{bit_depth}-bit")
    if sample_rate:
        details.append(f"{sample_rate / 1000:g} kHz")
    if details:
        quality = f"{quality}, {', '.join(details)}"

    availability = "free slot" if result.has_free_slots else "queued"
    if result.queue_size:
        availability += f", {result.queue_size} waiting"
    speed = _format_speed(result.avg_speed)
    if speed:
        availability += f", {speed} average"

    username = str(result.username)
    filename = str(file_data.filename)
    return {
        "title": os.path.basename(filename) or filename,
        "artist": username,
        "username": username,
        "remote_path": filename,
        "folder": os.path.dirname(filename),
        "format": quality,
        "extension": extension,
        "duration_s": int(duration) if duration else None,
        "size_bytes": int(file_data.filesize or 0),
        "file_size": _format_size(file_data.filesize),
        "has_free_slots": bool(result.has_free_slots),
        "average_speed": int(result.avg_speed or 0),
        "queue_size": int(result.queue_size or 0),
        "availability": availability,
    }


class _Service:
    """A single background asyncio loop that owns the Soulseek client."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._async_lock: asyncio.Lock | None = None
        self._client = None
        self._active_signature: tuple | None = None
        self._failed_signature: tuple | None = None
        self._failure: Exception | None = None

    def _ensure_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="requestcast-soulseek"
        )
        self._thread.start()
        self._loop_ready.wait(5)
        if self._loop is None:
            raise SoulseekError("Could not start the Soulseek background service.")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._async_lock = asyncio.Lock()
        self._loop_ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def _submit(self, coroutine):
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    async def _stop_client(self) -> None:
        client, self._client = self._client, None
        self._active_signature = None
        if client is not None:
            await client.stop()

    async def _configure(self, snapshot: dict[str, Any]):
        signature = _signature(snapshot)
        _set_leecher_guard(snapshot)
        async with self._async_lock:
            if not snapshot["enabled"]:
                await self._stop_client()
                self._failed_signature = None
                self._failure = None
                return None
            if self._client is not None and signature == self._active_signature:
                return self._client
            if signature == self._failed_signature and self._failure is not None:
                raise self._failure
            await self._stop_client()
            settings = _build_settings(snapshot)
            cache_dir = _cache_dir()
            client = SoulSeekClient(
                settings,
                shares_cache=SharesShelveCache(cache_dir),
                transfer_cache=TransferShelveCache(cache_dir),
            )
            try:
                await client.start()
                await client.login()
            except Exception as exc:
                try:
                    await client.stop()
                except Exception:
                    pass
                error = SoulseekError(str(exc) or exc.__class__.__name__)
                self._failed_signature = signature
                self._failure = error
                raise error from exc
            self._client = client
            self._active_signature = signature
            self._failed_signature = None
            self._failure = None
            return client

    def configure(self, config_dict: dict[str, Any], timeout: float = 30.0):
        snapshot = _config_snapshot(config_dict)
        return self._submit(self._configure(snapshot)).result(timeout=timeout)

    async def _search(self, snapshot, query, timeout_s, max_results):
        client = await self._configure(snapshot)
        if client is None:
            raise SoulseekError("Soulseek is not enabled.")
        request = await client.searches.search(query)
        deadline = asyncio.get_running_loop().time() + max(0.25, float(timeout_s))
        try:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
            items = [
                _result_item(result, file_data)
                for result in request.results
                for file_data in result.shared_items
                if _file_extension(file_data) in AUDIO_EXTENSIONS
            ]
            items.sort(
                key=lambda item: (
                    not item["has_free_slots"],
                    item["queue_size"],
                    -item["average_speed"],
                    item["title"].casefold(),
                )
            )
            return items[: max(1, int(max_results))]
        finally:
            try:
                client.searches.remove_request(request)
            except KeyError:
                pass

    def search(self, query, config_dict, timeout_s=12.0):
        snapshot = _config_snapshot(config_dict)
        if not snapshot["enabled"]:
            return []
        wait = max(15.0, float(timeout_s) + 30.0)
        return self._submit(
            self._search(snapshot, query, timeout_s, snapshot["max_results"])
        ).result(timeout=wait)

    async def _download(self, snapshot, item, target_dir, cancel_event):
        client = await self._configure(snapshot)
        if client is None:
            raise SoulseekError("Soulseek is not enabled.")
        transfer = await client.transfers.download(
            str(item["username"]), str(item["remote_path"])
        )
        if target_dir:
            safe_name = "".join(
                "_" if char in '<>:"/\\|?*' else char
                for char in os.path.basename(str(item["remote_path"]))
            ).rstrip(" .") or "download"
            transfer.local_path = os.path.join(str(target_dir), safe_name)
        while True:
            if client is not self._client:
                raise SoulseekSettingsChanged(SETTINGS_CHANGED_MESSAGE)
            if cancel_event is not None and cancel_event.is_set():
                try:
                    await client.transfers.abort(transfer)
                finally:
                    raise SoulseekDownloadCancelled()
            snapshot_now = transfer.take_progress_snapshot()
            state = snapshot_now.state
            if state == TransferState.COMPLETE:
                return transfer.local_path
            if state == TransferState.FAILED:
                reason = (
                    snapshot_now.fail_reason
                    or transfer.fail_reason
                    or "Transfer failed"
                )
                raise SoulseekError(str(reason))
            if state == TransferState.ABORTED:
                if cancel_event is not None and cancel_event.is_set():
                    raise SoulseekDownloadCancelled()
                reason = (
                    snapshot_now.abort_reason
                    or transfer.abort_reason
                    or "Transfer aborted"
                )
                raise SoulseekError(str(reason))
            await asyncio.sleep(0.25)

    def download(self, item, config_dict, target_dir=None, cancel_event=None):
        snapshot = _config_snapshot(config_dict)
        if not snapshot["enabled"]:
            raise SoulseekError("Soulseek is disabled in Preferences.")
        return self._submit(
            self._download(snapshot, item, target_dir, cancel_event)
        ).result()

    async def _shutdown(self):
        await self._stop_client()

    def shutdown(self):
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None or not thread.is_alive():
            return
        try:
            self._submit(self._shutdown()).result(timeout=15)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            self._loop = None
            self._thread = None


_SERVICE = _Service()


def verify_account(username: str, password: str, timeout: float = 30.0) -> None:
    """Sign in (or register an unused username) without sharing any files."""
    username = str(username or "").strip()
    password = str(password or "")
    if not username:
        raise SoulseekError("Enter a Soulseek username.")
    if not password:
        raise SoulseekError("Enter a Soulseek password.")
    asyncio.run(_verify_account_async(username, password, timeout))


async def _verify_account_async(username: str, password: str, timeout: float) -> None:
    if not available():
        raise SoulseekError(
            "The Soulseek backend could not be loaded"
            + (f" ({_IMPORT_ERROR})" if _IMPORT_ERROR else "")
            + "."
        )
    settings = Settings(
        credentials=CredentialsSettings(username=username, password=password)
    )
    settings.shares.scan_on_start = False
    settings.network.server.reconnect.auto = False
    settings.network.upnp.enabled = False
    ports: list[int] = []
    while len(ports) < 2:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in ports:
            ports.append(port)
    settings.network.listening.port = ports[0]
    settings.network.listening.obfuscated_port = ports[1]
    client = SoulSeekClient(settings)
    try:
        await asyncio.wait_for(client.start(), timeout=timeout)
        await asyncio.wait_for(client.login(), timeout=timeout)
    except Exception as exc:
        raise SoulseekError(str(exc) or exc.__class__.__name__) from exc
    finally:
        try:
            await client.stop()
        except Exception:
            pass


def configure(config_dict: dict[str, Any], timeout: float = 30.0):
    return _SERVICE.configure(config_dict, timeout=timeout)


def search(query: str, config_dict: dict[str, Any], timeout_s: float = 12.0):
    return _SERVICE.search(query, config_dict, timeout_s)


def download(item: dict[str, Any], config_dict: dict[str, Any], target_dir=None, cancel_event=None):
    return _SERVICE.download(item, config_dict, target_dir, cancel_event)


def shutdown() -> None:
    _SERVICE.shutdown()
