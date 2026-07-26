"""Build the portable Windows executable.

Produces ``dist/RequestCast/`` containing RequestCast.exe and everything it needs.
The folder is self-contained and can be copied to another machine; settings and the
downloaded tools are written beside the executable, so nothing is installed system-wide.

Usage:  python scripts/build_windows.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = "RequestCast"


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
        "RequestCast automatically creates RequestCast-Diagnostics.zip after startup.\n\n"
        "To collect a new bundle manually, run Collect RequestCast Diagnostics.cmd. "
        "The collector starts a temporary local server when RequestCast is not already running.\n\n"
        "Send the complete ZIP file. It excludes passwords, hashes, salts, API keys, session secrets, "
        "cookies, form contents, and Deezer ARL values.\n",
        encoding="utf-8",
    )

    print(f"\nBuilt {target}")
    print(f"Run {target / (NAME + '.exe')} and the setup page opens in your browser.")
    print(f"Run {diagnostic_launcher} to create a full support ZIP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
