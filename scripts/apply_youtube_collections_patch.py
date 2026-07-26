"""Install the YouTube collection compatibility hook in requestcast.app."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "requestcast" / "app.py"
IMPORT_BLOCK = """

# Keep direct playlist and channel URL support active for every entry point,
# including run.py, Waitress, Gunicorn, and Flask's application loader.
from .youtube_collections import install as _install_youtube_collection_support

_install_youtube_collection_support()
"""


def main() -> int:
    source = APP_PATH.read_text(encoding="utf-8")
    if "_install_youtube_collection_support" in source:
        print("YouTube collection support is already installed in requestcast.app.")
        return 0
    marker = "\n\napply_settings()\n"
    if marker not in source:
        raise RuntimeError("Could not find the requestcast.app startup marker.")
    APP_PATH.write_text(source.replace(marker, f"{IMPORT_BLOCK}{marker}", 1), encoding="utf-8")
    print("Installed YouTube collection support in requestcast.app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
