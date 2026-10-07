"""Checks for the in-addon server, runnable with FreeCAD's own Python.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe" tools\\test_holocad_server.py

Standard library only, like the thing it tests, so it runs anywhere the addon
runs and needs nothing installed.

The WebSocket handshake is checked against RFC 6455's own test vector because
of how that bug presented: one transposed character in the magic GUID, and the
only symptom was a client saying "Invalid challenge response" with nothing to
say which end was wrong.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.request

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "freecad_addon", "SpecsLink")
)

from specslink import holocad_server as hs  # noqa: E402

PORT = 8791
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def cube_glb() -> bytes:
    """The smallest thing that passes the glTF magic check."""
    return b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 8


def ws_handshake(sock, host, port):
    """Open a WebSocket by hand and return the server's first frames."""
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        "GET /ws HTTP/1.1\r\n"
        "Host: {0}:{1}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: {2}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).format(host, port, key)
    sock.sendall(request.encode())

    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("server closed during the handshake")
        buffer += chunk
    head, rest = buffer.split(b"\r\n\r\n", 1)
    headers = head.decode("latin-1")

    expected = base64.b64encode(
        hashlib.sha1((key + hs.WS_GUID).encode()).digest()
    ).decode()
    return headers, expected, rest


def read_frame_from(sock, pending=b""):
    """Read one unmasked server frame, returning (payload, leftover)."""
    buffer = pending

    def need(count):
        nonlocal buffer
        while len(buffer) < count:
            chunk = sock.recv(4096)
            if not chunk:
                raise AssertionError("server closed mid frame")
            buffer += chunk

    need(2)
    length = buffer[1] & 0x7F
    offset = 2
    if length == 126:
        need(4)
        length = struct.unpack(">H", buffer[2:4])[0]
        offset = 4
    elif length == 127:
        need(10)
        length = struct.unpack(">Q", buffer[2:10])[0]
        offset = 10
    masked = bool(buffer[1] & 0x80)
    need(offset + length)
    payload = buffer[offset:offset + length]
    return payload, buffer[offset + length:], masked


def main() -> int:
    print("handshake maths")
    accept = base64.b64encode(
        hashlib.sha1(("dGhlIHNhbXBsZSBub25jZQ==" + hs.WS_GUID).encode()).digest()
    ).decode()
    check("RFC 6455 test vector", accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
          "got {0}".format(accept))

    print("store")
    store = hs.ModelStore(keep=2)
    for version in (1, 2, 3):
        store.put("part", version, bytes([version]), {"version": version})
    check("keeps the newest versions", store.get("part", 3) == b"\x03")
    check("drops the oldest", store.get("part", 1) is None)
    check("latest is one per id", len(store.latest()) == 1)

    print("slugify")
    check("strips separators", hs.slugify("My Part/v2") == "My-Part-v2")
    check("never empty", hs.slugify("///") == "model")

    bridge = hs.Bridge(PORT)
    bridge.start()
    time.sleep(0.4)
    base = "http://127.0.0.1:{0}".format(PORT)
    try:
        print("http")
        with urllib.request.urlopen(base + "/status", timeout=5) as r:
            status = json.loads(r.read().decode())
        check("status responds", status.get("status") == "ok")
        check("reports no lenses yet", status.get("lenses") == 0)

        print("publish and websocket")
        sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        try:
            headers, expected, rest = ws_handshake(sock, "127.0.0.1", PORT)
            check("101 upgrade", "101" in headers.split("\r\n")[0], headers.split("\r\n")[0])
            check("challenge accepted", expected in headers,
                  "server did not return the expected accept")

            payload, rest, masked = read_frame_from(sock, rest)
            hello = json.loads(payload.decode())
            check("hello frame first", hello.get("type") == "hello")
            check("server frames are unmasked", not masked)

            meta = bridge.publish("Test Part", cube_glb(), (100.0, 40.0, 15.0),
                                  triangles=12)
            check("slugified id", meta["id"] == "Test-Part", meta["id"])
            check("version starts at 1", meta["version"] == 1)
            check("bbox carried through", meta["bbox_mm"]["y"] == 40.0)

            payload, rest, _ = read_frame_from(sock, rest)
            update = json.loads(payload.decode())
            check("broadcast reached the socket", update.get("type") == "model_update")
            check("url matches the model", update["url"].endswith("/models/Test-Part/1.glb"))

            with urllib.request.urlopen(update["url"].replace(
                    bridge.host_ip, "127.0.0.1"), timeout=5) as r:
                blob = r.read()
            check("glb served back intact", blob == cube_glb())

            with urllib.request.urlopen(base + "/status", timeout=5) as r:
                status = json.loads(r.read().decode())
            check("status counts the lens", status.get("lenses") == 1, str(status.get("lenses")))
        finally:
            sock.close()

        print("a large payload crosses the 126 byte length boundary")
        big = {"type": "model_update", "pad": "x" * 70000, "id": "big",
               "version": 1, "url": "", "bbox_mm": {}, "scale": {}, "triangles": 0}
        sock = socket.create_connection(("127.0.0.1", PORT), timeout=5)
        try:
            headers, expected, rest = ws_handshake(sock, "127.0.0.1", PORT)
            payload, rest, _ = read_frame_from(sock, rest)  # hello
            payload, rest, _ = read_frame_from(sock, rest)  # catch up
            time.sleep(0.2)
            bridge.broadcast(big)
            payload, rest, _ = read_frame_from(sock, rest)
            check("64 bit length frame survives", json.loads(payload.decode())["pad"]
                  == "x" * 70000)
        finally:
            sock.close()

        print("rejects nonsense")
        try:
            bridge.publish("bad", b"not a glb at all", (1, 1, 1))
            check("non GLB rejected", False, "publish accepted it")
        except ValueError:
            check("non GLB rejected", True)

        request = urllib.request.Request(base + "/models/nope/9.glb")
        try:
            urllib.request.urlopen(request, timeout=5)
            check("404 for a missing model", False, "got a response")
        except urllib.error.HTTPError as e:
            check("404 for a missing model", e.code == 404, str(e.code))
    finally:
        bridge.stop()

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
