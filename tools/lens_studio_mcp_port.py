"""Find the running Lens Studio's MCP port and repoint Claude Code at it.

Development tooling, not part of the Holo-CAD pipeline.

Lens Studio picks its MCP port at startup and does not keep the same one
across restarts (observed moving from 8880 to 8800 on this machine), while the
bearer token does survive. A fixed url in .claude.json therefore goes stale
every time the editor restarts, and the server comes back as a connection
failure rather than anything that explains itself.

    py tools\\lens_studio_mcp_port.py            find it and print what to do
    py tools\\lens_studio_mcp_port.py --write    find it and update .claude.json

Lens Studio must be running. Claude Code reads .claude.json at startup, so
restart it afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

CONFIG = os.path.join(os.path.expanduser("~"), ".claude.json")
SERVER_KEY = "lens-studio"
HANDSHAKE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "lens-studio-port-probe", "version": "1.0"},
    },
}


def lens_studio_pids() -> list:
    """PIDs of every running Lens Studio process."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Lens Studio.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    ).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def listening_ports(pids: list) -> list:
    """Loopback TCP ports those processes listen on, lowest first."""
    if not pids:
        return []
    ps = (
        "Get-NetTCPConnection -State Listen | "
        "Where-Object { @(" + ",".join(str(p) for p in pids) + ") -contains $_.OwningProcess } | "
        "ForEach-Object { $_.LocalPort }"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
    ).stdout
    ports = sorted({int(x) for x in out.split() if x.isdigit()})
    return ports


def probe(port: int, token: str | None) -> bool:
    """True when this port answers the MCP handshake."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(
        "http://127.0.0.1:{0}/mcp".format(port),
        data=json.dumps(HANDSHAKE).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 401 means the right endpoint with the wrong or missing token, which
        # still identifies the port.
        return e.code == 401
    except Exception:
        return False
    return "Lens Studio MCP Server" in body or '"serverInfo"' in body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="update .claude.json in place")
    args = ap.parse_args()

    config = json.load(open(CONFIG, encoding="utf-8"))
    entry = config.get("mcpServers", {}).get(SERVER_KEY)
    if entry is None:
        print("no '{0}' server in {1}".format(SERVER_KEY, CONFIG))
        return 1
    token = (entry.get("headers") or {}).get("Authorization")
    current = entry.get("url")

    pids = lens_studio_pids()
    if not pids:
        print("Lens Studio is not running, so it has no MCP port to find.")
        return 1

    ports = listening_ports(pids)
    print("Lens Studio pids {0}, listening on {1}".format(pids, ports))

    found = None
    for port in ports:
        if probe(port, token):
            found = port
            break

    if found is None:
        print("none of those ports answered an MCP handshake.")
        print("Lens Studio may still be starting, or the token may have been rotated.")
        return 1

    url = "http://127.0.0.1:{0}/mcp".format(found)
    print("MCP endpoint: {0}".format(url))
    if url == current:
        print("config already points there, nothing to do")
        return 0

    print("config says:  {0}".format(current))
    if not args.write:
        print("")
        print("run again with --write to update it")
        return 0

    raw = open(CONFIG, encoding="utf-8").read()
    trailing = raw.endswith("\n")
    data = json.loads(raw)
    data["mcpServers"][SERVER_KEY]["url"] = url
    out = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if trailing else "")
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    os.replace(tmp, CONFIG)
    print("updated {0}. Restart Claude Code to pick it up.".format(CONFIG))
    return 0


if __name__ == "__main__":
    sys.exit(main())
