"""Detailed, privacy-conscious diagnostics for local RequestCast failures."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

from . import config, server


_SENSITIVE_WORDS = ("password", "secret", "token", "api_key", "arl", "hash", "salt")
_LOG_LOCK = threading.Lock()


def diagnostics_root() -> Path:
    """Return a writable location that is easy for the user to find."""
    for candidate in (config.app_dir(), config.config_path().parent, Path.cwd()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".requestcast-diagnostics-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path.cwd()


def diagnostics_zip_path() -> Path:
    return diagnostics_root() / "RequestCast-Diagnostics.zip"


def diagnostics_work_path() -> Path:
    return diagnostics_root() / "RequestCast-Diagnostics-Current"


def http_log_path() -> Path:
    return config.config_path().parent / "requestcast-http.log"


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def log_http(message: str) -> None:
    """Write a request event without query strings, cookies, or form contents."""
    path = http_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_timestamp()} {message}\n")
    except OSError:
        pass


class RequestLoggingMiddleware:
    """Record whether browser and diagnostic requests actually reach Waitress."""

    def __init__(self, application: Callable[..., Iterable[bytes]]) -> None:
        self.application = application

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        started = time.monotonic()
        method = str(environ.get("REQUEST_METHOD") or "?")
        path = str(environ.get("PATH_INFO") or "/")
        remote = str(environ.get("REMOTE_ADDR") or "?")
        host = str(environ.get("HTTP_HOST") or "?")
        user_agent = str(environ.get("HTTP_USER_AGENT") or "")[:300]
        status_holder = {"status": "no response"}

        def logged_start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
            status_holder["status"] = status
            return start_response(status, headers, exc_info)

        log_http(
            f"REQUEST start method={method} path={path!r} remote={remote!r} "
            f"host={host!r} user_agent={user_agent!r}"
        )
        try:
            response = self.application(environ, logged_start_response)
        except BaseException:
            log_http(
                f"REQUEST exception method={method} path={path!r} "
                f"elapsed_ms={(time.monotonic() - started) * 1000:.1f}\n{traceback.format_exc()}"
            )
            raise

        def response_iterator() -> Iterable[bytes]:
            total = 0
            try:
                for chunk in response:
                    total += len(chunk)
                    yield chunk
            except BaseException:
                log_http(f"RESPONSE iteration exception method={method} path={path!r}\n{traceback.format_exc()}")
                raise
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        log_http(f"RESPONSE close exception method={method} path={path!r}")
                log_http(
                    f"RESPONSE finish method={method} path={path!r} status={status_holder['status']!r} "
                    f"bytes={total} elapsed_ms={(time.monotonic() - started) * 1000:.1f}"
                )

        return response_iterator()


def install_exception_hooks() -> None:
    """Persist uncaught main-thread and worker-thread exceptions."""
    previous_main = sys.excepthook

    def main_hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        server.log_startup("UNCAUGHT MAIN THREAD EXCEPTION:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        previous_main(exc_type, exc, tb)

    sys.excepthook = main_hook
    previous_thread = getattr(threading, "excepthook", None)
    if previous_thread is not None:
        def thread_hook(args: Any) -> None:
            server.log_startup(
                f"UNCAUGHT THREAD EXCEPTION thread={getattr(args.thread, 'name', '?')!r}:\n"
                + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            )
            previous_thread(args)

        threading.excepthook = thread_hook


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(word in lowered for word in _SENSITIVE_WORDS)


def sanitized_settings(settings: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in sorted(settings.items()):
        output[key] = "[REDACTED]" if _is_sensitive_key(key) and value not in (None, "", False) else value
    return output


def sanitized_environment() -> dict[str, str]:
    prefixes = ("HTTP_", "HTTPS_", "ALL_PROXY", "NO_PROXY", "REQUESTCAST_", "ADDTO_")
    output: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if key.upper().startswith(prefixes):
            output[key] = "[REDACTED]" if _is_sensitive_key(key) else str(value)
    return output


def _known_secret_values() -> list[str]:
    values: list[str] = []
    for key, value in config.load().items():
        if _is_sensitive_key(key) and isinstance(value, str) and value:
            values.append(value)
    return sorted(values, key=len, reverse=True)


def _redact_text(text: str) -> str:
    output = str(text or "")
    for value in _known_secret_values():
        output = output.replace(value, "[REDACTED]")
    output = re.sub(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@", r"\1[REDACTED]@", output)
    output = re.sub(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|deezer[_-]?arl)\s*[:=]\s*([^\s;]+)",
        r"\1=[REDACTED]",
        output,
    )
    return output


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_redact_text(text), encoding="utf-8", errors="replace")


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(_redact_text(text))
        handle.flush()


def _run_command(path: Path, command: list[str], timeout: float = 30.0) -> None:
    header = f"COMMAND: {subprocess.list2cmdline(command)}\nSTART: {_timestamp()}\n\n"
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        body = (
            f"EXIT CODE: {result.returncode}\nEND: {_timestamp()}\n\n"
            f"--- STDOUT ---\n{result.stdout}\n\n--- STDERR ---\n{result.stderr}\n"
        )
    except BaseException:
        body = "COMMAND FAILED:\n" + traceback.format_exc()
    _write(path, header + body)


def _powershell(path: Path, script: str, timeout: float = 45.0) -> None:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
    _run_command(
        path,
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )


def _copy_text_if_exists(source: Path, destination: Path) -> None:
    try:
        if source.is_file():
            _write(destination, source.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        _write(destination.with_suffix(destination.suffix + ".error.txt"), traceback.format_exc())


def _safe_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    allowed = {
        "content-type", "content-length", "location", "server", "date", "connection",
        "cache-control", "x-content-type-options", "x-frame-options", "content-security-policy",
    }
    return {key.casefold(): value for key, value in headers if key.casefold() in allowed}


def _body_metadata(body: bytes) -> dict[str, Any]:
    decoded = body.decode("utf-8", errors="replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", decoded)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip()[:200] if title_match else ""
    return {
        "bytes_read": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "contains_requestcast": "requestcast" in decoded.casefold(),
        "contains_html": "<html" in decoded.casefold(),
        "title": title,
    }


def _http_request(url: str, *, timeout: float = 4.0) -> dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    started = time.monotonic()
    try:
        connection.request(
            "GET",
            path,
            headers={"Host": parsed.netloc or host, "User-Agent": "RequestCast-full-diagnostics/1", "Connection": "close"},
        )
        response = connection.getresponse()
        body = response.read(65536)
        return {
            "ok": True,
            "url": url,
            "status": response.status,
            "reason": response.reason,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "headers": _safe_headers(response.getheaders()),
            "body": _body_metadata(body),
        }
    except BaseException as exc:
        return {
            "ok": False,
            "url": url,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "error_type": type(exc).__name__,
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _http_redirect_chain(url: str, maximum: int = 6) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = url
    for _ in range(maximum):
        result = _http_request(current)
        chain.append(result)
        if not result.get("ok"):
            break
        status = int(result.get("status") or 0)
        location = str((result.get("headers") or {}).get("location") or "")
        if status not in {301, 302, 303, 307, 308} or not location:
            break
        current = urljoin(current, location)
    return chain


def _socket_probe(host: str, port: int) -> dict[str, Any]:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except BaseException as exc:
        return {"host": host, "port": port, "resolve_error": repr(exc)}
    attempts: list[dict[str, Any]] = []
    for family, socktype, protocol, _canonical, address in addresses:
        sock = socket.socket(family, socktype, protocol)
        sock.settimeout(3.0)
        started = time.monotonic()
        try:
            sock.connect(address)
            attempts.append({
                "address": repr(address), "family": family, "connected": True,
                "local_socket": repr(sock.getsockname()), "peer_socket": repr(sock.getpeername()),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            })
        except BaseException as exc:
            attempts.append({
                "address": repr(address), "family": family, "connected": False,
                "error": repr(exc), "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            })
        finally:
            sock.close()
    return {"host": host, "port": port, "attempts": attempts}


def _collect_http_timeline(directory: Path, urls: list[str], duration: float) -> None:
    path = directory / "network" / "probe-timeline.jsonl"
    deadline = time.monotonic() + max(duration, 0.0)
    sample = 0
    while True:
        sample += 1
        record: dict[str, Any] = {"timestamp": _timestamp(), "sample": sample, "urls": {}}
        for url in urls:
            parsed = urlparse(url)
            record["urls"][url] = {
                "socket": _socket_probe(parsed.hostname or "127.0.0.1", parsed.port or 80),
                "root_redirect_chain": _http_redirect_chain(url),
                "health": _http_request(urljoin(url, "/healthz")),
                "setup": _http_request(urljoin(url, "/setup")),
                "login": _http_request(urljoin(url, "/login")),
            }
        _append(path, json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if time.monotonic() >= deadline:
            break
        time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))


def _collect_commands(directory: Path, urls: list[str], port: int) -> None:
    commands = directory / "commands"
    basic_commands: list[tuple[str, list[str], float]] = [
        ("whoami.txt", ["whoami.exe", "/all"], 30),
        ("systeminfo.txt", ["systeminfo.exe"], 60),
        ("ipconfig-all.txt", ["ipconfig.exe", "/all"], 30),
        ("route-print.txt", ["route.exe", "print"], 30),
        ("arp-a.txt", ["arp.exe", "-a"], 30),
        ("netstat-ano.txt", ["netstat.exe", "-ano"], 30),
        ("winhttp-proxy.txt", ["netsh.exe", "winhttp", "show", "proxy"], 30),
        ("winsock-catalog.txt", ["netsh.exe", "winsock", "show", "catalog"], 60),
        ("portproxy.txt", ["netsh.exe", "interface", "portproxy", "show", "all"], 30),
        ("firewall-profiles.txt", ["netsh.exe", "advfirewall", "show", "allprofiles"], 30),
        ("loopback-exemptions.txt", ["CheckNetIsolation.exe", "LoopbackExempt", "-s"], 30),
        ("tasklist.txt", ["tasklist.exe"], 30),
        ("internet-settings.txt", ["reg.exe", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings", "/s"], 30),
        ("edge-policies.txt", ["reg.exe", "query", r"HKLM\Software\Policies\Microsoft\Edge", "/s"], 30),
        ("firefox-policies.txt", ["reg.exe", "query", r"HKLM\Software\Policies\Mozilla\Firefox", "/s"], 30),
    ]
    for filename, command, timeout in basic_commands:
        _run_command(commands / filename, command, timeout)

    scripts: list[tuple[str, str, float]] = [
        ("tcp-connections.txt", f"Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object {{$_.LocalPort -eq {port} -or $_.RemotePort -eq {port}}} | Format-List *", 45),
        ("network-adapters.txt", "Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Sort-Object Name | Format-List *", 45),
        ("ip-configuration.txt", "Get-NetIPConfiguration -All -Detailed -ErrorAction SilentlyContinue | Format-List *", 45),
        ("relevant-processes.txt", "$names=@('RequestCast.exe','msedge.exe','firefox.exe','chrome.exe','brave.exe','vivaldi.exe'); Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {$names -contains $_.Name} | Select-Object Name,ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate | Format-List *", 45),
        ("firewall-requestcast.txt", "Get-NetFirewallRule -ErrorAction SilentlyContinue | ForEach-Object { $rule=$_; $app=$rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue; if ($rule.DisplayName -match 'RequestCast|Python|Edge|Firefox' -or $app.Program -match 'RequestCast|python|msedge|firefox') {[pscustomobject]@{DisplayName=$rule.DisplayName;Enabled=$rule.Enabled;Direction=$rule.Direction;Action=$rule.Action;Profile=$rule.Profile;Program=$app.Program}} } | Format-List *", 90),
        ("appx-browsers.txt", "Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object {$_.Name -match 'Firefox|Edge|Chrome|Brave'} | Select-Object Name,PackageFullName,InstallLocation,Status | Format-List *", 45),
        ("antivirus-products.txt", "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction SilentlyContinue | Select-Object displayName,pathToSignedProductExe,productState | Format-List *", 45),
        ("recent-application-events.txt", "$since=(Get-Date).AddMinutes(-30); Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=$since} -ErrorAction SilentlyContinue | Where-Object {$_.ProviderName -match 'Application Error|Windows Error Reporting|Python|RequestCast'} | Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message | Format-List *", 90),
    ]
    for filename, script, timeout in scripts:
        _powershell(commands / filename, script, timeout)

    for index, url in enumerate(urls, 1):
        safe = "localhost" if "localhost" in url else "127-0-0-1"
        curl = shutil.which("curl.exe")
        if curl:
            _run_command(
                commands / f"curl-{index}-{safe}.txt",
                [curl, "-sS", "-L", "--noproxy", "*", "--connect-timeout", "5", "--max-time", "15", "-o", "NUL", "-w", "FINAL_URL=%{url_effective}\nHTTP_CODE=%{http_code}\nREMOTE_IP=%{remote_ip}\nREMOTE_PORT=%{remote_port}\nLOCAL_IP=%{local_ip}\nLOCAL_PORT=%{local_port}\nTOTAL_TIME=%{time_total}\n", url],
                20,
            )
        escaped = url.replace("'", "''")
        _powershell(
            commands / f"powershell-webrequest-{index}-{safe}.txt",
            "$ProgressPreference='SilentlyContinue'; try { "
            f"$r=Invoke-WebRequest -UseBasicParsing -Uri '{escaped}' -Proxy $null -TimeoutSec 15 -MaximumRedirection 5; "
            "$r | Select-Object StatusCode,StatusDescription,RawContentLength | Format-List *; "
            "'FINAL_URL=' + $r.BaseResponse.ResponseUri.AbsoluteUri; "
            "'CONTAINS_REQUESTCAST=' + ($r.Content -match 'RequestCast') "
            "} catch { $_ | Select-Object Exception,FullyQualifiedErrorId,CategoryInfo | Format-List *; exit 1 }",
            25,
        )


def _system_summary(settings: dict[str, Any], urls: list[str]) -> str:
    try:
        from . import __version__
    except Exception:
        __version__ = "unknown"
    lines = [
        "RequestCast full diagnostic bundle",
        f"Created: {_timestamp()}",
        f"RequestCast version: {__version__}",
        f"PID: {os.getpid()}",
        f"Parent PID: {os.getppid()}",
        f"Executable: {sys.executable}",
        f"Frozen build: {bool(getattr(sys, 'frozen', False))}",
        f"Working directory: {Path.cwd()}",
        f"Application directory: {config.app_dir()}",
        f"Configuration path: {config.config_path()}",
        f"Startup log: {server.startup_log_path()}",
        f"HTTP request log: {http_log_path()}",
        f"Platform: {platform.platform()}",
        f"Python: {sys.version}",
        f"Machine: {platform.machine()}",
        f"Processor: {platform.processor()}",
        f"URLs tested: {', '.join(urls)}",
        f"Configured bind: {settings.get('bind_host')}:{settings.get('bind_port')}",
        "",
        "Passwords, password hashes, salts, API keys, session secrets, cookies, form contents, and Deezer ARL values are not included.",
        "Send the entire RequestCast-Diagnostics.zip file. Do not extract and send individual files unless asked.",
    ]
    return "\n".join(lines) + "\n"


def collect_bundle(urls: list[str], *, duration: float = 20.0, show_dialog: bool = False) -> Path:
    """Collect independent Windows networking evidence and create one support ZIP."""
    settings = config.load()
    port = int(settings.get("bind_port") or config.DEFAULT_BIND_PORT)
    work = diagnostics_work_path()
    archive = diagnostics_zip_path()
    try:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        _write(work / "README-SEND-THIS-ZIP.txt", _system_summary(settings, urls))
        _write(work / "settings-redacted.json", json.dumps(sanitized_settings(settings), indent=2, sort_keys=True))
        _write(work / "environment-redacted.json", json.dumps(sanitized_environment(), indent=2, sort_keys=True))
        _collect_commands(work, urls, port)
        _collect_http_timeline(work, urls, duration)
        _copy_text_if_exists(server.startup_log_path(), work / "logs" / "requestcast-startup.log")
        _copy_text_if_exists(http_log_path(), work / "logs" / "requestcast-http.log")
        hosts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
        _copy_text_if_exists(hosts, work / "network" / "windows-hosts-file.txt")
        temporary = archive.with_suffix(".zip.tmp")
        temporary.unlink(missing_ok=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    zipped.write(path, path.relative_to(work))
        temporary.replace(archive)
        server.log_startup(f"Full diagnostic bundle created: {archive}")
        print(f"Full diagnostic bundle created: {archive}", flush=True)
    except BaseException:
        _write(work / "DIAGNOSTIC-COLLECTION-FAILED.txt", traceback.format_exc())
        server.log_startup("Diagnostic collection failed:\n" + traceback.format_exc())
    if show_dialog and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                f"RequestCast diagnostics were saved here:\n\n{archive}\n\nSend this ZIP file for analysis.",
                "RequestCast diagnostics",
                0x40,
            )
        except Exception:
            pass
    return archive


def start_background_collection(urls: list[str], *, duration: float = 20.0) -> threading.Thread:
    """Build a diagnostic ZIP after startup without blocking the web server."""
    thread = threading.Thread(
        target=collect_bundle,
        kwargs={"urls": urls, "duration": duration, "show_dialog": False},
        name="requestcast-diagnostic-collector",
        daemon=False,
    )
    thread.start()
    print(f"A full diagnostic ZIP is being collected at: {diagnostics_zip_path()}", flush=True)
    return thread
