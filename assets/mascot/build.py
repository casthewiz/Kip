#!/usr/bin/env python3
"""Draws Kip the mascot as animated pixel-art SVGs, one per state, next to this file.

Edit the layers below and rerun: python3 assets/mascot/build.py
"""
from pathlib import Path

PALETTE = {
    "k": "#1d1b2e",  # outline
    "h": "#3d2c5e",  # hair
    "H": "#5a4385",  # hair highlight
    "b": "#8fb4ff",  # body
    "d": "#5f86e0",  # body shade
    "w": "#f4f7ff",  # lens
    "r": "#ff6b8a",  # hair tie
    "p": "#ffa3b8",  # blush
    "s": "#c9ced6",  # laptop
    "S": "#8b92a0",  # laptop shade
    "g": "#7cf2a7",  # laptop logo
    "y": "#ffd34d",  # sparkle
    "c": "#b7bfcc",  # cloud
    "C": "#8b93a3",  # cloud shade
    "u": "#5ab0ff",  # rain, sweat
    "z": "#9aa3b5",  # snore
}
SIZE = 32

BODY = (
    "........kkkkkkkk........",
    "......kkhhhhhhhhkk......",
    ".....khhhhhhhhhhhhk.....",
    "....khhhhhhhhhhhhhhk....",
    "...khhhhhhhhhhhhhhhhk...",
    "...khbbhhhbbbbhhhbbhk...",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kbbbbbbbbbbbbbbbbbbk..",
    "..kdbbbbbbbbbbbbbbbbdk..",
    "...kddbbbbbbbbbbbbddk...",
    "....kkddddddddddddkk....",
    "......kkkkkkkkkkkk......",
    ".....kkk........kkk.....",
)
GLASSES = (
    "kkkkkk....kkkkkk",
    "kwwwwkkkkkkwwwwk",
    "kwwwwk....kwwwwk",
    "kwwwwk....kwwwwk",
    "kkkkkk....kkkkkk",
)
BUNS = (
    (
        "..h....k..",
        "...kkkkhk.",
        "..khhHhhk.",
        ".khHhhhhhk",
        ".khhhhHhhk",
        "..kkrrrkk.",
    ),
    (
        ".k.....h..",
        "..hkkkkk..",
        "..khhHhhk.",
        ".khHhhhhhk",
        ".khhhhHhhk",
        "..kkrrrkk.",
    ),
)
# (row offset inside the glasses, rows spanning both lenses)
EYES = {
    "open": (1, (".kk........kk.", ".kk........kk.")),
    "down": (2, (".kk........kk.", ".kk........kk.")),
    "closed": (2, ("kkkk......kkkk",)),
    "happy": (1, (".kk........kk.", "k..k......k..k")),
}
MOUTHS = {
    "flat": (".kk.",),
    "smile": ("k..k", ".kk."),
    "frown": (".kk.", "k..k"),
    "o": (".kk.", ".kk."),
}
BLUSH = ("pp............pp",)
LAPTOP = (
    "..kkkkkkkkkkkkkk..",
    "..kssssssssssssk..",
    "..ksssssggsssssk..",
    "..kssssssssssssk..",
    "kkkkkkkkkkkkkkkkkk",
    "kSSSSSSSSSSSSSSSSk",
    "kkkkkkkkkkkkkkkkkk",
)
PAW = ("kkk", "kbk")
Z = ("zzzz", "..z.", ".z..", "zzzz")
SPARK = (".y.", "yyy", ".y.")
DOT = ("y",)
CLOUD = (
    "....cccc......",
    "..ccccccc.cc..",
    ".cccccccccccc.",
    "cccccccccccccc",
    ".CCCCCCCCCCCC.",
)
DROP = ("u", "u")
SWEAT = (".u", "uu", "uu")


def kip(dy=0, eyes="open", mouth="smile", bun=0, blush=False, laptop=False, paws=(0, 0)):
    x, y = 4, 10 + dy
    eye_row, eye_rows = EYES[eyes]
    parts = [
        (BUNS[bun], x + 7, y - 6),
        (BODY, x, y),
        (GLASSES, x + 4, y + 7),
        (eye_rows, x + 5, y + 7 + eye_row),
        (MOUTHS[mouth], x + 10, y + 13),
    ]
    if blush:
        parts.append((BLUSH, x + 4, y + 13))
    if laptop:
        parts += [(LAPTOP, x + 3, y + 14), (PAW, x + 4, y + 16 + paws[0]), (PAW, x + 17, y + 16 + paws[1])]
    return parts


def waiting():
    path = [(5, 9), (4, 6), (3, 3), (2, 0)]
    return [
        kip(dy=i // 2, eyes="closed", mouth="o" if i // 2 else "flat") + [(Z, *path[i]), (Z, *path[(i + 2) % 4])]
        for i in range(4)
    ]


def working():
    return [kip(eyes="down", mouth="flat", bun=i % 2, laptop=True, paws=(i % 2, 1 - i % 2)) for i in range(4)]


def success():
    hops = [0, 0, -2, -4, -4, -2]
    frames = []
    for i, dy in enumerate(hops):
        big = dy <= -2
        sparks = [(SPARK, 2, 6), (SPARK, 27, 10), (DOT, 6, 2), (DOT, 28, 4)] if big else [(DOT, 3, 7), (DOT, 28, 11)]
        frames.append(sparks + kip(dy=dy, eyes="happy", bun=i % 2, blush=True))
    return frames


def failure():
    frames = []
    for i in range(4):
        rain = [(DROP, x, 6 + (i + x) % 4 * 2) for x in (10, 14, 18, 22)]
        frames.append([(CLOUD, 9, 0)] + rain + kip(dy=1, eyes="down", mouth="frown") + [(SWEAT, 26, 13 + i % 2)])
    return frames


STATES = {"waiting": (0.5, waiting()), "working": (0.15, working()), "success": (0.12, success()), "failure": (0.25, failure())}


def svg(frame_time, frames):
    rects = []
    for f, parts in enumerate(frames):
        pixels = {}
        for rows, ox, oy in parts:
            for ry, row in enumerate(rows):
                for rx, ch in enumerate(row):
                    if ch != "." and 0 <= ox + rx < SIZE and 0 <= oy + ry < SIZE:
                        pixels[ox + rx, oy + ry] = ch
        for y in range(SIZE):
            x = 0
            while x < SIZE:
                ch = pixels.get((x, y))
                run = 1
                while ch and pixels.get((x + run, y)) == ch:
                    run += 1
                if ch:
                    rects.append(f'<rect x="{f * SIZE + x}" y="{y}" width="{run}" height="1" fill="{PALETTE[ch]}"/>')
                x += run
    n = len(frames)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="128" height="128" shape-rendering="crispEdges">'
        f"<style>g{{animation:play {n * frame_time:g}s steps({n}) infinite}}"
        f"@keyframes play{{to{{transform:translateX(-{n * SIZE}px)}}}}"
        "@media (prefers-reduced-motion:reduce){g{animation:none}}</style>"
        f'<g>{"".join(rects)}</g></svg>\n'
    )


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, (frame_time, frames) in STATES.items():
        (here / f"kip-{name}.svg").write_text(svg(frame_time, frames))
