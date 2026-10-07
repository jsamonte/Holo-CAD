"""HTTP and WebSocket server, standard library only, to run inside FreeCAD.

This is the whole bridge, folded into the addon. The deliverable is two
downloads, the addon and the lens, so there is no separate server to install
and nothing to pip install into FreeCAD's Python.

    FreeCAD addon  --in process-->  this server  <--WebSocket /ws--  lens
                                        |                             |
                                        +--- GET /models/{id}/{v}.glb-+

Deliberately free of FreeCAD imports so it can be run and tested on its own:

    py freecad_addon/SpecsLink/holocad_server.py        serves on 8765

Routes:
    GET  /ws                      WebSocket. On connect, the latest metadata
                                  for every model, so a lens that opens late
                                  catches up. Then one message per push.
    GET  /models/{id}/{v}.glb     the bytes
    GET  /status                  health, for a browser on the same Wi-Fi
    POST /push                    only for testing without FreeCAD. The addon
                                  calls publish() directly.

Models live in memory, a few versions per id. A CAD export is regenerated
whenever FreeCAD recomputes, so there is nothing worth writing to disk, and
nothing is left behind when FreeCAD closes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765
KEEP_VERSIONS = 3
# RFC 6455's magic string, appended to Sec-WebSocket-Key before hashing.
# Transposing one character here fails with "Invalid challenge response" and
# nothing more specific, so it is checked against the RFC's own test vector in
# tools/test_holocad_server.py.
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
MODEL_PATH_RE = re.compile(r"^/models/([A-Za-z0-9_.-]+)/(\d+)\.glb$")

# WebSocket opcodes
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def slugify(name) -> str:
    slug = SLUG_RE.sub("-", str(name)).strip("-.")
    return slug[:64] or "model"


def lan_ip() -> str:
    """The address the glasses should use, via the route to the internet.

    Picking the route this way skips loopback and virtual adapters such as a
    WSL or Hyper-V bridge, which is what makes the printed url usable.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _QuietServer(ThreadingHTTPServer):
    """A dropped connection is normal here, not an error worth a traceback.

    The glasses sleep, Wi-Fi drops, the lens is closed: every one of those
    resets a socket. socketserver's default is to print a full traceback,
    which inside FreeCAD means a wall of red in the Report view each time.
    """

    log = staticmethod(lambda message: None)

    def handle_error(self, request, client_address):
        import sys

        kind = sys.exc_info()[0]
        if kind is not None and issubclass(
            kind, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
        ):
            return
        self.log("Holo-CAD server error from {0}: {1}".format(
            client_address[0], sys.exc_info()[1]))


class ModelStore:
    """Versioned GLBs in memory, a few per id, safe across threads."""

    def __init__(self, keep=KEEP_VERSIONS):
        self.keep = keep
        self._lock = threading.Lock()
        self._blobs = {}   # (id, version) -> bytes
        self._latest = {}  # id -> metadata dict
        self._versions = {}  # id -> [version, ...] newest last

    def next_version(self, model_id: str) -> int:
        with self._lock:
            versions = self._versions.get(model_id)
            return (versions[-1] + 1) if versions else 1

    def put(self, model_id: str, version: int, blob: bytes, metadata: dict) -> None:
        with self._lock:
            self._blobs[(model_id, version)] = blob
            self._latest[model_id] = metadata
            versions = self._versions.setdefault(model_id, [])
            versions.append(version)
            while len(versions) > self.keep:
                self._blobs.pop((model_id, versions.pop(0)), None)

    def get(self, model_id: str, version: int):
        with self._lock:
            return self._blobs.get((model_id, version))

    def latest(self) -> list:
        with self._lock:
            return list(self._latest.values())

    def drop(self, model_id: str) -> bool:
        """Forget a model entirely. True when there was one to forget."""
        with self._lock:
            versions = self._versions.pop(model_id, None)
            self._latest.pop(model_id, None)
            if versions is None:
                return False
            for version in versions:
                self._blobs.pop((model_id, version), None)
            return True

    def ids(self) -> list:
        with self._lock:
            return list(self._latest)

    def summary(self) -> dict:
        with self._lock:
            return {
                mid: {"version": m.get("version"), "triangles": m.get("triangles")}
                for mid, m in self._latest.items()
            }


class WebSocketPeer:
    """One connected lens. Frames out are serialised by a per peer lock."""

    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()
        self.closed = False

    def send_text(self, text: str) -> bool:
        payload = text.encode("utf-8")
        header = bytearray([0x80 | OP_TEXT])
        length = len(payload)
        # Server frames are never masked, and the length is encoded in the
        # smallest of the three forms the protocol allows.
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        with self.lock:
            if self.closed:
                return False
            try:
                self.handler.wfile.write(bytes(header) + payload)
                self.handler.wfile.flush()
                return True
            except OSError:
                self.closed = True
                return False

    def send_close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.handler.wfile.write(bytes([0x80 | OP_CLOSE, 0]))
                self.handler.wfile.flush()
            except OSError:
                pass


class Bridge:
    """What the addon talks to: publish a model, and the lenses hear about it."""

    def __init__(self, port=DEFAULT_PORT, log=None):
        self.port = port
        self.store = ModelStore()
        self.peers = set()
        self._peers_lock = threading.Lock()
        self._httpd = None
        self._thread = None
        self.started_at = time.time()
        self.host_ip = lan_ip()
        self._log = log or (lambda message: None)

    # ---- lifecycle ----

    def start(self) -> str:
        """Serve on a daemon thread so FreeCAD's UI never waits on it."""
        if self._httpd is not None:
            return self.socket_url()
        handler = _make_handler(self)
        self._httpd = _QuietServer(("0.0.0.0", self.port), handler)
        self._httpd.daemon_threads = True
        self._httpd.log = self._log
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="HoloCAD server", daemon=True
        )
        self._thread.start()
        self.host_ip = lan_ip()
        self._log("Holo-CAD serving on {0}".format(self.socket_url()))
        return self.socket_url()

    def stop(self) -> None:
        with self._peers_lock:
            peers = list(self.peers)
            self.peers.clear()
        for peer in peers:
            peer.send_close()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._log("Holo-CAD server stopped")

    @property
    def running(self) -> bool:
        return self._httpd is not None

    # ---- urls ----

    def base_url(self) -> str:
        return "http://{0}:{1}".format(self.host_ip, self.port)

    def socket_url(self) -> str:
        return "ws://{0}:{1}/ws".format(self.host_ip, self.port)

    def model_url(self, model_id: str, version: int) -> str:
        return "{0}/models/{1}/{2}.glb".format(self.base_url(), model_id, version)

    # ---- the one call the addon makes ----

    def publish(self, model_id: str, glb: bytes, bbox_mm, scale=None, triangles=0) -> dict:
        """Store a GLB and tell every connected lens about it.

        bbox_mm is the true size in millimetres, which is what the lens
        measures against. Returns the metadata that went out.
        """
        if not glb[:4] == b"glTF":
            raise ValueError("not a GLB, missing the glTF magic")

        model_id = slugify(model_id)
        version = self.store.next_version(model_id)
        scale = scale or {"mode": "true_size", "factor": 1.0, "target_mm": None}

        metadata = {
            "type": "model_update",
            "id": model_id,
            "version": version,
            "url": self.model_url(model_id, version),
            "bbox_mm": {
                "x": float(bbox_mm[0]),
                "y": float(bbox_mm[1]),
                "z": float(bbox_mm[2]),
            },
            "scale": {
                "mode": scale.get("mode", "true_size"),
                "factor": float(scale.get("factor", 1.0)),
                "target_mm": scale.get("target_mm"),
            },
            "triangles": int(triangles),
            "pushed_ms": int(time.time() * 1000),
            "bytes": len(glb),
        }
        self.store.put(model_id, version, glb, metadata)
        reached = self.broadcast(metadata)
        self._log(
            "Holo-CAD sent {0} v{1}, {2:.1f} KB, {3:.1f} x {4:.1f} x {5:.1f} mm, "
            "to {6} lens(es)".format(
                model_id, version, len(glb) / 1024.0,
                metadata["bbox_mm"]["x"], metadata["bbox_mm"]["y"], metadata["bbox_mm"]["z"],
                reached,
            )
        )
        return metadata

    def remove(self, model_id: str) -> bool:
        """Tell every lens to forget a model, for instance after a delete.

        Without this, deleting a body in FreeCAD leaves it hanging in the
        air, because the lens has no way to know it is gone.
        """
        model_id = slugify(model_id)
        if not self.store.drop(model_id):
            return False
        self.broadcast({"type": "model_remove", "id": model_id})
        self._log("Holo-CAD removed {0}".format(model_id))
        return True

    def known_ids(self) -> list:
        return self.store.ids()

    def broadcast(self, message: dict) -> int:
        text = json.dumps(message)
        with self._peers_lock:
            peers = list(self.peers)
        alive = 0
        for peer in peers:
            if peer.send_text(text):
                alive += 1
            else:
                self.remove_peer(peer)
        return alive

    def add_peer(self, peer) -> None:
        with self._peers_lock:
            self.peers.add(peer)

    def remove_peer(self, peer) -> None:
        with self._peers_lock:
            self.peers.discard(peer)

    @property
    def lens_count(self) -> int:
        with self._peers_lock:
            return len(self.peers)


def _make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "HoloCAD/1.0"

        def log_message(self, fmt, *args):
            # BaseHTTPRequestHandler logs to stderr, which inside FreeCAD is
            # noise at best. The bridge logs what matters instead.
            pass

        # ---- helpers ----

        def _send(self, code, body=b"", content_type="text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, code, payload):
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        # ---- routes ----

        def do_GET(self):
            if self.path == "/ws":
                self._websocket()
                return
            if self.path == "/status":
                self._json(200, {
                    "status": "ok",
                    "uptime_s": round(time.time() - bridge.started_at, 1),
                    "lenses": bridge.lens_count,
                    "host_ip": bridge.host_ip,
                    "port": bridge.port,
                    "socket_url": bridge.socket_url(),
                    "models": bridge.store.summary(),
                })
                return
            match = MODEL_PATH_RE.match(self.path)
            if match:
                blob = bridge.store.get(match.group(1), int(match.group(2)))
                if blob is None:
                    self._send(404, b"no such model version")
                    return
                self._send(200, blob, "model/gltf-binary", {"Cache-Control": "no-store"})
                return
            self._send(404, b"not found")

        def do_POST(self):
            """Only for testing without FreeCAD. The addon calls publish()."""
            if self.path != "/push":
                self._send(404, b"not found")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            raw_meta = self.headers.get("X-Holocad-Meta")
            glb = body
            metadata = {}

            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                parsed = _parse_multipart(body, content_type)
                if parsed is None:
                    self._send(400, b"could not parse the multipart body")
                    return
                raw_meta, glb = parsed
            if raw_meta:
                try:
                    metadata = json.loads(raw_meta)
                except ValueError as e:
                    self._send(400, "metadata is not valid JSON: {0}".format(e).encode())
                    return
            if not glb:
                self._send(400, b"no glb bytes in request")
                return

            bbox = metadata.get("bbox_mm") or {}
            try:
                out = bridge.publish(
                    metadata.get("id", "model"),
                    glb,
                    (bbox.get("x", 0), bbox.get("y", 0), bbox.get("z", 0)),
                    metadata.get("scale"),
                    metadata.get("triangles", 0),
                )
            except ValueError as e:
                self._send(400, str(e).encode())
                return
            payload = {"ok": True}
            payload.update(out)
            self._json(200, payload)

        # ---- websocket ----

        def _websocket(self):
            key = self.headers.get("Sec-WebSocket-Key")
            upgrade = (self.headers.get("Upgrade") or "").lower()
            if not key or upgrade != "websocket":
                self._send(400, b"/ws expects a WebSocket upgrade")
                return

            accept = base64.b64encode(
                hashlib.sha1((key + WS_GUID).encode()).digest()
            ).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()

            peer = WebSocketPeer(self)
            bridge.add_peer(peer)
            bridge._log("Holo-CAD lens connected from {0} ({1} total)".format(
                self.client_address[0], bridge.lens_count))

            peer.send_text(json.dumps(
                {"type": "hello", "server": "holo-cad", "protocol": 1}))
            # Catch a lens up on everything already exported, so opening the
            # lens after the export still shows the model.
            for message in bridge.store.latest():
                peer.send_text(json.dumps(message))

            try:
                self._pump(peer)
            finally:
                bridge.remove_peer(peer)
                peer.closed = True
                bridge._log("Holo-CAD lens disconnected ({0} left)".format(
                    bridge.lens_count))

        def _pump(self, peer):
            """Read frames until the lens goes away. Replies to pings."""
            while not peer.closed:
                frame = _read_frame(self.rfile)
                if frame is None:
                    return
                opcode, payload = frame
                if opcode == OP_CLOSE:
                    peer.send_close()
                    return
                if opcode == OP_PING:
                    with peer.lock:
                        try:
                            self.wfile.write(
                                bytes([0x80 | OP_PONG, len(payload)]) + payload)
                            self.wfile.flush()
                        except OSError:
                            return
                # Text frames from the lens are keepalives and hello. Nothing
                # the lens says changes what gets exported, so they are read
                # and dropped.

    return Handler


def _read_exactly(stream, count):
    data = b""
    while len(data) < count:
        try:
            chunk = stream.read(count - len(data))
        except OSError:
            return None
        if not chunk:
            return None
        data += chunk
    return data


def _read_frame(stream):
    """One WebSocket frame, unmasked. Returns (opcode, payload) or None.

    Client frames are always masked, which is the only case this has to
    handle, and the lens never sends anything big enough to fragment.
    """
    header = _read_exactly(stream, 2)
    if header is None:
        return None
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F

    if length == 126:
        extended = _read_exactly(stream, 2)
        if extended is None:
            return None
        length = struct.unpack(">H", extended)[0]
    elif length == 127:
        extended = _read_exactly(stream, 8)
        if extended is None:
            return None
        length = struct.unpack(">Q", extended)[0]

    mask = _read_exactly(stream, 4) if masked else None
    if masked and mask is None:
        return None

    payload = _read_exactly(stream, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _parse_multipart(body: bytes, content_type: str):
    """Pull the metadata and glb parts out, without the email module.

    Only the two part shape this project sends is supported, which keeps it
    short and avoids email.parser's habit of mangling binary payloads.
    """
    marker = "boundary="
    index = content_type.find(marker)
    if index < 0:
        return None
    boundary = content_type[index + len(marker):].strip().strip('"')
    sep = b"--" + boundary.encode()

    metadata = None
    glb = None
    for chunk in body.split(sep):
        if chunk in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        split = chunk.find(b"\r\n\r\n")
        if split < 0:
            continue
        headers = chunk[:split].decode("utf-8", "replace")
        payload = chunk[split + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if 'name="metadata"' in headers:
            metadata = payload.decode("utf-8", "replace")
        elif 'name="glb"' in headers:
            glb = payload
    if glb is None:
        return None
    return metadata, glb


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Holo-CAD server, standalone")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    bridge = Bridge(args.port, log=lambda message: print(message, flush=True))
    bridge.start()
    print("")
    print("Paste this into the lens BridgeClient bridgeUrl input:")
    print("  {0}".format(bridge.socket_url()))
    print("")
    print("  health check   {0}/status".format(bridge.base_url()))
    print("", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        bridge.stop()


if __name__ == "__main__":
    main()
