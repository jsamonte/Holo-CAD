"""Render one of the addon's own SVG icons as a 320x320 lens icon PNG.

Standard library only, same rule the addon follows, so there is nothing to
install. It reads the SVG rather than redrawing it from memory, so the lens
icon and the FreeCAD toolbar icon cannot drift apart.

    py tools\\svg_to_lens_icon.py
    py tools\\svg_to_lens_icon.py --svg freecad_addon/SpecsLink/icons/send.svg
    py tools\\svg_to_lens_icon.py --transparent --out plain.png

Snap asks for a 320x320 PNG, readable small, and crops it to a circle. So
by default the mark is drawn on a filled dark circle, which is what stops a
transparent icon looking like a hole. --transparent gives the bare mark
instead.

Set it in Lens Studio under Project Settings, Distribution Settings, Lens
Icon.

Only the path syntax the addon's icons actually use is supported: absolute
M, L and Z. That is a deliberate limit rather than a half finished SVG
parser. If an icon ever grows a curve, this says so instead of quietly
drawing the wrong shape.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import xml.etree.ElementTree as ET
import zlib

SIZE = 320
SS = 4  # supersample factor, for edges that are not jagged

# The same background the hand drawn lens icon uses, so the two are
# recognisably the same product.
BACKGROUND = (14, 20, 38)
TRANSPARENT = (0, 0, 0, 0)

SVG_NS = "{http://www.w3.org/2000/svg}"


def parse_colour(text, fallback=(255, 255, 255)):
    """#rgb or #rrggbb to a tuple. Anything else falls back."""
    if not text:
        return fallback
    text = text.strip()
    match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text)
    if not match:
        return fallback
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def parse_path(d):
    """A path of absolute M, L and Z into a list of point lists."""
    tokens = re.findall(r"[MLZmlz]|-?\d*\.?\d+", d)
    runs = []
    current = []
    start = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in "ML":
            x = float(tokens[i + 1])
            y = float(tokens[i + 2])
            if token == "M":
                if len(current) > 1:
                    runs.append(current)
                current = [(x, y)]
                start = (x, y)
            else:
                current.append((x, y))
            i += 3
        elif token in "Zz":
            if start is not None and current:
                current.append(start)
            i += 1
        elif token in "mlz":
            raise ValueError(
                "relative path commands are not supported, and this icon "
                "uses one: {0}".format(token))
        else:
            raise ValueError("unexpected token in path: {0}".format(token))
    if len(current) > 1:
        runs.append(current)
    return runs


def read_svg(path):
    """(viewbox size, [(points, colour, stroke width)]) from an SVG."""
    tree = ET.parse(path)
    root = tree.getroot()
    viewbox = root.get("viewBox")
    if viewbox:
        parts = [float(v) for v in viewbox.replace(",", " ").split()]
        extent = max(parts[2], parts[3])
    else:
        extent = float(root.get("width", 64))

    strokes = []

    def walk(node, stroke, width):
        stroke = node.get("stroke", stroke)
        width = float(node.get("stroke-width", width))
        if node.tag == SVG_NS + "path":
            d = node.get("d")
            if d:
                colour = parse_colour(stroke)
                for run in parse_path(d):
                    strokes.append((run, colour, width))
        for child in node:
            walk(child, stroke, width)

    walk(root, "#ffffff", 2.0)
    if not strokes:
        raise ValueError("no stroked paths found in {0}".format(path))
    return extent, strokes


def draw_segment(px, w, h, a, b, radius, colour):
    """A hard round capped line. Anti-aliasing comes from supersampling.

    Only the segment's bounding box is visited, which keeps the whole
    render well under a second in pure Python.
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


def fill_circle(px, w, h, cx, cy, radius, colour):
    rsq = radius * radius
    for y in range(max(0, int(cy - radius)), min(h, int(cy + radius) + 1)):
        row = y * w
        dy = y - cy
        for x in range(max(0, int(cx - radius)), min(w, int(cx + radius) + 1)):
            dx = x - cx
            if dx * dx + dy * dy <= rsq:
                px[row + x] = colour


def render(svg_path, transparent=False, margin=0.80):
    extent, strokes = read_svg(svg_path)
    w = h = SIZE * SS
    px = [TRANSPARENT] * (w * h)

    if not transparent:
        fill_circle(px, w, h, w / 2.0, h / 2.0, w / 2.0,
                    BACKGROUND + (255,))

    # Scale the viewBox into the circle with a margin, since Snap crops to
    # a circle and a mark that touches the edge loses its corners.
    scale = (w * margin) / extent
    offset = (w - extent * scale) / 2.0

    for points, colour, width in strokes:
        radius = max(1.0, width * scale / 2.0)
        rgba = colour + (255,)
        placed = [(offset + x * scale, offset + y * scale) for x, y in points]
        for i in range(len(placed) - 1):
            draw_segment(px, w, h, placed[i], placed[i + 1], radius, rgba)

    # Box filter down from the supersampled buffer.
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)  # PNG filter type 0 for this row
        base = y * SS * w
        for x in range(SIZE):
            r = g = b = a = 0
            for sy in range(SS):
                row = base + sy * w + x * SS
                for sx in range(SS):
                    pr, pg, pb, pa = px[row + sx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            count = SS * SS
            if a == 0:
                raw.extend((0, 0, 0, 0))
            else:
                # Composited against the coverage, so edges do not darken.
                raw.extend((r // a, g // a, b // a, a // count))
    return bytes(raw)


def write_png(path, raw):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">2I5B", SIZE, SIZE, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", header))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_svg = os.path.join(
        here, "..", "freecad_addon", "SpecsLink", "icons", "workbench.svg")

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--svg", default=default_svg)
    ap.add_argument("--out", default=os.path.join(here, "lens_icon_workbench.png"))
    ap.add_argument("--transparent", action="store_true",
                    help="no background circle, just the mark")
    ap.add_argument("--margin", type=float, default=0.80,
                    help="how much of the square the mark fills")
    args = ap.parse_args()

    raw = render(args.svg, transparent=args.transparent, margin=args.margin)
    write_png(args.out, raw)
    print("wrote {0}, {1} x {1}, from {2}".format(
        os.path.abspath(args.out), SIZE, os.path.abspath(args.svg)))


main()
