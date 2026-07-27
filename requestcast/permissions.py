"""Make finished downloads readable by everyone who uses the computer.

RequestCast builds each file inside a private temporary folder. On Windows a
folder created under ``AppData`` or inside the program folder carries an access
control list that only its creator can read, and a move keeps those entries, so
finished music arrived with permissions the person had to fix by hand every
time. On other systems the process umask can strip the group and other read
bits for the same reason.

Both are corrected here, right before a file becomes the person's music.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from subprocess import run as _run


# Bound at import so that tests and callers replacing ``subprocess.run`` — which they do
# to stand in for yt-dlp — never intercept a permission change.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# Well-known SIDs, used instead of names so this works on any language of Windows.
EVERYONE_SID = "*S-1-1-0"
USERS_SID = "*S-1-5-32-545"

FILE_MODE = 0o644
DIRECTORY_MODE = 0o755


def _icacls(arguments: list[str]) -> bool:
    """Run icacls quietly and report whether it succeeded."""
    try:
        result = _run(
            ["icacls", *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _grant_windows_read(path: Path, *, directory: bool) -> bool:
    """Let every account on this computer read the item, and inherit from now on."""
    target = str(path)
    # Drop the private entries inherited from the temporary folder, then take the
    # download folder's own rules, so the file looks like any other file there.
    _icacls([target, "/reset"])
    _icacls([target, "/inheritance:e"])
    read = "(OI)(CI)(RX)" if directory else "(RX)"
    granted = _icacls([target, "/grant", f"{USERS_SID}:{read}"])
    # "Everyone" also covers accounts left out of the local Users group.
    granted = _icacls([target, "/grant", f"{EVERYONE_SID}:{read}"]) or granted
    return granted


def make_readable(path: Path | str) -> bool:
    """Give every user read access to one file or folder.

    Returns True when the permissions were changed, and False when the system
    refused. A refusal is never fatal: the download itself already succeeded.
    """
    path = Path(path)
    try:
        directory = path.is_dir()
    except OSError:
        return False
    if not directory and not path.is_file():
        return False
    if os.name == "nt":
        return _grant_windows_read(path, directory=directory)
    mode = DIRECTORY_MODE if directory else FILE_MODE
    try:
        os.chmod(path, mode)
    except OSError:
        return False
    return True


def readable_by_everyone(path: Path | str) -> bool:
    """True when other accounts can read this item. Used by the tests."""
    path = Path(path)
    if os.name == "nt":
        try:
            result = _run(
                ["icacls", str(path)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=60,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        listing = result.stdout.casefold()
        return "everyone:" in listing or "\\users:" in listing
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_IROTH) and bool(mode & stat.S_IRGRP)
