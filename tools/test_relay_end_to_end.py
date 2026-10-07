"""FreeCAD through a running relay to a lens, with nothing on the same host
pretending to be something else.

    bridge\\.venv\\Scripts\\python.exe tools\\test_relay_end_to_end.py

This is the one test that exercises the published-lens path: the addon's
relay client uploads over HTTP from a worker thread, the relay routes by
pairing code, and a WebSocket client standing in for the lens receives the
announcement and fetches the GLB from the relay rather than from FreeCAD.

Run with the bridge venv, because the relay needs aiohttp. The addon half
imported here is still standard library only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import urllib.request

import aiohttp
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "relay"))
sys.path.insert(0, os.path.join(HERE, "..", "freecad_addon", "SpecsLink"))

import relay as relay_module  # noqa: E402
from specslink import relay_client  # noqa: E402

PORT = 8797
BASE = "http://127.0.0.1:{0}".format(PORT)
FAILURES = []
MESSAGES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def cube_glb(fill: int = 0) -> bytes:
    return b"glTF" + b"\x02\x00\x00\x00" + bytes([fill]) * 32


async def run() -> int:
    relay = relay_module.Relay(BASE)
    runner = web.AppRunner(relay_module.build_app(relay))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()

    logs = []
    warns = []
    client = relay_client.RelayClient(log=logs.append, warn=warns.append)

    try:
        async with aiohttp.ClientSession() as session:
            print("the lens connects and is given a code")
            ws = await session.ws_connect(BASE + "/lens")
            hello = json.loads((await ws.receive()).data)
            code = hello["code"]
            check("a code was issued", len(code) == relay_module.CODE_LENGTH, code)

            print("the user types that code into FreeCAD")
            client.configure(BASE, code.lower())  # lower case on purpose
            check("configured", client.configured)

            print("the addon uploads, off the calling thread")
            before = threading.active_count()
            started = time.time()
            client.push("Bracket", cube_glb(1), {
                "id": "Bracket",
                "bbox_mm": {"x": 120.0, "y": 40.0, "z": 15.0},
                "scale": {"mode": "true_size", "factor": 1.0, "target_mm": None},
                "triangles": 12,
            })
            check("push returned immediately", time.time() - started < 0.1,
                  "{0:.3f}s".format(time.time() - started))
            check("a worker thread took it", threading.active_count() > before)

            update = json.loads((await asyncio.wait_for(ws.receive(), 10)).data)
            check("the lens heard about it", update.get("type") == "model_update",
                  str(update)[:120])
            check("a typed code is case insensitive",
                  "/m/{0}/".format(code) in update["url"], update["url"])
            check("true size survived the trip", update["bbox_mm"]["x"] == 120.0)

            async with session.get(update["url"]) as r:
                blob = await r.read()
            check("the GLB came from the relay, not FreeCAD", blob == cube_glb(1))

            print("only the newest version of a model is uploaded")
            for fill in range(2, 8):
                client.push("Bracket", cube_glb(fill), {
                    "id": "Bracket", "bbox_mm": {"x": 120.0, "y": 40.0, "z": 15.0},
                    "scale": {"mode": "true_size", "factor": 1.0,
                              "target_mm": None},
                    "triangles": 12})
            check("the queue coalesced them", client.pending_count() <= 1,
                  str(client.pending_count()))

            deadline = time.time() + 15
            last = None
            while time.time() < deadline:
                try:
                    message = json.loads(
                        (await asyncio.wait_for(ws.receive(), 2)).data)
                except asyncio.TimeoutError:
                    break
                if message.get("type") == "model_update":
                    last = message
            check("the lens ended on a later version",
                  last is not None and last["version"] > update["version"],
                  str(last and last.get("version")))

            print("removal travels too")
            client.remove("Bracket")
            deadline = time.time() + 10
            removed = None
            while time.time() < deadline and removed is None:
                try:
                    message = json.loads(
                        (await asyncio.wait_for(ws.receive(), 2)).data)
                except asyncio.TimeoutError:
                    break
                if message.get("type") == "model_remove":
                    removed = message
            check("the lens was told to forget it",
                  removed is not None and removed.get("id") == "Bracket",
                  str(removed))

            print("a wrong code is reported, not swallowed")
            warns.clear()
            client.configure(BASE, "BCDFGHJK")
            client.push("Bracket", cube_glb(9), {"id": "Bracket"})
            deadline = time.time() + 10
            while time.time() < deadline and not warns:
                await asyncio.sleep(0.2)
            check("the user is told the code is wrong",
                  any("no lens with code" in w for w in warns), str(warns))

            print("an unreachable relay is reported, not swallowed")
            warns.clear()
            client.configure("http://127.0.0.1:9", "BCDFGHJK")
            client.push("Bracket", cube_glb(9), {"id": "Bracket"})
            deadline = time.time() + 15
            while time.time() < deadline and not warns:
                await asyncio.sleep(0.2)
            check("the user is told the relay is unreachable",
                  any("could not reach the relay" in w for w in warns), str(warns))

            print("reconnecting keeps the pairing")
            await ws.close()
            ws2 = await session.ws_connect(BASE + "/lens?code=" + code)
            hello2 = json.loads((await ws2.receive()).data)
            check("same code after a reconnect", hello2.get("code") == code,
                  str(hello2))
            await ws2.close()
    finally:
        client.stop()
        await runner.cleanup()

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
