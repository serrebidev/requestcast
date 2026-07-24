from io import BytesIO
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


fixtures = [Path(value) for value in sys.argv[1:]]
assert fixtures, "Pass at least one TXT, XLSX, or PDF fixture."

expected = {
    "Future House.txt": 563,
    "Amy Master Playlist Gold.xlsx": 7440,
    "Amy Master Playlist Gold.pdf": 7440,
    "Amy Master Playlist Gold(1).pdf": 7440,
}

for fixture in fixtures:
    with fixture.open("rb") as source:
        entries = app.parse_import_file(fixture.name, source)
    assert len(entries) == expected[fixture.name], (fixture.name, len(entries))
    assert len({app.import_entry_key(entry) for entry in entries}) == len(entries)
    if fixture.suffix.lower() in {".xlsx", ".pdf"}:
        assert entries[0] == {"artist": "707", "title": "I Could Be Good For You"}
        assert {"artist": ".38 Special", "title": "Caught Up In You"} in entries
    else:
        assert entries[0] == {"url": "https://www.youtube.com/@alexslist/videos"}
        assert {"query": "2MUCH FUTURE"} in entries
    print(f"{fixture.name}: indexed={len(entries)}")

assert app.parse_txt_import(BytesIO(b"Performer\tSong\nExample Artist\tExample Title\nExample Artist\tExample Title\nArtist - Other Song\n")) == [
    {"artist": "Example Artist", "title": "Example Title"},
    {"artist": "Artist", "title": "Other Song"},
]
print("file_import_parsers=passed")
