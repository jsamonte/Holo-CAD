"""Stand in for the lens: connect to the bridge over WebSocket, print every
message, and download each model that gets announced.

This proves the bridge and the network path without the glasses in the loop.

Run:
    .venv\\Scripts\\python.exe ws_probe.py
    .venv\\Scripts\\python.exe ws_probe.py --url ws://172.16.9.63:8765/ws --seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aiohttp


async def run(url: str, seconds: float, download: bool, insecure: bool = False) -> int:
    seen = 0
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    # insecure is for testing a TLS setup with a throwaway certificate. The
    # glasses will not accept one, so it proves plumbing, never readiness.
    connector = aiohttp.TCPConnector(ssl=False) if insecure else None
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        print("connecting to {0}".format(url), flush=True)
        async with session.ws_connect(url) as ws:
            print("connected", flush=True)
            deadline = asyncio.get_event_loop().time() + seconds
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    msg = await ws.receive(timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if msg.type is aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    print("message: {0}".format(json.dumps(data)), flush=True)
                    if data.get("type") == "model_update":
                        seen += 1
                        if download:
                            async with session.get(data["url"]) as r:
                                body = await r.read()
                                ok = body[:4] == b"glTF"
                                print(
                                    "  downloaded {0} bytes from {1}, glTF magic {2}".format(
                                        len(body), data["url"], "ok" if ok else "MISSING"
                                    )
                                )
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print("socket closed: {0}".format(msg.type), flush=True)
                    break
    print("done, {0} model_update message(s)".format(seen), flush=True)
    return 0 if seen else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="WebSocket probe for the Holo-CAD bridge")
    ap.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification, for testing a cert the machine does not trust")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.seconds, not args.no_download, args.insecure)))


if __name__ == "__main__":
    main()
