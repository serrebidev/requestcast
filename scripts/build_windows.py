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
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", NAME,
        # A folder build starts much faster than one-file and keeps the
        # tools folder and settings visibly beside the program.
        "--onedir",
        "--console",
        "--add-data", f"{ROOT / 'requestcast' / 'templates'}{separator}requestcast/templates",
        "--add-data", f"{ROOT / 'requestcast' / 'static'}{separator}requestcast/static",
        # Imported lazily by their libraries, so PyInstaller cannot see them.
        "--hidden-import", "waitress",
        "--hidden-import", "openpyxl",
        "--hidden-import", "pypdf",
        "--collect-all", "ytmusicapi",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        str(ROOT / "run.py"),
    ]
    print(" ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode

    target = ROOT / "dist" / NAME
    for extra in ("README.md", "LICENSE"):
        source = ROOT / extra
        if source.exists():
            shutil.copy2(source, target / extra)
    print(f"\nBuilt {target}")
    print(f"Run {target / (NAME + '.exe')} and the setup page opens in your browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
