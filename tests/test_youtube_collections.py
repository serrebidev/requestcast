from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(
    prefix="requestcast-youtube-collections-", ignore_cleanup_errors=True
) as temp_name:
    temp = Path(temp_name)
    os.environ["REQUESTCAST_CONFIG"] = str(temp / "config.json")
    os.environ["REQUESTCAST_DISABLE_WORKER"] = "1"
    os.environ["REQUESTCAST_DOWNLOAD_DIR"] = str(temp / "downloads")
    os.environ["REQUESTCAST_STATE_DIR"] = str(temp / "state")
    os.environ["REQUESTCAST_SECRET_KEY"] = "youtube-collection-test-secret"

    from requestcast import app
    from requestcast.youtube_collections import install

    install()

    playlist_id = "PL1234567890"
    first_video = "8QzLiHvt_EA"
    second_video = "Z9x8C7v6B5n"

    def fake_ytdlp_json(url: str, *, playlist_limit: int | None = None):
        if url == f"https://www.youtube.com/watch?v={first_video}":
            return {
                "id": first_video,
                "title": "Single video",
                "uploader": "Example Artist",
                "duration": 180,
                "thumbnail": "https://example.invalid/video.jpg",
            }
        if url == f"https://www.youtube.com/playlist?list={playlist_id}":
            return {
                "title": "Example playlist",
                "uploader": "Playlist Owner",
                "playlist_count": 2,
                "entries": [
                    {
                        "id": first_video,
                        "title": "First song",
                        "uploader": "First Artist",
                        "duration": 180,
                    },
                    {
                        "url": f"https://www.youtube.com/watch?v={second_video}",
                        "title": "Second song",
                        "channel": "Second Artist",
                        "duration": 200,
                    },
                    {"id": first_video, "title": "Duplicate"},
                ],
            }
        if url == "https://www.youtube.com/@example/videos":
            return {
                "title": "Example channel - Videos",
                "channel": "Example channel",
                "playlist_count": 2,
                "entries": [
                    {"id": first_video, "title": "Channel song one"},
                    {"id": second_video, "title": "Channel song two"},
                ],
            }
        raise AssertionError(f"Unexpected yt-dlp URL: {url} (limit={playlist_limit})")

    app.ytdlp_json = fake_ytdlp_json

    single = app.resolve_media_url(f"https://www.youtube.com/watch?v={first_video}")
    assert single["kind"] == "video"
    assert single["video_id"] == first_video

    playlist = app.resolve_media_url(
        f"https://www.youtube.com/watch?v={first_video}&list={playlist_id}"
    )
    assert playlist["kind"] == "playlist"
    assert playlist["collection_url"] == f"https://www.youtube.com/playlist?list={playlist_id}"
    playlist_tracks = app.expand_youtube(playlist)
    assert [track["video_id"] for track in playlist_tracks] == [first_video, second_video]

    channel = app.resolve_media_url("https://www.youtube.com/@example")
    assert channel["kind"] == "channel"
    assert channel["collection_url"] == "https://www.youtube.com/@example/videos"
    channel_tracks = app.expand_youtube(channel)
    assert [track["video_id"] for track in channel_tracks] == [first_video, second_video]

    assert app.is_youtube_collection_url(
        f"https://youtu.be/{first_video}?list={playlist_id}"
    )
    assert app.is_youtube_collection_url("https://www.youtube.com/channel/UC123/videos")

print("youtube_collection_url_test=passed")
