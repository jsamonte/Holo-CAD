"""Push test_cube.glb to the bridge, with no FreeCAD involved.

Standard library only, same as the FreeCAD addon, so the multipart body built
here is the same shape the addon sends.

Run:
    .venv\\Scripts\\python.exe push_test_cube.py
    .venv\\Scripts\\python.exe push_test_cube.py --mode ratio --factor 0.1
    .venv\\Scripts\\python.exe push_test_cube.py --mode fit --target-mm 300
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_GLB = HERE / "test_cube.glb"


def build_multipart(metadata: dict, glb: bytes, filename: str):
    """Return (content_type, body) for a multipart/form-data POST."""
    boundary = "----HoloCAD" + uuid.uuid4().hex
    crlf = b"\r\n"
    parts = []

    parts.append(("--" + boundary).encode())
    parts.append(b'Content-Disposition: form-data; name="metadata"')
    parts.append(b"Content-Type: application/json")
    parts.append(b"")
    parts.append(json.dumps(metadata).encode("utf-8"))

    parts.append(("--" + boundary).encode())
    parts.append(
        'Content-Disposition: form-data; name="glb"; filename="{0}"'.format(
            filename
        ).encode()
    )
    parts.append(b"Content-Type: model/gltf-binary")
    parts.append(b"")
    parts.append(glb)

    parts.append(("--" + boundary + "--").encode())
    parts.append(b"")

    body = crlf.join(parts)
    return "multipart/form-data; boundary=" + boundary, body


def push(bridge_url: str, metadata: dict, glb: bytes, filename: str, timeout=30.0):
    content_type, body = build_multipart(metadata, glb, filename)
    url = bridge_url.rstrip("/") + "/push"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Push a GLB to the Holo-CAD bridge")
    ap.add_argument("--bridge", default=os.environ.get("HOLOCAD_BRIDGE", "http://127.0.0.1:8765"))
    ap.add_argument("--glb", default=str(DEFAULT_GLB))
    ap.add_argument("--id", default="test_cube")
    ap.add_argument("--mode", default="true_size", choices=["true_size", "ratio", "fit"])
    ap.add_argument("--factor", type=float, default=1.0)
    ap.add_argument("--target-mm", type=float, default=None)
    ap.add_argument("--bbox-mm", default="100,100,100", help="x,y,z in mm")
    args = ap.parse_args()

    glb_path = Path(args.glb)
    if not glb_path.is_file():
        raise SystemExit(
            "missing {0}, run make_test_cube.py first".format(glb_path)
        )
    glb = glb_path.read_bytes()

    x, y, z = [float(v) for v in args.bbox_mm.split(",")]
    metadata = {
        "id": args.id,
        "bbox_mm": {"x": x, "y": y, "z": z},
        "scale": {"mode": args.mode, "factor": args.factor, "target_mm": args.target_mm},
        "triangles": 12,
        "source": "push_test_cube.py",
    }

    print("pushing {0} ({1} bytes) to {2}".format(glb_path.name, len(glb), args.bridge))
    try:
        result = push(args.bridge, metadata, glb, glb_path.name)
    except urllib.error.HTTPError as e:
        raise SystemExit("bridge rejected the push: {0} {1}".format(e.code, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        raise SystemExit(
            "could not reach the bridge at {0}: {1}\nIs server.py running?".format(
                args.bridge, e.reason
            )
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
