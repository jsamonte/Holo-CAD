"""Holo-CAD bridge server.

Receives GLB models from the FreeCAD addon over HTTP, serves them to the
Spectacles lens over HTTP, and notifies connected lenses over a WebSocket.

Run:
    .venv\\Scripts\\python.exe server.py
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import time
from pathlib import Path

from aiohttp import WSMsgType, web

DEFAULT_PORT = 8765
KEEP_VERSIONS = 5
MODELS_DIR = Path(__file__).resolve().parent / "models"

SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
VERSION_FILE_RE = re.compile(r"^(\d+)\.glb$")


def slugify(name: str) -> str:
    """Reduce a name to something safe to use as a directory name."""
    slug = SLUG_RE.sub("-", str(name)).strip("-.")
    return slug[:64] or "model"


def lan_ip() -> str:
    """Best guess at the LAN address the glasses should connect to.

    Uses the route the OS would pick to reach the internet, which skips
    loopback and virtual adapters such as the WSL or Hyper-V bridge.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def all_ipv4() -> list:
    """Every IPv4 address on this host, for the startup banner."""
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in out and not addr.startswith("127."):
                out.append(addr)
    except socket.gaierror:
        pass
    return out


class ModelStore:
    """Versioned GLB files on disk, one directory per model id."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.latest = {}

    def next_version(self, model_id: str) -> int:
        d = self.root / model_id
        highest = 0
        if d.is_dir():
            for f in d.iterdir():
                m = VERSION_FILE_RE.match(f.name)
                if m:
                    highest = max(highest, int(m.group(1)))
        known = self.latest.get(model_id, {}).get("version", 0)
        return max(highest, known) + 1

    def write(self, model_id: str, version: int, data: bytes) -> Path:
        d = self.root / model_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / "{0}.glb".format(version)
        path.write_bytes(data)
        self._prune(d)
        return path

    def _prune(self, d: Path) -> None:
        versions = []
        for f in d.iterdir():
            m = VERSION_FILE_RE.match(f.name)
            if m:
                versions.append((int(m.group(1)), f))
        versions.sort(reverse=True)
        for _, f in versions[KEEP_VERSIONS:]:
            try:
                f.unlink()
            except OSError:
                pass

    def path_for(self, model_id: str, version: int) -> Path:
        return self.root / model_id / "{0}.glb".format(version)


class Bridge:
    def __init__(self, port: int, host_ip: str):
        self.port = port
        self.host_ip = host_ip
        self.store = ModelStore(MODELS_DIR)
        self.sockets = set()
        self.started = time.time()

    def model_url(self, model_id: str, version: int) -> str:
        return "http://{0}:{1}/models/{2}/{3}.glb".format(
            self.host_ip, self.port, model_id, version
        )

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message)
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    # ---------------- routes ----------------

    async def handle_status(self, request: web.Request) -> web.Response:
        models = {}
        for mid, m in self.store.latest.items():
            models[mid] = {"version": m["version"], "triangles": m.get("triangles")}
        return web.json_response(
            {
                "status": "ok",
                "uptime_s": round(time.time() - self.started, 1),
                "lenses": len(self.sockets),
                "host_ip": self.host_ip,
                "port": self.port,
                "models": models,
            }
        )

    async def handle_push(self, request: web.Request) -> web.Response:
        """Accept a GLB plus metadata.

        Two request shapes are accepted:
          multipart/form-data with a "metadata" JSON part and a "glb" file part
          any other content type, treated as raw GLB bytes with the metadata in
          an X-Holocad-Meta header, which keeps quick curl tests easy
        """
        meta_raw = None
        glb = None

        if request.content_type == "multipart/form-data":
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "metadata":
                    meta_raw = (await part.read(decode=False)).decode("utf-8")
                elif part.name == "glb":
                    glb = await part.read(decode=False)
        else:
            meta_raw = request.headers.get("X-Holocad-Meta")
            glb = await request.read()

        if not glb:
            raise web.HTTPBadRequest(text="no glb bytes in request")
        if glb[:4] != b"glTF":
            raise web.HTTPBadRequest(text="payload is not a GLB (missing glTF magic)")

        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except json.JSONDecodeError as e:
            raise web.HTTPBadRequest(text="metadata is not valid JSON: {0}".format(e))

        model_id = slugify(meta.get("id", "model"))
        version = self.store.next_version(model_id)
        self.store.write(model_id, version, glb)

        bbox = meta.get("bbox_mm") or {}
        scale = meta.get("scale") or {}
        message = {
            "type": "model_update",
            "id": model_id,
            "version": version,
            "url": self.model_url(model_id, version),
            "bbox_mm": {
                "x": float(bbox.get("x", 0.0)),
                "y": float(bbox.get("y", 0.0)),
                "z": float(bbox.get("z", 0.0)),
            },
            "scale": {
                "mode": scale.get("mode", "true_size"),
                "factor": float(scale.get("factor", 1.0)),
                "target_mm": scale.get("target_mm"),
            },
            "triangles": int(meta.get("triangles", 0)),
            "pushed_ms": int(time.time() * 1000),
            "bytes": len(glb),
        }
        self.store.latest[model_id] = message
        await self.broadcast(message)

        print(
            "push  {0} v{1}  {2:.1f} KB  bbox {3:.1f} x {4:.1f} x {5:.1f} mm  "
            "mode {6}  -> {7} lens(es)".format(
                model_id,
                version,
                len(glb) / 1024.0,
                message["bbox_mm"]["x"],
                message["bbox_mm"]["y"],
                message["bbox_mm"]["z"],
                message["scale"]["mode"],
                len(self.sockets),
            ),
            flush=True,
        )
        out = {"ok": True}
        out.update(message)
        return web.json_response(out)

    async def handle_model(self, request: web.Request) -> web.StreamResponse:
        model_id = slugify(request.match_info["id"])
        try:
            version = int(request.match_info["version"])
        except ValueError:
            raise web.HTTPBadRequest(text="version must be an integer")
        path = self.store.path_for(model_id, version)
        if not path.is_file():
            raise web.HTTPNotFound(
                text="no such model version: {0}/{1}".format(model_id, version)
            )
        print("serve {0} v{1} to {2}".format(model_id, version, request.remote), flush=True)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "model/gltf-binary",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)
        self.sockets.add(ws)
        print(
            "lens connected from {0} ({1} total)".format(request.remote, len(self.sockets)),
            flush=True,
        )

        await ws.send_str(
            json.dumps({"type": "hello", "server": "holo-cad-bridge", "protocol": 1})
        )
        # Catch a freshly opened lens up on everything we already hold.
        for message in self.store.latest.values():
            await ws.send_str(json.dumps(message))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    print("lens says: {0}".format(msg.data[:400]), flush=True)
                elif msg.type == WSMsgType.ERROR:
                    print("lens socket error: {0}".format(ws.exception()), flush=True)
        finally:
            self.sockets.discard(ws)
            print("lens disconnected ({0} left)".format(len(self.sockets)), flush=True)
        return ws


def build_app(bridge: Bridge) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.add_routes(
        [
            web.get("/status", bridge.handle_status),
            web.post("/push", bridge.handle_push),
            web.get("/models/{id}/{version}.glb", bridge.handle_model),
            web.get("/ws", bridge.handle_ws),
        ]
    )
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Holo-CAD bridge server")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--host-ip",
        default=None,
        help="LAN address to put in model URLs, default is auto detected",
    )
    args = ap.parse_args()

    host_ip = args.host_ip or lan_ip()
    bridge = Bridge(args.port, host_ip)

    print("Holo-CAD bridge")
    print("  listening on 0.0.0.0:{0}".format(args.port))
    print("  model store {0}".format(MODELS_DIR))
    print("")
    print("Paste this into the lens BridgeClient bridgeUrl input:")
    print("  ws://{0}:{1}/ws".format(host_ip, args.port))
    print("")
    print("  health check   http://{0}:{1}/status".format(host_ip, args.port))
    others = [ip for ip in all_ipv4() if ip != host_ip]
    if others:
        print("  other addresses on this host: {0}".format(", ".join(others)))
        print("  (if the glasses cannot reach the one above, try these)")
    print("", flush=True)

    web.run_app(build_app(bridge), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
