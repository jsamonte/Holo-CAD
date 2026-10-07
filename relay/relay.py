"""Holo-CAD relay.

Runs on a public host so a published lens can reach a FreeCAD that is sitting
behind someone's home router. Without it the lens would have to connect
straight to the user's laptop, which needs a certificate for a hostname
pointing at that laptop, which cannot be shipped to strangers.

    FreeCAD addon  --https POST /push-->  RELAY  <--wss /lens--  Lens
                                            |                      |
                                            +-- https GET /m/... <-+

Both ends dial out, so no user has to open a port or touch a firewall.

Pairing. The lens connects first and the relay gives it a short code, which
the lens shows on screen. The user types that code into FreeCAD. The code is
the room: a push carrying it reaches exactly the lenses holding it. Typing
happens on the laptop keyboard rather than on the glasses, which is the only
reason this is bearable.

What this server is not: it is not a place to keep anything. A room exists
while a lens holds it, models are kept in memory, only the last few versions
survive, and everything is dropped once the room goes quiet. A relay that
accumulated other people's CAD would be a liability.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import time

from aiohttp import WSMsgType, web

DEFAULT_PORT = 8080
CODE_LENGTH = 8
# No vowels, so no code is ever an unfortunate word, and no 0/O or 1/I/L,
# because someone is reading this off a headset and typing it on a laptop.
CODE_ALPHABET = "23456789BCDFGHJKMNPQRSTVWXYZ"
KEEP_VERSIONS = 3
ROOM_IDLE_SECONDS = 15 * 60
MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_ROOM_BYTES = 192 * 1024 * 1024
PUSH_MIN_INTERVAL = 0.25

CODE_RE = re.compile(r"^[{0}]{{{1}}}$".format(CODE_ALPHABET, CODE_LENGTH))
MODEL_PATH_RE = re.compile(
    r"^/m/([{0}]{{{1}}})/([A-Za-z0-9_.-]+)/(\d+)\.glb$".format(
        CODE_ALPHABET, CODE_LENGTH)
)
SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def slugify(name) -> str:
    slug = SLUG_RE.sub("-", str(name)).strip("-.")
    return slug[:64] or "model"


class Room:
    """One pairing: the lenses holding a code, and the models pushed to it."""

    def __init__(self, code: str):
        self.code = code
        self.sockets = set()
        self.blobs = {}      # (model_id, version) -> bytes
        self.versions = {}   # model_id -> [version, ...] oldest first
        self.latest = {}     # model_id -> metadata
        self.created = time.time()
        self.touched = time.time()
        self.last_push = 0.0
        self.bytes_held = 0

    def touch(self) -> None:
        self.touched = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.touched

    def next_version(self, model_id: str) -> int:
        versions = self.versions.get(model_id)
        return (versions[-1] + 1) if versions else 1

    def put(self, model_id: str, version: int, blob: bytes, metadata: dict) -> None:
        self.blobs[(model_id, version)] = blob
        self.bytes_held += len(blob)
        self.latest[model_id] = metadata
        versions = self.versions.setdefault(model_id, [])
        versions.append(version)
        while len(versions) > KEEP_VERSIONS:
            dropped = self.blobs.pop((model_id, versions.pop(0)), b"")
            self.bytes_held -= len(dropped)
        # A room that keeps growing is a room someone is abusing. Shed the
        # oldest models rather than letting one pairing eat the host.
        while self.bytes_held > MAX_ROOM_BYTES and self.versions:
            oldest = min(self.versions, key=lambda k: self.versions[k][0])
            self.drop(oldest)

    def drop(self, model_id: str) -> bool:
        versions = self.versions.pop(model_id, None)
        self.latest.pop(model_id, None)
        if versions is None:
            return False
        for version in versions:
            dropped = self.blobs.pop((model_id, version), b"")
            self.bytes_held -= len(dropped)
        return True

    def get(self, model_id: str, version: int):
        return self.blobs.get((model_id, version))


class Relay:
    def __init__(self, public_base: str):
        self.public_base = public_base.rstrip("/")
        self.rooms = {}
        self.started = time.time()
        self.pushes = 0

    # ---- rooms ----

    def new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self.rooms:
                return code

    def room(self, code: str, create: bool = False):
        existing = self.rooms.get(code)
        if existing is not None:
            return existing
        if not create:
            return None
        room = Room(code)
        self.rooms[code] = room
        return room

    def sweep(self) -> int:
        """Forget rooms nobody is using. Called on a timer and on each push."""
        gone = []
        for code, room in self.rooms.items():
            if room.sockets:
                continue
            if room.idle_seconds > ROOM_IDLE_SECONDS:
                gone.append(code)
        for code in gone:
            del self.rooms[code]
        return len(gone)

    def model_url(self, code: str, model_id: str, version: int) -> str:
        return "{0}/m/{1}/{2}/{3}.glb".format(
            self.public_base, code, model_id, version)

    async def broadcast(self, room: Room, message: dict) -> int:
        payload = json.dumps(message)
        reached = 0
        for ws in list(room.sockets):
            try:
                await ws.send_str(payload)
                reached += 1
            except Exception:
                room.sockets.discard(ws)
        return reached

    # ---- routes ----

    async def handle_status(self, request: web.Request) -> web.Response:
        self.sweep()
        return web.json_response({
            "status": "ok",
            "uptime_s": round(time.time() - self.started, 1),
            "rooms": len(self.rooms),
            "lenses": sum(len(r.sockets) for r in self.rooms.values()),
            "pushes": self.pushes,
            "bytes_held": sum(r.bytes_held for r in self.rooms.values()),
        })

    async def handle_lens(self, request: web.Request) -> web.WebSocketResponse:
        """A lens connects, is given a code, and waits to be paired."""
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)

        # Reconnecting with the code it already has keeps a pairing alive
        # across the socket drops that happen whenever the glasses sleep.
        wanted = request.query.get("code", "").upper()
        if wanted and CODE_RE.match(wanted):
            room = self.room(wanted, create=True)
        else:
            room = self.room(self.new_code(), create=True)

        room.sockets.add(ws)
        room.touch()
        print("lens joined {0} ({1} in room, {2} rooms)".format(
            room.code, len(room.sockets), len(self.rooms)), flush=True)

        await ws.send_str(json.dumps({
            "type": "hello", "server": "holo-cad-relay", "protocol": 1,
            "code": room.code,
        }))
        for message in room.latest.values():
            await ws.send_str(json.dumps(message))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    room.touch()
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            room.sockets.discard(ws)
            room.touch()
            print("lens left {0} ({1} left)".format(room.code, len(room.sockets)),
                  flush=True)
        return ws

    async def handle_push(self, request: web.Request) -> web.Response:
        """FreeCAD pushes a model into a room. Outbound from the user's side."""
        code = (request.match_info.get("code") or "").upper()
        if not CODE_RE.match(code):
            raise web.HTTPBadRequest(text="that is not a pairing code")
        room = self.room(code)
        if room is None:
            # Deliberately not created here. A code only exists because a
            # lens asked for one, so an unknown code means a typo, and
            # saying so beats silently accepting pushes nobody will see.
            raise web.HTTPNotFound(
                text="no lens is waiting with that code. Check the code on "
                     "the glasses, and that the lens is still open.")

        now = time.time()
        if now - room.last_push < PUSH_MIN_INTERVAL:
            raise web.HTTPTooManyRequests(text="slow down")
        room.last_push = now

        if request.content_length and request.content_length > MAX_GLB_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_GLB_BYTES, actual_size=request.content_length)

        meta_raw = request.headers.get("X-Holocad-Meta")
        glb = await request.read()
        if len(glb) > MAX_GLB_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_GLB_BYTES, actual_size=len(glb))
        if glb[:4] != b"glTF":
            raise web.HTTPBadRequest(text="payload is not a GLB")
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except ValueError as e:
            raise web.HTTPBadRequest(text="metadata is not valid JSON: {0}".format(e))

        model_id = slugify(meta.get("id", "model"))
        version = room.next_version(model_id)
        bbox = meta.get("bbox_mm") or {}
        scale = meta.get("scale") or {}
        message = {
            "type": "model_update",
            "id": model_id,
            "version": version,
            "url": self.model_url(code, model_id, version),
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
            "pushed_ms": int(now * 1000),
            "bytes": len(glb),
        }
        room.put(model_id, version, glb, message)
        room.touch()
        self.pushes += 1
        reached = await self.broadcast(room, message)
        self.sweep()
        print("push {0} {1} v{2} {3:.1f} KB -> {4} lens(es)".format(
            code, model_id, version, len(glb) / 1024.0, reached), flush=True)
        out = {"ok": True, "lenses": reached}
        out.update(message)
        return web.json_response(out)

    async def handle_remove(self, request: web.Request) -> web.Response:
        code = (request.match_info.get("code") or "").upper()
        room = self.room(code) if CODE_RE.match(code) else None
        if room is None:
            raise web.HTTPNotFound(text="no such pairing")
        model_id = slugify(request.match_info["id"])
        if not room.drop(model_id):
            return web.json_response({"ok": True, "removed": False})
        room.touch()
        await self.broadcast(room, {"type": "model_remove", "id": model_id})
        return web.json_response({"ok": True, "removed": True})

    async def handle_model(self, request: web.Request) -> web.StreamResponse:
        match = MODEL_PATH_RE.match(request.path)
        if match is None:
            raise web.HTTPNotFound(text="not found")
        code, model_id, version = match.group(1), match.group(2), int(match.group(3))
        room = self.room(code)
        if room is None:
            raise web.HTTPNotFound(text="no such pairing")
        blob = room.get(model_id, version)
        if blob is None:
            raise web.HTTPNotFound(text="no such model version")
        room.touch()
        return web.Response(
            body=blob,
            headers={
                "Content-Type": "model/gltf-binary",
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )


async def _sweeper(relay: Relay) -> None:
    while True:
        await asyncio.sleep(60)
        dropped = relay.sweep()
        if dropped:
            print("swept {0} idle room(s), {1} left".format(
                dropped, len(relay.rooms)), flush=True)


def build_app(relay: Relay) -> web.Application:
    app = web.Application(client_max_size=MAX_GLB_BYTES + 1024 * 1024)
    app.add_routes([
        web.get("/status", relay.handle_status),
        web.get("/lens", relay.handle_lens),
        web.post("/push/{code}", relay.handle_push),
        web.post("/remove/{code}/{id}", relay.handle_remove),
        web.get("/m/{code}/{id}/{version}.glb", relay.handle_model),
    ])

    async def start_sweeper(app):
        app["sweeper"] = asyncio.create_task(_sweeper(relay))

    async def stop_sweeper(app):
        app["sweeper"].cancel()

    app.on_startup.append(start_sweeper)
    app.on_cleanup.append(stop_sweeper)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Holo-CAD relay")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", DEFAULT_PORT)))
    ap.add_argument(
        "--public-base",
        default=os.environ.get("HOLOCAD_PUBLIC_BASE", ""),
        help=(
            "The https base the glasses will use, for example "
            "https://relay.example.com. Required: it goes into every model "
            "url, and a published lens can only fetch https."
        ),
    )
    args = ap.parse_args()
    if not args.public_base:
        ap.error("--public-base is required, for example https://relay.example.com")
    if not args.public_base.startswith("https://"):
        print("WARNING: --public-base is not https, so a published lens will "
              "refuse these urls. Fine for local testing only.", flush=True)

    relay = Relay(args.public_base)
    print("Holo-CAD relay")
    print("  listening on 0.0.0.0:{0}".format(args.port))
    print("  public base {0}".format(relay.public_base))
    print("  lens endpoint {0}/lens".format(
        relay.public_base.replace("https://", "wss://").replace("http://", "ws://")))
    print("", flush=True)
    web.run_app(build_app(relay), host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
