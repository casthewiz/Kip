"""Draws the Windows tray icons, one per mood, from the first frame of each mascot SVG: kip-<mood>.ico here.

    python app/windows/make_icons.py

Rerun after assets/mascot/build.py redraws the mascots. Stdlib only: the tray needs no SVG renderer at run time.
"""
import re
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASCOT = HERE.parents[1] / "assets" / "mascot"
MOODS = ("failure", "asking", "working", "success", "waiting")  # receipt.py's MOOD_RANK
RECT = re.compile(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" fill="#([0-9a-fA-F]{6})"')


def frame(svg):
    """32x32 RGBA rows of the first frame (frames sit side by side, 32px apart)."""
    px = [[(0, 0, 0, 0)] * 32 for _ in range(32)]
    for x, y, w, h, fill in RECT.findall(svg):
        x, y, w, h = int(x), int(y), int(w), int(h)
        for cy in range(y, min(y + h, 32)):
            for cx in range(x, min(x + w, 32)):
                px[cy][cx] = (*bytes.fromhex(fill), 255)
    return px


def dib(px, scale):
    """One ICO image: a 32-bit bottom-up DIB at `scale` times, then an all-clear AND mask (alpha does the work)."""
    n = 32 * scale
    xor = b"".join(bytes((b, g, r, a)) for y in reversed(range(n)) for r, g, b, a in
                   (px[y // scale][x // scale] for x in range(n)))
    mask = bytes((n + 31) // 32 * 4 * n)
    return struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0) + xor + mask


def ico(px, scales=(1, 2)):
    """32x32 and 64x64."""
    images = [dib(px, s) for s in scales]
    out, offset = struct.pack("<HHH", 0, 1, len(images)), 6 + 16 * len(images)
    for s, img in zip(scales, images):
        out += struct.pack("<BBBBHHII", 32 * s, 32 * s, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return out + b"".join(images)


if __name__ == "__main__":
    for mood in MOODS:
        (HERE / f"kip-{mood}.ico").write_bytes(ico(frame((MASCOT / f"kip-{mood}.svg").read_text(encoding="utf-8"))))
