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

    print("one inch in FreeCAD is one inch on the glasses")
    # FreeCAD works in millimetres internally whatever the display units
    # are, and the lens is told millimetres. If anything ever read the
    # display schema instead, an imperial user would be out by 25.4x.
    FreeCAD.Units.setSchema(3)  # Imperial decimal
    inch = doc.addObject("Part::Box", "OneInch")
    inch.Length = FreeCAD.Units.Quantity("1 in")
    inch.Width = FreeCAD.Units.Quantity("1 in")
    inch.Height = FreeCAD.Units.Quantity("1 in")
    doc.recompute()
    result = exporter.export([inch])
    check("a 1 inch cube reports 25.4 mm",
          all(close(v, 25.4, 1e-6) for v in result["bbox_mm"]),
          str(result["bbox_mm"]))
    gltf, _ = read_glb(result["glb"])
    extent = measured_extent(gltf)
    correction = max(result["bbox_mm"]) / 10.0 / max(extent)
    shown_mm = max(extent) * correction * 10
    check("and the lens would draw it 25.4 mm across",
          close(shown_mm, 25.4, 1e-4), str(shown_mm))
    check("which is one inch", close(shown_mm / 25.4, 1.0, 1e-6),
          str(shown_mm / 25.4))
    FreeCAD.Units.setSchema(0)  # back to the standard schema

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
    # Reversed deliberately. A container is now kept and its contents
    # dropped, because the old way exported an intermediate PartDesign
    # feature rather than the finished Body.
    check("a container is exported instead of its contents",
          "Assembly" in names and "Inner" not in names, str(names))
    chosen = exporter.collect_objects(doc, selection=[box])
    check("an explicit selection wins", [o.Name for o in chosen] == ["Cube"])

    print("what a real PartDesign document drags along")
    # The failure this guards: with nothing selected, a document full of
    # origin axes, datum planes and sketches exported none of them and
    # reported "every object tessellated to nothing", because all of them
    # have a Shape and none has any triangles.
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
    plane = doc.addObject("App::Plane", "XY_Plane")
    line = doc.addObject("App::Line", "X_Axis")
    doc.recompute()
    check("a sketch is not renderable", not exporter.has_renderable_shape(sketch))
    check("a datum plane is not renderable, despite having a face",
          not exporter.has_renderable_shape(plane))
    check("an origin axis is not renderable",
          not exporter.has_renderable_shape(line))
    check("a solid is renderable", exporter.has_renderable_shape(box))

    chosen = exporter.collect_objects(doc)
    names = sorted(o.Name for o in chosen)
    check("none of them are picked up",
          not any(n in names for n in ("Sketch", "XY_Plane", "X_Axis")),
          str(names))
    check("the solids still are", "Cube" in names and "Slab" in names, str(names))

    print("selecting something with no surfaces says so")
    try:
        exporter.collect_objects(doc, selection=[sketch])
        check("a sketch only selection is refused", False, "it did not raise")
    except exporter.ExportError as e:
        check("a sketch only selection is refused", "no surfaces" in str(e),
              str(e))

    print("a container wins over its contents")
    holder = doc.addObject("App::Part", "Holder")
    inner_box = doc.addObject("Part::Box", "Held")
    inner_box.Length = inner_box.Width = inner_box.Height = 30
    holder.addObject(inner_box)
    doc.recompute()
    chosen = exporter.collect_objects(doc, selection=[holder, inner_box])
    names = [o.Name for o in chosen]
    check("the container is kept, not the part inside it",
          names == ["Holder"], str(names))

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

    print("each part is written about its own origin, and says where it belongs")
    # This is what stopped an assembly arriving in a heap. Every part used
    # to carry its document position in its vertices and then be recentred
    # by the lens, separately, so they all ended up on the same spot. And
    # the lens measures a box that also contains the origin, so a part
    # modelled far from the document origin measured as that distance
    # across however small it really was.
    far = doc.addObject("Part::Box", "FarAway")
    far.Length, far.Width, far.Height = 10, 10, 10
    far.Placement.Base = FreeCAD.Vector(500, 300, 200)
    doc.recompute()

    lone = exporter.export([far])
    gltf_far, _ = read_glb(lone["glb"])
    extent = measured_extent(gltf_far)
    check("a part far from the origin still measures its own size",
          all(close(v, 0.01, 1e-6) for v in extent), str(extent))
    # The lens's box includes the origin, so this is the number that matters.
    lo = [min(0.0, gltf_far["accessors"][p["attributes"]["POSITION"]]["min"][a])
          for p in gltf_far["meshes"][0]["primitives"] for a in range(3)]
    hi = [max(0.0, gltf_far["accessors"][p["attributes"]["POSITION"]]["max"][a])
          for p in gltf_far["meshes"][0]["primitives"] for a in range(3)]
    span = max(hi[i] - lo[i] for i in range(3))
    check("and still measures it once the origin is included",
          close(span, 0.01, 1e-6), str(span))
    check("a lone part reports no offset",
          all(close(v, 0.0) for v in lone["offset_mm"]), str(lone["offset_mm"]))

    origin = exporter.bottom_centre_mm(exporter.bounding_box_doc([box, far]))
    near = exporter.export([box], origin_mm=origin)
    away = exporter.export([far], origin_mm=origin)
    check("two parts report different offsets",
          near["offset_mm"] != away["offset_mm"],
          "{0} vs {1}".format(near["offset_mm"], away["offset_mm"]))
    # The gap is between their bottom centres, not their placements, and
    # the two cubes are different sizes. The 100 mm cube sits at the origin,
    # so its bottom centre is (50, 50, 0); the 10 mm cube is placed at
    # (500, 300, 200), so its bottom centre is (505, 305, 200). In document
    # axes that is (455, 255, 200) apart.
    #
    # Lens axes: x is document x, y is document z, z is negative document y.
    # So the same gap reads as (455, 200, -255).
    check("the offset is the gap between their bottom centres, in lens axes",
          close(away["offset_mm"][0] - near["offset_mm"][0], 455.0, 0.01)
          and close(away["offset_mm"][1] - near["offset_mm"][1], 200.0, 0.01)
          and close(away["offset_mm"][2] - near["offset_mm"][2], -255.0, 0.01),
          "{0} vs {1}".format(near["offset_mm"], away["offset_mm"]))

    print("a curved shape reports the size it is, not the size of its surfaces")
    # Shape.BoundBox is only an estimate for curved geometry. A trimmed
    # sphere reported the whole sphere, which made the lens scale the part
    # down and warn that the proportions disagreed.
    ball = doc.addObject("Part::Sphere", "Hemisphere")
    ball.Radius = 50
    ball.Angle1 = 0
    doc.recompute()
    loose = ball.Shape.BoundBox
    tight = exporter.bounding_box_mm([ball])
    check("the tight box is no larger than the estimate",
          all(tight[i] <= (loose.XLength, loose.YLength, loose.ZLength)[i] + 1e-6
              for i in range(3)),
          "{0} vs {1}".format(tight, (loose.XLength, loose.YLength, loose.ZLength)))
    hemi = exporter.export([ball])
    gltf_h, _ = read_glb(hemi["glb"])
    ext_h = measured_extent(gltf_h)
    corr = (max(hemi["bbox_mm"]) / 10.0) / max(ext_h)
    check("so the correction a curved part produces is still 100",
          close(corr, 100.0, 1.0), "{0:.4f}".format(corr))

    print("colours are reported beside the model, and land in the GLB")
    # Headless there is no ViewObject, so object_colour falls back to its
    # default. That is the case worth pinning anyway: the lens must get a
    # usable colour per mesh whether or not FreeCAD had a GUI.
    two = exporter.export([box, sphere])
    check("one colour per exported object",
          len(two["colours"]) == 2, str(two["colours"]))
    check("each colour is rgba",
          all(len(c) == 4 for c in two["colours"]), str(two["colours"]))
    check("each channel is within 0 to 1",
          all(0.0 <= v <= 1.0 for c in two["colours"] for v in c),
          str(two["colours"]))

    single = exporter.export([box])
    check("one object reports one colour",
          len(single["colours"]) == 1, str(single["colours"]))

    gltf_two, _ = read_glb(two["glb"])
    factors = [
        m["pbrMetallicRoughness"]["baseColorFactor"]
        for m in gltf_two.get("materials", [])
    ]
    check("the GLB carries a material per mesh",
          len(factors) == 2, str(factors))
    check("the reported colours match the GLB's own baseColorFactor",
          all(all(close(a, b, 1e-5) for a, b in zip(reported, written))
              for reported, written in zip(two["colours"], factors)),
          "{0} vs {1}".format(two["colours"], factors))

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
