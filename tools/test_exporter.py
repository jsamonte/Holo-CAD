"""The spec's acceptance test, run headless.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" tools\\test_exporter.py

Builds shapes in FreeCAD, exports them with the addon's exporter, then reads
the GLB back and measures it exactly the way the lens does: combine the
bounding box of every mesh, take the largest dimension, and compare it to the
bbox_mm in the metadata. If the correction factor that falls out is not 100,
the lens would show the part at the wrong size.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys

import FreeCAD
import Part

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "freecad_addon", "SpecsLink")
)

from specslink import exporter  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def close(a, b, tolerance=1e-6):
    return abs(a - b) <= tolerance


def read_glb(blob: bytes):
    """Parse a GLB far enough to measure it, the way the lens would."""
    magic, version, total = struct.unpack_from("<4sII", blob, 0)
    assert magic == b"glTF" and version == 2, "not a glTF 2.0 binary"
    assert total == len(blob), "declared length {0} but got {1}".format(total, len(blob))

    offset = 12
    gltf = None
    binary = b""
    while offset < len(blob):
        length, kind = struct.unpack_from("<I4s", blob, offset)
        chunk = blob[offset + 8:offset + 8 + length]
        if kind == b"JSON":
            gltf = json.loads(chunk.decode("utf-8"))
        elif kind.startswith(b"BIN"):
            binary = chunk
        offset += 8 + length
    return gltf, binary


def measured_extent(gltf):
    """Largest dimension across every POSITION accessor, in file units."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
            for axis in range(3):
                lo[axis] = min(lo[axis], accessor["min"][axis])
                hi[axis] = max(hi[axis], accessor["max"][axis])
    return [hi[i] - lo[i] for i in range(3)]


def main() -> int:
    doc = FreeCAD.newDocument("holocad_test")

    print("the 100 mm cube, which is the spec's acceptance test")
    box = doc.addObject("Part::Box", "Cube")
    box.Length = box.Width = box.Height = 100
    doc.recompute()

    result = exporter.export([box])
    check("exported something", result["glb"][:4] == b"glTF")
    check("used the testable exporter", result["exporter"] == "fallback")
    check("bbox is 100 mm cubed",
          all(close(v, 100.0, 1e-7) for v in result["bbox_mm"]),
          str(result["bbox_mm"]))
    check("12 triangles for a box", result["triangles"] == 12,
          str(result["triangles"]))

    gltf, binary = read_glb(result["glb"])
    check("GLB parses", gltf is not None and len(binary) > 0)
    extent = measured_extent(gltf)
    check("file holds 0.1 units per side, since glTF says metres",
          all(close(v, 0.1, 1e-6) for v in extent), str(extent))

    # This is the lens's arithmetic, verbatim.
    correction = max(result["bbox_mm"]) / 10.0 / max(extent)
    check("correction factor comes out at 100", close(correction, 100.0, 1e-6),
          str(correction))
    shown_mm = [v * correction * 10 for v in sorted(extent, reverse=True)]
    check("would render at 100 mm", all(close(v, 100.0, 1e-4) for v in shown_mm),
          str(shown_mm))

    print("a part that is not a cube, to catch an axis swap")
    slab = doc.addObject("Part::Box", "Slab")
    slab.Length, slab.Width, slab.Height = 120.0, 40.0, 15.0
    doc.recompute()
    result = exporter.export([slab])
    check("bbox matches the part",
          [round(v, 6) for v in result["bbox_mm"]] == [120.0, 40.0, 15.0],
          str(result["bbox_mm"]))
    gltf, _ = read_glb(result["glb"])
    extent = measured_extent(gltf)
    correction = max(result["bbox_mm"]) / 10.0 / max(extent)
    predicted = sorted([v * correction * 10 for v in extent], reverse=True)
    check("sorted proportions survive the Z up to Y up turn",
          all(close(a, b, 1e-4) for a, b in zip(predicted, [120.0, 40.0, 15.0])),
          str(predicted))
    # Z up to Y up means the part's height ends up on glTF's Y axis.
    check("height lands on the glTF Y axis",
          close(extent[1] * 1000.0, 15.0, 1e-3), str(extent[1] * 1000.0))

    print("placement inside an App::Part, which the stock exporter has got wrong")
    container = doc.addObject("App::Part", "Assembly")
    inner = doc.addObject("Part::Box", "Inner")
    inner.Length = inner.Width = inner.Height = 50
    container.addObject(inner)
    container.Placement = FreeCAD.Placement(
        FreeCAD.Vector(1000, 0, 0), FreeCAD.Rotation())
    doc.recompute()
    bbox = exporter.bounding_box_mm([inner])
    check("size is unchanged by the container",
          all(close(v, 50.0, 1e-7) for v in bbox), str(bbox))
    placed = exporter.global_shape(inner).BoundBox
    check("the container's offset is applied", close(placed.XMin, 1000.0, 1e-6),
          "XMin {0}".format(placed.XMin))

    print("selection rules")
    chosen = exporter.collect_objects(doc)
    names = sorted(o.Name for o in chosen)
    check("nothing selected takes every visible solid",
          "Cube" in names and "Slab" in names, str(names))
    check("a container is not exported alongside its contents",
          "Assembly" not in names, str(names))
    chosen = exporter.collect_objects(doc, selection=[box])
    check("an explicit selection wins", [o.Name for o in chosen] == ["Cube"])

    print("refusals")
    try:
        exporter.export([])
        check("empty selection refused", False, "it did not raise")
    except exporter.ExportError:
        check("empty selection refused", True)

    empty_doc = FreeCAD.newDocument("holocad_empty")
    try:
        exporter.collect_objects(empty_doc)
        check("an empty document refused", False, "it did not raise")
    except exporter.ExportError:
        check("an empty document refused", True)

    print("mesh quality changes the triangle count")
    sphere = doc.addObject("Part::Sphere", "Sphere")
    sphere.Radius = 50
    doc.recompute()
    counts = {}
    for quality in ("draft", "normal", "fine"):
        counts[quality] = exporter.export([sphere], quality=quality)["triangles"]
    check("finer means more triangles",
          counts["draft"] < counts["normal"] < counts["fine"], str(counts))
    curved = exporter.export([sphere])
    check("a curved surface still measures 100 mm across",
          all(close(v, 100.0, 0.5) for v in curved["bbox_mm"]),
          str(curved["bbox_mm"]))

    FreeCAD.closeDocument(doc.Name)
    FreeCAD.closeDocument(empty_doc.Name)

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


# Called unconditionally on purpose. freecadcmd executes a script without
# setting __name__ to "__main__", so the usual guard makes the whole file do
# nothing and still exit 0, which looks exactly like a passing run.
_status = main()
if __name__ == "__main__":
    sys.exit(_status)
