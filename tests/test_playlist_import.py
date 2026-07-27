"""Playlist uploads: M3U, PLS, XSPF, WPL, CUE, and foobar2000 FPL."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


def parse(name: str, data: bytes) -> list[dict[str, str]]:
    return app.parse_import_file(name, BytesIO(data))


# Extended M3U: the #EXTINF line names the artist and title, so the file path is not needed.
entries = parse(
    "favourites.m3u8",
    "#EXTM3U\n"
    "#EXTINF:222,ABBA - Dancing Queen\n"
    "D:\\Music\\ABBA\\01 - Dancing Queen.flac\n"
    "#EXTINF:189,Blondie - Heart of Glass\n"
    "/home/me/music/heart of glass.mp3\n"
    "#EXTINF:-1,https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n".encode("utf-8"),
)
assert {"artist": "ABBA", "title": "Dancing Queen"} in entries, entries
assert {"artist": "Blondie", "title": "Heart of Glass"} in entries, entries
assert {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"} in entries, entries
print("m3u8_playlist=passed")


# A plain M3U with no metadata falls back to the file names it lists.
entries = parse(
    "plain.m3u",
    b"C:\\Music\\Queen - Bohemian Rhapsody.mp3\n"
    b"..\\Pink Floyd - Money.flac\n"
    b"# a comment line\n",
)
assert {"artist": "Queen", "title": "Bohemian Rhapsody"} in entries, entries
assert {"artist": "Pink Floyd", "title": "Money"} in entries, entries
print("plain_m3u_playlist=passed")


# A leading track number in a file name is not part of the artist.
assert app.playlist_track_name("D:\\Music\\07 - Radiohead - Creep.flac") == "Radiohead - Creep"
assert app.playlist_track_name("file:///C:/Music/Nirvana%20-%20Lithium.mp3") == "Nirvana - Lithium"
print("playlist_track_name=passed")


entries = parse(
    "stations.pls",
    b"[playlist]\nNumberOfEntries=2\n"
    b"File1=C:\\Music\\unused.mp3\nTitle1=Fleetwood Mac - Dreams\nLength1=257\n"
    b"File2=C:\\Music\\Toto - Africa.mp3\n",
)
assert {"artist": "Fleetwood Mac", "title": "Dreams"} in entries, entries
assert {"artist": "Toto", "title": "Africa"} in entries, entries
print("pls_playlist=passed")


entries = parse(
    "list.xspf",
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<playlist version="1" xmlns="http://xspf.org/ns/0/"><trackList>'
    b"<track><location>file:///music/a.flac</location>"
    b"<creator>Daft Punk</creator><title>Around the World</title></track>"
    b"<track><location>file:///music/Air%20-%20La%20Femme%20d%27Argent.flac</location></track>"
    b"</trackList></playlist>",
)
assert {"artist": "Daft Punk", "title": "Around the World"} in entries, entries
assert {"artist": "Air", "title": "La Femme d'Argent"} in entries, entries
print("xspf_playlist=passed")


entries = parse(
    "library.wpl",
    b"<?wpl version=\"1.0\"?><smil><body><seq>"
    b"<media src=\"C:\\Music\\Portishead - Glory Box.mp3\"/>"
    b"<media src=\"C:\\Music\\Massive Attack - Teardrop.mp3\"/>"
    b"</seq></body></smil>",
)
assert {"artist": "Portishead", "title": "Glory Box"} in entries, entries
assert {"artist": "Massive Attack", "title": "Teardrop"} in entries, entries
print("wpl_playlist=passed")


entries = parse(
    "album.cue",
    b'PERFORMER "Miles Davis"\nTITLE "Kind of Blue"\nFILE "kob.flac" WAVE\n'
    b'  TRACK 01 AUDIO\n    TITLE "So What"\n    INDEX 01 00:00:00\n'
    b'  TRACK 02 AUDIO\n    TITLE "Blue in Green"\n    PERFORMER "Bill Evans"\n',
)
assert {"artist": "Miles Davis", "title": "So What"} in entries, entries
assert {"artist": "Bill Evans", "title": "Blue in Green"} in entries, entries
print("cue_playlist=passed")


# foobar2000 stores its references in a binary string table.
fpl = (
    b"\x01\x00\x00\x00fpl-header\x00"
    b"file://C:\\Music\\Boards of Canada - Roygbiv.flac\x00"
    b"\x7f\x00\x02"
    b"file://C:\\Music\\Aphex Twin - Xtal.flac\x00"
    b"meta\x00"
)
entries = parse("saved.fpl", fpl)
assert {"artist": "Boards of Canada", "title": "Roygbiv"} in entries, entries
assert {"artist": "Aphex Twin", "title": "Xtal"} in entries, entries
print("fpl_playlist=passed")


# XML playlists must not be able to pull in a document type definition.
try:
    parse(
        "evil.xspf",
        b'<?xml version="1.0"?><!DOCTYPE playlist [<!ENTITY a "boom">]>'
        b"<playlist><trackList><track><title>&a;</title></track></trackList></playlist>",
    )
except RuntimeError as error:
    assert "document type" in str(error), error
else:
    raise AssertionError("A playlist document type definition must be refused.")
print("xml_doctype_refused=passed")


# An unsupported extension is still refused, with a message that lists what works.
try:
    parse("music.exe", b"anything")
except RuntimeError as error:
    assert "M3U" in str(error), error
else:
    raise AssertionError("Unsupported file types must be refused.")
print("unsupported_extension_refused=passed")


assert ".m3u" in app.IMPORT_EXTENSIONS and ".fpl" in app.IMPORT_EXTENSIONS
print("playlist_import=passed")
