"""Turn FreeCAD objects into a GLB, and measure their true size.

Two exporters, as SPEC.md calls for:

  stock      ImportGui.export(objs, path). FreeCAD registers .glb against
             ImportGui, which only exists when the GUI is loaded, so this
             path cannot run under freecadcmd at all. It has also had
             long standing trouble with placements inside Links and
             App::Part, and with mirrored objects.

  fallback   tessellate each globally placed shape with
             MeshPart.meshFromShape and write the GLB here with struct.
             No GUI, no dependencies, and placements are applied by hand so
             the Link and App::Part cases cannot go wrong.

The fallback is the default, because it is the one that can be tested and the
one whose failure modes are known. Set prefer_stock to try the other first.

Units are the thing to be careful about. FreeCAD is millimetres and Z up,
glTF is metres and Y up. The conversion happens once, in to_gltf_space.
The lens measures what it receives and corrects anyway, so a mistake here
shows up as a model lying on its side rather than one at the wrong size.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile

import FreeCAD

COMPONENT_FLOAT = 5126
COMPONENT_UINT = 5125
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963

# LinearDeflection in mm, AngularDeflection in radians. Smaller is finer.
QUALITY = {
    "draft": (0.50, 0.70),
    "normal": (0.10, 0.50),
    "fine": (0.02, 0.30),
}
DEFAULT_QUALITY = "normal"

# Origin and datum geometry. These have shapes, and the planes even have
# faces, but none of them is part of the model anyone wants to look at.
DATUM_TYPES = frozenset((
    "App::Origin", "App::Line", "App::Plane", "App::Point",
    "PartDesign::Line", "PartDesign::Plane", "PartDesign::Point",
    "PartDesign::CoordinateSystem",
))

# Anything bigger than a kilometre is not a part, it is something infinite
# wearing a bounding box.
UNBOUNDED_MM = 1.0e6

# Containers worth keeping instead of their contents. Pruning normally
# keeps the leaf and drops the ancestor, which is right for an assembly
# holding separate parts, and wrong for a PartDesign Body: its contents
# are the intermediate features that build up the one solid you want.
PREFERRED_CONTAINERS = frozenset(("PartDesign::Body", "App::Part"))
DEFAULT_COLOUR = (0.78, 0.80, 0.84, 1.0)


class ExportError(Exception):
    pass


# ---------------------------------------------------------------- selection


def has_shape(obj) -> bool:
    shape = getattr(obj, "Shape", None)
    return shape is not None and not shape.isNull()


def is_visible(obj) -> bool:
    """Visibility, tolerating a document opened without the GUI."""
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return True
    try:
        return bool(view.Visibility)
    except Exception:
        return True


def has_renderable_shape(obj) -> bool:
    """Whether this object would actually produce triangles.

    Having a Shape is not enough, and assuming it was is what made a real
    PartDesign document fail. Such a document is full of objects that have
    a Shape and tessellate to nothing: origin axes, datum planes, datum
    points, and every sketch. Picking those up produced an empty GLB and
    the unhelpful message "every object tessellated to nothing". Worse, an
    origin axis is an infinite line whose bounding box reads about 2e+98
    mm, which would have made the true size meaningless had anything got
    that far.

    Faces are the test. No faces, no triangles, nothing to look at.
    """
    if getattr(obj, "TypeId", "") in DATUM_TYPES:
        return False
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return False
    try:
        if len(shape.Faces) == 0:
            return False
        # Datum planes are infinite, and report a bounding box around
        # 2e+98 mm while having a perfectly good face. A size check
        # catches those, and anything else unbounded, whatever it is
        # called.
        box = shape.BoundBox
        return max(box.XLength, box.YLength, box.ZLength) < UNBOUNDED_MM
    except Exception:
        return False


def collect_objects(doc=None, selection=None) -> list:
    """The selected objects, or every visible solid when nothing is selected.

    Objects that merely contain others, such as App::Part and bodies whose
    tip is already included, would otherwise be exported twice, so anything
    that is an ancestor of another chosen object is dropped.
    """
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise ExportError("no active document")

    asked_for = list(selection or [])
    chosen = [o for o in asked_for if has_renderable_shape(o)]
    if asked_for and not chosen:
        raise ExportError(
            "nothing to send: the selection has no surfaces to show. "
            "Sketches, datum planes and origin axes have no faces, so "
            "select a body or a solid instead."
        )

    if not chosen:
        chosen = [
            o for o in doc.Objects
            if has_renderable_shape(o) and is_visible(o)
        ]
    if not chosen:
        raise ExportError(
            "nothing to send: this document has no visible object with any "
            "surfaces. Make a body visible, or select one."
        )

    # A Body wins over the features inside it. Without this the Body is
    # treated as a mere ancestor and dropped, and what gets exported is
    # some intermediate Pad rather than the finished part.
    inside_a_container = set()
    for obj in chosen:
        if getattr(obj, "TypeId", "") not in PREFERRED_CONTAINERS:
            continue
        for descendant in _descendants(obj):
            inside_a_container.add(descendant)
    chosen = [o for o in chosen if o.Name not in inside_a_container]

    names = {o.Name for o in chosen}
    pruned = []
    for obj in chosen:
        children = {c.Name for c in getattr(obj, "OutList", [])}
        if children & names:
            # A container whose contents are already in the list.
            continue
        pruned.append(obj)
    return pruned or chosen


def _descendants(obj, seen=None) -> set:
    """Every object below this one, by name, cycles tolerated."""
    seen = seen if seen is not None else set()
    for child in getattr(obj, "OutList", []):
        name = getattr(child, "Name", None)
        if name is None or name in seen:
            continue
        seen.add(name)
        _descendants(child, seen)
    return seen


def global_shape(obj):
    """A copy of the shape positioned where it really is in the document.

    Shape.Placement is the object's own placement, which leaves out any
    App::Part or Link above it. getGlobalPlacement folds those in, and this
    is the step the stock exporter has historically got wrong.
    """
    shape = obj.Shape.copy()
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:
        # Plain objects outside any container have no global placement.
        pass
    return shape


def shape_box(shape):
    """The tightest axis aligned box around what will actually be drawn.

    Shape.BoundBox is only an estimate for curved geometry, and it can be a
    wild one: it is derived from the underlying surfaces rather than the
    trimmed faces, so a mask built from three trimmed spherical faces
    reported 78.6 x 103.9 x 141.0 mm while the part is really
    31.4 x 53.3 x 54.7. Reporting that as the true size made the lens scale
    the part to a third of its size and warn that the proportions did not
    match, which is exactly what it should have done: the size was wrong.

    optimalBoundingBox measures the triangulation instead, which is both
    tight and the same thing the mesh is built from. It can fail or be slow
    on awkward geometry, so the estimate stays as a fallback.
    """
    try:
        return shape.optimalBoundingBox(True)
    except Exception:
        return shape.BoundBox


def bounding_box_doc(objs):
    """The combined tight box of objs, in document millimetres."""
    box = None
    for obj in objs:
        each = shape_box(global_shape(obj))
        box = each if box is None else box.united(each)
    if box is None:
        raise ExportError("no shapes to measure")
    return box


def bounding_box_mm(objs) -> tuple:
    """True size in millimetres, from the globally placed shapes."""
    box = bounding_box_doc(objs)
    return (box.XLength, box.YLength, box.ZLength)


def bottom_centre_mm(box):
    """The point a model is hung from: centred in X and Y, bottom in Z.

    Matches what the lens used to work out for itself by measuring the
    loaded mesh, which is why models now arrive already sitting on this
    point rather than needing to be recentred after the fact.
    """
    return ((box.XMin + box.XMax) / 2.0,
            (box.YMin + box.YMax) / 2.0,
            box.ZMin)


def object_colour(obj):
    """The object's colour, or a neutral grey without a GUI."""
    view = getattr(obj, "ViewObject", None)
    if view is None:
        return DEFAULT_COLOUR
    try:
        colour = view.ShapeColor
        return (float(colour[0]), float(colour[1]), float(colour[2]),
                float(getattr(view, "Transparency", 0)) / 100.0 * -1.0 + 1.0)
    except Exception:
        return DEFAULT_COLOUR


# ------------------------------------------------------------------- glTF


def _shifted(point, shift):
    return (point[0] - shift[0], point[1] - shift[1], point[2] - shift[2])


def to_gltf_space(x_mm, y_mm, z_mm):
    """Millimetres Z up to metres Y up, which is a -90 degree turn about X."""
    return (x_mm / 1000.0, z_mm / 1000.0, -y_mm / 1000.0)


def tessellate(obj, linear, angular, shift=(0.0, 0.0, 0.0)):
    """(positions, normals, indices) for one object, flat shaded.

    Each facet gets its own three vertices so edges stay hard, which is what
    a machined part should look like.

    shift is subtracted from every vertex, in glTF space. It is how the mesh
    ends up written about its own bottom centre instead of about the
    document origin. That matters more than it looks: the lens measures the
    loaded mesh to work out the file's units, and its measurement includes
    the origin, so a part modelled 130 mm away from the document origin
    measured as 130 mm across however small it really was. Writing the mesh
    around the origin makes the measurement right by construction.
    """
    import MeshPart

    shape = global_shape(obj)
    mesh = MeshPart.meshFromShape(
        Shape=shape, LinearDeflection=linear, AngularDeflection=angular,
        Relative=False,
    )
    points, facets = mesh.Topology

    positions = []
    normals = []
    indices = []
    for facet in facets:
        corners = [points[i] for i in facet]
        a, b, c = [_shifted(to_gltf_space(p.x, p.y, p.z), shift)
                   for p in corners]
        # Winding is preserved by the coordinate change, since it is a
        # rotation and not a mirror.
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length == 0:
            continue  # a degenerate facet contributes nothing
        normal = (nx / length, ny / length, nz / length)
        base = len(positions)
        positions.extend((a, b, c))
        normals.extend((normal, normal, normal))
        indices.extend((base, base + 1, base + 2))
    return positions, normals, indices


def _pad4(data: bytes, fill: bytes) -> bytes:
    remainder = len(data) % 4
    return data if remainder == 0 else data + fill * (4 - remainder)


def build_glb(parts) -> bytes:
    """Assemble one GLB from [(name, positions, normals, indices, colour)].

    One mesh and one material per object, so colours survive and the lens
    can still treat the whole thing as a single model.
    """
    buffer = bytearray()
    views = []
    accessors = []
    meshes = []
    materials = []
    nodes = []

    for name, positions, normals, indices, colour in parts:
        if not indices:
            continue
        pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
        nrm_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
        idx_bytes = b"".join(struct.pack("<I", i) for i in indices)

        pos_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(pos_bytes), "target": TARGET_ARRAY_BUFFER})
        buffer += pos_bytes
        nrm_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(nrm_bytes), "target": TARGET_ARRAY_BUFFER})
        buffer += nrm_bytes
        idx_view = len(views)
        views.append({"buffer": 0, "byteOffset": len(buffer),
                      "byteLength": len(idx_bytes),
                      "target": TARGET_ELEMENT_ARRAY_BUFFER})
        buffer += idx_bytes
        while len(buffer) % 4:
            buffer += b"\x00"

        pos_accessor = len(accessors)
        accessors.append({
            "bufferView": pos_view, "componentType": COMPONENT_FLOAT,
            "count": len(positions), "type": "VEC3",
            "min": [min(p[i] for p in positions) for i in range(3)],
            "max": [max(p[i] for p in positions) for i in range(3)],
        })
        nrm_accessor = len(accessors)
        accessors.append({"bufferView": nrm_view, "componentType": COMPONENT_FLOAT,
                          "count": len(normals), "type": "VEC3"})
        idx_accessor = len(accessors)
        accessors.append({"bufferView": idx_view, "componentType": COMPONENT_UINT,
                          "count": len(indices), "type": "SCALAR"})

        material = len(materials)
        materials.append({
            "name": "{0} material".format(name),
            "pbrMetallicRoughness": {
                "baseColorFactor": list(colour),
                "metallicFactor": 0.15,
                "roughnessFactor": 0.65,
            },
            "doubleSided": True,
        })
        mesh = len(meshes)
        meshes.append({"name": name, "primitives": [{
            "attributes": {"POSITION": pos_accessor, "NORMAL": nrm_accessor},
            "indices": idx_accessor, "material": material, "mode": 4,
        }]})
        nodes.append({"mesh": mesh, "name": name})

    if not nodes:
        raise ExportError("every object tessellated to nothing")

    gltf = {
        "asset": {"version": "2.0", "generator": "Holo-CAD SpecsLink"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers": [{"byteLength": len(buffer)}],
        "bufferViews": views,
        "accessors": accessors,
    }

    json_chunk = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(bytes(buffer), b"\x00")
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(json_chunk), b"JSON")
    out += json_chunk
    out += struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
    out += bin_chunk
    return bytes(out)


# ---------------------------------------------------------------- exporters


def export_fallback(objs, quality=DEFAULT_QUALITY, shift=(0.0, 0.0, 0.0)) -> tuple:
    """(glb bytes, triangle count). Works with or without the GUI."""
    linear, angular = QUALITY.get(quality, QUALITY[DEFAULT_QUALITY])
    parts = []
    triangles = 0
    for obj in objs:
        positions, normals, indices = tessellate(obj, linear, angular, shift)
        triangles += len(indices) // 3
        parts.append((obj.Label or obj.Name, positions, normals, indices,
                      object_colour(obj)))
    return build_glb(parts), triangles


def export_stock(objs) -> tuple:
    """(glb bytes, triangle count or 0). GUI only, raises without it."""
    try:
        import ImportGui
    except ImportError as e:
        raise ExportError(
            "the stock glTF exporter needs the FreeCAD GUI, since FreeCAD "
            "registers .glb against ImportGui ({0})".format(e)
        )
    handle, path = tempfile.mkstemp(suffix=".glb", prefix="holocad-")
    os.close(handle)
    try:
        ImportGui.export(objs, path)
        with open(path, "rb") as f:
            glb = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if glb[:4] != b"glTF":
        raise ExportError("the stock exporter did not write a GLB")
    return glb, 0


def export(objs, quality=DEFAULT_QUALITY, prefer_stock=False,
           origin_mm=None) -> dict:
    """Export objs and report what happened.

    Returns id, glb, bbox_mm, triangles, colours, offset_mm and which
    exporter produced it.

    origin_mm is the document point the whole send is measured from, as
    (x, y, z) in document millimetres. With it, every part is written
    about its own bottom centre and reports where that sits relative to
    that shared origin, which is what lets the lens rebuild an assembly
    in the right shape. Each part used to carry its document position
    baked into its vertices and then be recentred by the lens, so in per
    body mode every part was recentred separately and they all landed on
    top of each other.
    """
    if not objs:
        raise ExportError("nothing to export")

    box = bounding_box_doc(objs)
    bbox = (box.XLength, box.YLength, box.ZLength)
    if max(bbox) <= 0:
        raise ExportError("the selection measures zero in every direction")

    centre = bottom_centre_mm(box)
    shift = to_gltf_space(*centre)
    # Where this part hangs, in the lens's own axes and in millimetres,
    # so the lens never has to redo the Z up to Y up turn.
    origin = origin_mm if origin_mm is not None else centre
    offset_mm = [
        centre[0] - origin[0],
        centre[2] - origin[2],
        -(centre[1] - origin[1]),
    ]

    used = "fallback"
    if prefer_stock:
        try:
            glb, triangles = export_stock(objs)
            used = "stock"
            # The stock exporter writes its own vertices, so nothing
            # here can centre them. Report no offset rather than a
            # wrong one.
            offset_mm = [0.0, 0.0, 0.0]
        except ExportError as e:
            FreeCAD.Console.PrintWarning(
                "Holo-CAD: stock exporter unavailable, tessellating instead. "
                "{0}\n".format(e)
            )
            glb, triangles = export_fallback(objs, quality, shift)
    else:
        glb, triangles = export_fallback(objs, quality, shift)

    doc = objs[0].Document
    name = objs[0].Label if len(objs) == 1 else (doc.Label if doc else "model")
    return {
        "id": name,
        "glb": glb,
        "bbox_mm": bbox,
        "triangles": triangles,
        "exporter": used,
        # Also sent beside the model, even though the GLB already carries it
        # as baseColorFactor. Lens Studio instantiates glTF against one
        # template material and the file's own colours did not survive that,
        # so the lens tints each mesh from this list instead of relying on
        # the importer. In the same order as objs, which is the order
        # build_glb writes the meshes in.
        "colours": [[round(c, 6) for c in object_colour(obj)] for obj in objs],
        "offset_mm": [round(v, 6) for v in offset_mm],
    }
