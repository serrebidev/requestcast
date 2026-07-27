"""Build the portable Windows executable.

Produces ``dist/RequestCast/`` containing RequestCast.exe and everything it needs.
The folder is self-contained and can be copied to another machine; settings and the
downloaded tools are written beside the executable, so nothing is installed system-wide.

Usage:  python scripts/build_windows.py
"""

from __future__ import annotations

import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = "RequestCast"


def reserve_local_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def test_packaged_web_server(executable: Path, working_directory: Path) -> bool:
    """Require the packaged executable to deliver a fully framed setup page promptly."""
    port = reserve_local_port()
    with tempfile.TemporaryDirectory(prefix="requestcast-smoke-") as temporary_name:
        temporary = Path(temporary_name)
        environment = os.environ.copy()
        environment.update(
            {
                "REQUESTCAST_BIND_HOST": "127.0.0.1",
                "REQUESTCAST_BIND_PORT": str(port),
                "REQUESTCAST_CONFIG": str(temporary / "requestcast.json"),
                "REQUESTCAST_DISABLE_WORKER": "1",
                "REQUESTCAST_DISABLE_DIAGNOSTICS": "1",
            }
        )
        process = subprocess.Popen(
            [str(executable), "--no-browser", "--no-diagnostics"],
            cwd=working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        success = False
        error = "The packaged server did not become reachable."
        try:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    error = f"The packaged server exited early with code {process.returncode}."
                    break
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
                try:
                    connection.request(
                        "GET",
                        "/setup",
                        headers={"Connection": "close", "User-Agent": "RequestCast-build-smoke-test"},
                    )
                    response = connection.getresponse()
                    response_version = response.version
                    content_length = response.getheader("Content-Length")
                    connection_header = str(response.getheader("Connection") or "").casefold()
                    transfer_encoding = response.getheader("Transfer-Encoding")
                    body = response.read(128 * 1024)
                    if (
                        response.status == 200
                        and response_version == 10
                        and content_length == str(len(body))
                        and connection_header == "close"
                        and transfer_encoding is None
                        and len(body) > 1000
                        and b"Set up RequestCast" in body
                    ):
                        print(
                            f"Packaged web response succeeded: HTTP/1.0 {response.status}, "
                            f"Content-Length {content_length}, Connection close, "
                            f"{len(body)} bytes received from /setup."
                        )
                        success = True
                        break
                    error = (
                        f"Unexpected packaged web response: version={response_version}, "
                        f"HTTP={response.status}, Content-Length={content_length!r}, "
                        f"Connection={connection_header!r}, Transfer-Encoding={transfer_encoding!r}, "
                        f"bytes={len(body)}."
                    )
                except (OSError, http.client.HTTPException) as exc:
                    error = f"Packaged web request failed: {exc!r}"
                    time.sleep(0.2)
                finally:
                    connection.close()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)

        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)
        if not success:
            print(error, file=sys.stderr)
        return success


def main() -> int:
    if sys.platform != "win32":
        print("This build script targets Windows.", file=sys.stderr)
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is missing. Install it with: pip install pyinstaller", file=sys.stderr)
        return 1

    for stale in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(stale, ignore_errors=True)

    separator = ";"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        NAME,
        "--onedir",
        "--console",
        "--add-data",
        f"{ROOT / 'requestcast' / 'templates'}{separator}requestcast/templates",
        "--add-data",
        f"{ROOT / 'requestcast' / 'static'}{separator}requestcast/static",
        "--hidden-import",
        "waitress",
        "--hidden-import",
        "requestcast.http_framing",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "pypdf",
        "--hidden-import",
        "Crypto.Cipher.Blowfish",
        "--collect-all",
        "ytmusicapi",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "pytest",
        # RequestCast does not use NumPy. OpenPyXL treats it as optional, but
        # an incompatible NumPy copy in the build environment can otherwise be
        # collected and crash the executable before RequestCast starts.
        "--exclude-module",
        "numpy",
        str(ROOT / "run.py"),
    ]
    print(subprocess.list2cmdline(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode

    target = ROOT / "dist" / NAME

    bundled_numpy = [
        path
        for path in target.rglob("*")
        if path.name.lower() == "numpy"
        or path.name.lower().startswith("numpy.")
        or "numpy" in {part.lower() for part in path.parts}
    ]
    if bundled_numpy:
        print("The build unexpectedly contains NumPy:", file=sys.stderr)
        for path in bundled_numpy:
            print(f"  {path.relative_to(target)}", file=sys.stderr)
        return 1

    smoke_test = subprocess.run(
        [str(target / f"{NAME}.exe"), "--check-imports"],
        cwd=target,
        check=False,
        timeout=60,
        capture_output=True,
        text=True,
    )
    if smoke_test.stdout:
        print(smoke_test.stdout, end="")
    if smoke_test.stderr:
        print(smoke_test.stderr, end="", file=sys.stderr)

    smoke_output = f"{smoke_test.stdout}\n{smoke_test.stderr}"
    if "RequestsDependencyWarning" in smoke_output:
        print(
            "The packaged executable emitted a Requests dependency warning.",
            file=sys.stderr,
        )
        return 1
    if smoke_test.returncode != 0:
        print("The packaged executable failed its import smoke test.", file=sys.stderr)
        return smoke_test.returncode or 1

    if not test_packaged_web_server(target / f"{NAME}.exe", target):
        print(
            "The packaged executable failed to deliver a correctly framed setup page over HTTP.",
            file=sys.stderr,
        )
        return 1

    for extra in ("README.md", "LICENSE"):
        source = ROOT / extra
        if source.exists():
            shutil.copy2(source, target / extra)

    diagnostic_launcher = target / "Collect RequestCast Diagnostics.cmd"
    diagnostic_launcher.write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo RequestCast will collect detailed diagnostics and create RequestCast-Diagnostics.zip.\r\n"
        "echo The collector may take one or two minutes.\r\n"
        "echo.\r\n"
        "RequestCast.exe --diagnose\r\n"
        "echo.\r\n"
        "echo Send the RequestCast-Diagnostics.zip file from this folder.\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    diagnostic_readme = target / "DIAGNOSTICS.txt"
    diagnostic_readme.write_text(
        "RequestCast does not collect diagnostics unless you ask it to.\n\n"
        "To collect a bundle, run Collect RequestCast Diagnostics.cmd. The collector starts a "
        "temporary local server when RequestCast is not already running, writes "
        "RequestCast-Diagnostics.zip in this folder, and exits.\n\n"
        "To collect one on every start instead, tick the diagnostics box in Preferences, or run "
        "RequestCast.exe --diagnostics. That writes a request log and tens of megabytes of "
        "networking evidence each time, so leave it off unless you have been asked for it.\n\n"
        "Send the complete ZIP file. It excludes passwords, hashes, salts, API keys, session secrets, "
        "cookies, form contents, and Deezer ARL values.\n",
        encoding="utf-8",
    )

    print(f"\nBuilt {target}")
    print(f"Run {target / (NAME + '.exe')} and the setup page opens in your default browser.")
    print(f"Run {diagnostic_launcher} to create a full support ZIP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
