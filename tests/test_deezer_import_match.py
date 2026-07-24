from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app
from ytmusicapi import YTMusic


assert app.match_key("Dancing Queen (Official Video)") == "dancing queen"
assert app.match_key("Boys of Summer, The feat. Someone") == "boys of summer the"
assert app.match_key("Beyoncé") == "beyonce"
print("match_key_normalization=passed")

raw = app.find_deezer_song("ABBA", "Dancing Queen")
assert raw is not None, "Deezer should know ABBA - Dancing Queen"
assert app.match_key(raw["title"]) == "dancing queen"
assert app.match_key(raw["artist"]["name"]) == "abba"
print("find_deezer_song_exact=passed")

assert app.find_deezer_song("ABBA", "Zzzz Not A Real Song Title Qqq") is None
print("find_deezer_song_rejects_mismatch=passed")

ytm = YTMusic()
tracks = app.resolve_import_entry({"artist": "ABBA", "title": "Dancing Queen"}, ytm)
assert len(tracks) == 1
track = tracks[0]
assert track["source"] == "deezer", track["source"]
assert track["isrc"], "the Deezer version should carry an ISRC"
assert track["title"].casefold() == "dancing queen"
assert "abba" in track["artist"].casefold()
assert track["album"], "the Deezer version should carry an album"
assert track["video_id"], "a YouTube audio source is still required"
print("import_entry_prefers_deezer=passed")

# An entry Deezer cannot match cleanly must still fall back to the YouTube path.
fallback = app.resolve_import_entry(
    {"artist": "Zqx Nonexistent Artist", "title": "Zqx Nonexistent Song 12345"}, ytm
)
assert fallback and fallback[0]["source"] == "youtube", fallback[0]["source"]
print("import_entry_falls_back_to_youtube=passed")

capped = app.resolve_import_entry({"query": "ABBA"}, ytm, 10)
assert capped, "artist-only entries should resolve to tracks"
assert all(track["source"] == "deezer" for track in capped)
assert len(capped) <= 10, len(capped)
print(f"import_artist_respects_cap=passed ({len(capped)} tracks)")

# The default (no cap) takes the artist's whole catalogue, not just their hits.
catalog = app.deezer_artist_catalog("180", "abba", app.MAX_IMPORT_TRACKS)
assert len(catalog) > 100, f"ABBA's full catalogue should exceed the top-tracks list ({len(catalog)})"
keys = [app.match_key(raw.get("title_short") or raw.get("title")) for raw in catalog]
assert len(keys) == len(set(keys)), "the catalogue should not repeat a title"
assert "dancing queen" in keys
print(f"deezer_artist_catalog=passed ({len(catalog)} distinct tracks)")

assert app.find_deezer_artist("ABBA")["id"] == 180, "namesakes must not outrank the real artist"
assert app.find_deezer_artist("Zqx Nonexistent Artist 12345") is None
print("find_deezer_artist=passed")

assert app.IMPORT_ARTIST_TRACK_CHOICES["all"] == 0
assert app.IMPORT_ARTIST_TRACK_CHOICES["25"] == 25
print("artist_track_choices=passed")
