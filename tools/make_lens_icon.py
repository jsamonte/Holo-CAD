"""Write a 320x320 lens icon PNG for the Holo-CAD publish settings.

Standard library only, same rule the FreeCAD addon follows, so there is
nothing to install and nothing to keep in sync.

Snap asks for a 320x320 PNG of simple vector shapes, readable small, and
crops it to a circle. So this draws a wireframe cube, which is the one image
that says CAD and hologram at once, on a filled circle.

    py tools\\make_lens_icon.py                  writes tools/lens_icon.png
    py tools\\make_lens_icon.py --out other.png

Set it in Lens Studio under Project Settings, Distribution Settings, Lens Icon.
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib

SIZE = 320
SS = 4  # supersample factor, for edges that are not jagged

BACKGROUND = (14, 20, 38)
CUBE_NEAR = (90, 200, 255)
CUBE_FAR = (46, 102, 160)
ACCENT = (255, 214, 102)


def iso(x: float, y: float, z: float, scale: float, cx: float, cy: float):
    """Isometric projection of a unit cube corner into image space."""
    sx = (x - y) * math.cos(math.radians(30))
    sy = (x + y) * math.sin(math.radians(30)) - z
    return cx + sx * scale, cy + sy * scale


def draw_segment(px, w, h, a, b, radius, colour):
    """Anti-aliasing comes from supersampling, so this is a hard round line.

    Only the segment's bounding box is visited, which keeps the whole render
    well under a second in pure Python.
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    x0 = max(0, int(min(ax, bx) - radius - 1))
    x1 = min(w - 1, int(max(ax, bx) + radius + 1))
    y0 = max(0, int(min(ay, by) - radius - 1))
    y1 = min(h - 1, int(max(ay, by) + radius + 1))
    rsq = radius * radius
    for y in range(y0, y1 + 1):
        row = y * w
        for x in range(x0, x1 + 1):
            if length_sq == 0:
                t = 0.0
            else:
                t = ((x - ax) * dx + (y - ay) * dy) / length_sq
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
            ex = ax + t * dx - x
            ey = ay + t * dy - y
            if ex * ex + ey * ey <= rsq:
                px[row + x] = colour


def render() -> bytes:
    w = h = SIZE * SS
    px = [None] * (w * h)

    cx = cy = w / 2.0
    # Lens Studio crops to a circle, so keep everything inside one.
    disc = w / 2.0 - 1
    disc_sq = disc * disc
    for y in range(h):
        row = y * w
        dy = y - cy
        for x in range(w):
            dx = x - cx
            if dx * dx + dy * dy <= disc_sq:
                px[row + x] = BACKGROUND

    scale = w * 0.165
    centre_y = cy + w * 0.03
    corners = {}
    for x in (0, 1):
        for y in (0, 1):
            for z in (0, 1):
                corners[(x, y, z)] = iso(x - 0.5, y - 0.5, z - 0.5, scale, cx, centre_y)

    thick = w * 0.016
    # Back edges first, so the near ones overdraw them.
    far = [
        ((0, 0, 0), (1, 0, 0)),
        ((0, 0, 0), (0, 1, 0)),
        ((0, 0, 0), (0, 0, 1)),
    ]
    near = [
        ((1, 0, 0), (1, 1, 0)), ((0, 1, 0), (1, 1, 0)),
        ((0, 0, 1), (1, 0, 1)), ((0, 0, 1), (0, 1, 1)),
        ((1, 0, 0), (1, 0, 1)), ((0, 1, 0), (0, 1, 1)),
        ((1, 1, 0), (1, 1, 1)), ((1, 0, 1), (1, 1, 1)), ((0, 1, 1), (1, 1, 1)),
    ]
    for a, b in far:
        draw_segment(px, w, h, corners[a], corners[b], thick * 0.7, CUBE_FAR)
    for a, b in near:
        draw_segment(px, w, h, corners[a], corners[b], thick, CUBE_NEAR)

    # A measurement tick under the cube: this shows true size, after all.
    base_y = centre_y + w * 0.30
    left = (cx - w * 0.21, base_y)
    right = (cx + w * 0.21, base_y)
    draw_segment(px, w, h, left, right, thick * 0.55, ACCENT)
    for end in (left, right):
        draw_segment(px, w, h, (end[0], end[1] - w * 0.035),
                     (end[0], end[1] + w * 0.035), thick * 0.55, ACCENT)

    # Box downsample to the final size, over transparency where unset.
    out = bytearray()
    area = SS * SS
    for y in range(SIZE):
        out.append(0)  # PNG filter: none
        for x in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                row = (y * SS + sy) * w + x * SS
                for sx in range(SS):
                    p = px[row + sx]
                    if p is not None:
                        r += p[0]
                        g += p[1]
                        b += p[2]
                        a += 255
            out += bytes((r // area, g // area, b // area, a // area))
    return bytes(out)


def write_png(path: str, raw: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">2I5B", SIZE, SIZE, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", header))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Write the Holo-CAD lens icon")
    ap.add_argument("--out", default="tools/lens_icon.png")
    args = ap.parse_args()
    write_png(args.out, render())
    print("wrote {0}, {1}x{1} PNG".format(args.out, SIZE))
    print("Set it in Project Settings, Distribution Settings, Lens Icon.")


if __name__ == "__main__":
    main()
