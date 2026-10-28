#!/usr/bin/env python3
"""Capture a site for a receipts claim without writing a spec, and make videos
GitHub plays inline.

  kubrick.py shot CLAIM URL [--viewports NAME,...] [--run ID]     # PNG per viewport
  kubrick.py record CLAIM URL [--viewports NAME,...] [--run ID]   # WebM per viewport
  kubrick.py mp4 VIDEO [OUT]                                      # H.264 MP4 under 10 MB

shot and record run shot.spec.ts or record.spec.ts through desktest.py, so each
viewport is its own evidence row with the capture collected into the run.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTEST = HERE.parent / "kip-receipts" / "playwright" / "desktest.py"
MAX_MB = 10  # the most an agent browser's file_upload takes (interfaces/claude-code.md)


def mp4(src, out):
    out = out or str(Path(src).with_suffix(".mp4"))
    # yuv420p and even dimensions are what browsers' H.264 decoders accept; faststart lets it play while loading.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-c:v", "libx264", "-crf", "28",
                    "-pix_fmt", "yuv420p", "-vf", "scale='trunc(min(1280,iw)/2)*2':-2", "-movflags", "+faststart",
                    "-an", out], check=True)
    mb = os.path.getsize(out) / 1e6
    if mb > MAX_MB:
        sys.exit(f"{out} is {mb:.1f} MB, over {MAX_MB} MB; record less")
    print(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("shot", "record"):
        s = sub.add_parser(name)
        s.add_argument("claim")
        s.add_argument("url")
        s.add_argument("--viewports")
        s.add_argument("--run")
    m = sub.add_parser("mp4")
    m.add_argument("video")
    m.add_argument("out", nargs="?")
    a = p.parse_args()

    if a.cmd == "mp4":
        return mp4(a.video, a.out)
    opts = [f"--{k}={v}" for k, v in (("viewports", a.viewports), ("run", a.run)) if v]
    sys.exit(subprocess.run([sys.executable, str(DESKTEST), a.claim, a.url, str(HERE / f"{a.cmd}.spec.ts"),
                             *opts]).returncode)


if __name__ == "__main__":
    main()
