import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from requestcast import app


root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr)


def packet_hashes(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
            "-show_entries", "packet=data_hash", "-show_data_hash", "sha256", "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]


track = {
    "title": "Quality Test", "artist": "RequestCast", "album": "Tests", "year": "2026",
    "track_number": 1, "disc_number": 1, "source": "youtube", "source_id": "quality-test",
    "isrc": "", "cover": "",
}

flac = root / "source.flac"
run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "flac", str(flac))
flac_before = packet_hashes(flac)
assert app.remux_or_preserve_audio(flac, root) == flac
app.tag_audio(flac, track)
assert packet_hashes(flac) == flac_before

m4a = root / "source.m4a"
run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=550:duration=1", "-c:a", "aac", "-b:a", "192k", str(m4a))
m4a_before = packet_hashes(m4a)
assert app.remux_or_preserve_audio(m4a, root) == m4a
app.tag_audio(m4a, track)
assert packet_hashes(m4a) == m4a_before

webm = root / "source.webm"
run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=1", "-c:a", "libopus", "-b:a", "160k", str(webm))
webm_before = packet_hashes(webm)
opus = app.remux_or_preserve_audio(webm, root)
assert opus.suffix == ".opus"
assert app.probe_audio(opus)["codec_name"] == "opus"
assert packet_hashes(opus) == webm_before

print("flac_passthrough_audio_hash=passed")
print("m4a_passthrough_audio_hash=passed")
print("webm_opus_lossless_remux_hash=passed")
