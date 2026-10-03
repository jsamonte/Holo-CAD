"""Write test_cube.glb, a cube that is exactly 100 x 100 x 100 mm.

The glTF spec says linear distances are meters, so a 100 mm cube is 0.1 units
on a side in the file. The lens never trusts that, it measures the loaded mesh
and compares it to the bbox_mm in the metadata, so this file is also the test
of that correction path.

Standard library only, on purpose: this is the prototype of the fallback
exporter that has to run inside FreeCAD's bundled Python.

Run:
    .venv\\Scripts\\python.exe make_test_cube.py
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

SIZE_MM = 100.0
OUT = Path(__file__).resolve().parent / "test_cube.glb"

COMPONENT_FLOAT = 5126
COMPONENT_USHORT = 5123
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY_BUFFER = 34963


def cube(half: float):
    """Return (positions, normals, indices) for a flat shaded cube.

    Each face gets its own four vertices so the normals stay hard edged.
    """
    faces = [
        ((0.0, 0.0, 1.0), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
        ((0.0, 0.0, -1.0), [(1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)]),
        ((1.0, 0.0, 0.0), [(1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)]),
        ((-1.0, 0.0, 0.0), [(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)]),
        ((0.0, 1.0, 0.0), [(-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)]),
        ((0.0, -1.0, 0.0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
    ]
    positions = []
    normals = []
    indices = []
    for normal, corners in faces:
        base = len(positions)
        for c in corners:
            positions.append((c[0] * half, c[1] * half, c[2] * half))
            normals.append(normal)
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return positions, normals, indices


def pad4(data: bytes, fill: bytes) -> bytes:
    remainder = len(data) % 4
    if remainder == 0:
        return data
    return data + fill * (4 - remainder)


def write_glb(path: Path, positions, normals, indices, color) -> None:
    pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
    nrm_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
    idx_bytes = b"".join(struct.pack("<H", i) for i in indices)
    idx_bytes = pad4(idx_bytes, b"\x00")

    bin_chunk = pos_bytes + nrm_bytes + idx_bytes

    min_pos = [min(p[i] for p in positions) for i in range(3)]
    max_pos = [max(p[i] for p in positions) for i in range(3)]

    gltf = {
        "asset": {"version": "2.0", "generator": "Holo-CAD make_test_cube"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "TestCube100mm"}],
        "meshes": [
            {
                "name": "TestCube100mm",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1},
                        "indices": 2,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "TestCubeMaterial",
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(color),
                    "metallicFactor": 0.1,
                    "roughnessFactor": 0.6,
                },
                "doubleSided": True,
            }
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": len(pos_bytes),
                "target": TARGET_ARRAY_BUFFER,
            },
            {
                "buffer": 0,
                "byteOffset": len(pos_bytes),
                "byteLength": len(nrm_bytes),
                "target": TARGET_ARRAY_BUFFER,
            },
            {
                "buffer": 0,
                "byteOffset": len(pos_bytes) + len(nrm_bytes),
                "byteLength": len(idx_bytes),
                "target": TARGET_ELEMENT_ARRAY_BUFFER,
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": COMPONENT_FLOAT,
                "count": len(positions),
                "type": "VEC3",
                "min": min_pos,
                "max": max_pos,
            },
            {
                "bufferView": 1,
                "componentType": COMPONENT_FLOAT,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": COMPONENT_USHORT,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
    }

    json_chunk = pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = pad4(bin_chunk, b"\x00")

    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(json_chunk), b"JSON"))
        f.write(json_chunk)
        f.write(struct.pack("<I4s", len(bin_chunk), b"BIN\x00"))
        f.write(bin_chunk)


def main() -> None:
    half_m = (SIZE_MM / 1000.0) / 2.0
    positions, normals, indices = cube(half_m)
    write_glb(OUT, positions, normals, indices, (0.25, 0.55, 0.95, 1.0))
    print(
        "wrote {0} ({1} bytes), {2:.0f} mm cube, {3} triangles".format(
            OUT, OUT.stat().st_size, SIZE_MM, len(indices) // 3
        )
    )
    print("glTF extent per axis: {0:.3f} units (meters)".format(half_m * 2.0))


if __name__ == "__main__":
    main()
