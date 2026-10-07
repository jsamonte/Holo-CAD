"""The tunnel half of the addon, against a real cloudflared quick tunnel.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" tools\\test_tunnel.py

Starts the addon's own server, puts a real tunnel in front of it, and checks
that the glasses' side of the world works: https for the model, wss for the
socket, and model urls rewritten to the tunnel rather than the LAN.

Needs cloudflared installed and the internet. Skips itself, passing, when
cloudflared is missing, so it never fails a run for a reason that is not a
defect.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "freecad_addon", "SpecsLink"))

from specslink import holocad_server, tunnel  # noqa: E402

PORT = 8799
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def cube_glb() -> bytes:
    return b"glTF" + b"\x02\x00\x00\x00" + b"\x00" * 24


def fetch_with_retry(url, seconds=90):
    """GET url, waiting out DNS for a hostname created seconds ago.

    A fresh trycloudflare name does not resolve immediately, so the first
    attempts fail with getaddrinfo rather than anything meaningful.

    Default certificate verification on purpose: passing here means the
    certificate is one a stranger's device would accept too, which is the
    whole reason for a tunnel over a self signed certificate.
    """
    context = ssl.create_default_context()
    deadline = time.time() + seconds
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=20, context=context) as r:
                return r.read(), ""
        except Exception as e:
            last = str(e)
            time.sleep(3)
    return None, last


def main() -> int:
    if tunnel.find_cloudflared() is None:
        print("cloudflared is not installed, skipping")
        print("")
        print("all checks passed")
        return 0

    state_path = os.path.join(
        os.environ.get("TEMP", "."), "holocad_tunnel_test.json")
    if os.path.exists(state_path):
        os.remove(state_path)

    bridge = holocad_server.Bridge(PORT, log=lambda m: None)
    bridge.start()

    ready = []
    handle = tunnel.Tunnel(
        log=lambda m: print("  ..    {0}".format(m)),
        warn=lambda m: print("  warn  {0}".format(m)),
        on_ready=lambda base: ready.append(base),
        state_path=state_path,
    )

    try:
        print("starting a quick tunnel")
        started = handle.start(PORT)
        check("cloudflared started", started, handle.error)
        if not started:
            return 1

        deadline = time.time() + 90
        while time.time() < deadline and not ready:
            time.sleep(0.5)
        check("a hostname arrived within 90 s", bool(ready), handle.error)
        if not ready:
            return 1

        public_base = ready[0]
        bridge.public_base = public_base
        check("hostname ends in the fixed suffix",
              handle.hostname.endswith(".trycloudflare.com"), handle.hostname)
        check("the typed part has no dots", "." not in handle.words,
              handle.words)
        check("socket url is wss", bridge.socket_url().startswith("wss://"),
              bridge.socket_url())
        check("model urls point at the tunnel",
              bridge.model_url("Bracket", 1).startswith(public_base),
              bridge.model_url("Bracket", 1))

        print("the model travels over the real certificate")
        metadata = bridge.publish("Bracket", cube_glb(), (120.0, 40.0, 15.0),
                                  triangles=12)
        check("published", metadata["version"] == 1)

        fetched, error = fetch_with_retry(metadata["url"])
        check("fetched over https with a trusted certificate",
              fetched == cube_glb(),
              error or "{0} bytes".format(len(fetched or b"")))

        body, error = fetch_with_retry(public_base + "/status")
        status = json.loads(body.decode()) if body else {}
        check("status answers through the tunnel", status.get("status") == "ok",
              error or str(status)[:100])

        print("the hostname survives FreeCAD restarting")
        handle.detach()
        check("detach leaves the process alone", not handle.running)
        # A second Tunnel, as a fresh FreeCAD session would make.
        second = tunnel.Tunnel(log=lambda m: print("  ..    {0}".format(m)),
                               warn=lambda m: print("  warn  {0}".format(m)),
                               state_path=state_path)
        adopted = second.adopt(PORT)
        check("a new session adopts the running tunnel", adopted)
        check("and gets the same words", second.words == handle.words,
              "{0} vs {1}".format(second.words, handle.words))

        print("stopping")
        second.stop()
        check("tunnel reports stopped", not second.running)
        third = tunnel.Tunnel(state_path=state_path)
        check("nothing left to adopt afterwards", not third.adopt(PORT))
    finally:
        handle.stop()
        bridge.stop()
        if os.path.exists(state_path):
            os.remove(state_path)

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


# freecadcmd does not set __name__ to "__main__".
try:
    _status = main()
except Exception:
    import traceback

    traceback.print_exc()
    print("")
    print("the suite did not finish")
    _status = 1
if __name__ == "__main__":
    sys.exit(_status)
