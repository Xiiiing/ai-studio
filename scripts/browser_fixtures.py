#!/usr/bin/env python3
"""Build tiny synthetic browser fixtures with CPU FFmpeg; never invoke a model."""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.core import sha256, within
from factory.audio_editor import FFMPEG


def main():
    folder = within(ROOT / "runtime", "browser-fixtures")
    folder.mkdir(parents=True, exist_ok=True)
    video, image = folder / "clip.mp4", folder / "frame.png"
    if not video.exists():
        subprocess.run([str(FFMPEG), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                        "testsrc=size=360x640:rate=24:duration=6", "-c:v", "libx264", "-threads", "1",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video)], check=True, timeout=45)
    if not image.exists():
        subprocess.run([str(FFMPEG), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-i", str(video),
                        "-frames:v", "1", str(image)], check=True, timeout=20)
    from factory.providers import public_inventory
    config = json.loads((ROOT / "config/factory.empty.example.json").read_text())
    (folder / "model-catalog.json").write_text(json.dumps(public_inventory(config), ensure_ascii=False))
    print(json.dumps({"fixture": "CPU_test_pattern_not_model_generation", "video_sha256": sha256(video),
                      "frame_sha256": sha256(image)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
