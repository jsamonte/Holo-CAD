"""Checks for the relay.

    bridge\\.venv\\Scripts\\python.exe tools\\test_relay.py

Uses the bridge venv because the relay needs aiohttp, which is fine: the
relay runs on a server, not inside FreeCAD, so it is the one piece of this
project allowed dependencies.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp
from aiohttp import web

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "relay")
)

import relay as relay_module  # noqa: E402

PORT = 8795
BASE = "http://127.0.0.1:{0}".format(PORT)
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def cube_glb() -> bytes:
    return b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 8


async def push(session, code, glb, meta):
    async with session.post(
        "{0}/push/{1}".format(BASE, code),
        data=glb,
        headers={"X-Holocad-Meta": json.dumps(meta),
                 "Content-Type": "application/octet-stream"},
    ) as r:
        return r.status, (await r.json() if r.status == 200 else await r.text())


async def run() -> int:
    relay = relay_module.Relay(BASE)
    runner = web.AppRunner(relay_module.build_app(relay))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()

    meta = {"id": "Bracket", "bbox_mm": {"x": 120, "y": 40, "z": 15},
            "scale": {"mode": "true_size", "factor": 1.0, "target_mm": None},
            "triangles": 12}

    try:
        async with aiohttp.ClientSession() as session:
            print("status")
            async with session.get(BASE + "/status") as r:
                status = await r.json()
            check("responds", status.get("status") == "ok")
            check("no rooms yet", status.get("rooms") == 0, str(status.get("rooms")))

            print("pushing to a code nobody holds is refused")
            code, body = await push(session, "BCDFGHJK", cube_glb(), meta)
            check("404 for an unknown code", code == 404, str(code))
            status_code, _ = await push(session, "not-a-code", cube_glb(), meta)
            check("400 for a malformed code", status_code == 400, str(status_code))

            print("a lens gets a code")
            ws = await session.ws_connect(BASE + "/lens")
            hello = json.loads((await ws.receive()).data)
            check("hello carries a code", hello.get("type") == "hello"
                  and len(hello.get("code", "")) == relay_module.CODE_LENGTH,
                  str(hello))
            room_code = hello["code"]
            check("code avoids ambiguous characters",
                  not set(room_code) & set("01OIL AEIOU".replace(" ", "")),
                  room_code)

            print("push reaches the paired lens")
            http_status, result = await push(session, room_code, cube_glb(), meta)
            check("push accepted", http_status == 200, str(result)[:120])
            check("it says how many lenses heard it", result.get("lenses") == 1,
                  str(result.get("lenses")))
            update = json.loads((await ws.receive()).data)
            check("the lens was told", update.get("type") == "model_update")
            check("url is under the pairing code",
                  "/m/{0}/Bracket/1.glb".format(room_code) in update["url"],
                  update["url"])
            check("true size carried", update["bbox_mm"]["x"] == 120.0)

            async with session.get(update["url"]) as r:
                blob = await r.read()
            check("GLB served back", blob == cube_glb())

            print("a second lens on the same code is caught up")
            ws2 = await session.ws_connect(BASE + "/lens?code=" + room_code)
            hello2 = json.loads((await ws2.receive()).data)
            check("same code honoured", hello2.get("code") == room_code)
            caught = json.loads((await ws2.receive()).data)
            check("caught up on the model", caught.get("id") == "Bracket")

            print("rooms are separate")
            other = await session.ws_connect(BASE + "/lens")
            other_hello = json.loads((await other.receive()).data)
            other_code = other_hello["code"]
            check("a different code", other_code != room_code)
            await asyncio.sleep(relay_module.PUSH_MIN_INTERVAL + 0.05)
            await push(session, other_code, cube_glb(),
                       dict(meta, id="Somebody Else"))
            other_update = json.loads((await other.receive()).data)
            check("the other room got its own model",
                  other_update["id"] == "Somebody-Else", other_update["id"])
            check("and this room is untouched",
                  "Somebody-Else" not in relay.room(room_code).latest,
                  str(list(relay.room(room_code).latest)))
            async with session.get(
                    "{0}/m/{1}/Somebody-Else/1.glb".format(BASE, room_code)) as r:
                check("a model cannot be fetched from the wrong room",
                      r.status == 404, str(r.status))

            print("rate limiting")
            await asyncio.sleep(relay_module.PUSH_MIN_INTERVAL + 0.05)
            await push(session, room_code, cube_glb(), meta)
            too_fast, _ = await push(session, room_code, cube_glb(), meta)
            check("a flood is refused", too_fast == 429, str(too_fast))

            print("removal")
            await asyncio.sleep(relay_module.PUSH_MIN_INTERVAL + 0.05)
            async with session.post(
                    "{0}/remove/{1}/Bracket".format(BASE, room_code)) as r:
                removed = await r.json()
            check("remove accepted", removed.get("removed") is True, str(removed))
            # Earlier pushes left updates queued on this socket, so read
            # until the removal rather than assuming it is next.
            message = None
            for _ in range(5):
                candidate = json.loads((await ws.receive()).data)
                if candidate.get("type") == "model_remove":
                    message = candidate
                    break
            check("lens told to forget it", message is not None
                  and message.get("id") == "Bracket", str(message))

            print("junk is refused")
            await asyncio.sleep(relay_module.PUSH_MIN_INTERVAL + 0.05)
            bad, _ = await push(session, room_code, b"not a glb at all", meta)
            check("non GLB refused", bad == 400, str(bad))

            print("versions are pruned")
            room = relay.room(room_code)
            for version in range(1, 8):
                room.put("Many", version, b"glTF" + bytes([version]) * 20, {})
            check("only the newest few kept",
                  len(room.versions["Many"]) == relay_module.KEEP_VERSIONS,
                  str(room.versions["Many"]))
            check("dropped bytes are accounted for", room.bytes_held > 0)

            print("idle rooms are swept, busy ones are not")
            await ws.close()
            await ws2.close()
            await other.close()
            await asyncio.sleep(0.2)
            relay.room(room_code).touched -= relay_module.ROOM_IDLE_SECONDS + 10
            swept = relay.sweep()
            check("the idle room went", swept >= 1, str(swept))
            check("and is gone from the table", relay.room(room_code) is None)
    finally:
        await runner.cleanup()

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
