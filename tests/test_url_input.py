from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app

result = app.resolve_media_url("https://www.youtube.com/watch?v=8QzLiHvt_EA")
assert result["source"] == "youtube"
assert result["kind"] == "video"
assert result["video_id"] == "8QzLiHvt_EA"
assert result["preview_type"] == "youtube"
assert result["token"]
print(f"source={result['source']}")
print(f"kind={result['kind']}")
print(f"video_id={result['video_id']}")
print(f"artist={result['artist']}")
print(f"title={result['title']}")
print(f"duration_seconds={result['duration_seconds']}")
print("url_input_test=passed")
