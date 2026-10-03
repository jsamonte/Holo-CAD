# Holo-CAD

Push a model from FreeCAD onto Snapchat Spectacles at its true size, and keep it
updating while you edit. `SPEC.md` is the authority on scope and milestones.

```
FreeCAD addon  --HTTP POST /push-->  Bridge (laptop)  <--WebSocket /ws--  Spectacles lens
                                         |                                      |
                                         +---- HTTP GET /models/{id}/{v}.glb <---+
```

## Status

| Phase | What it covers | State |
| ----- | -------------- | ----- |
| 1 | Networking proof: bridge serves `test_cube.glb`, lens shows it at 1:1 | bridge done and tested, lens scripts written, scene wiring pending |
| 2 | FreeCAD "Send to Spectacles", 100 mm cube acceptance test | not started |
| 3 | Ratio and fit modes, dimension overlay, grab and move | not started |
| 4 | Live mode on recompute | not started |
| 5 | Per-body updates, status panel, polish | not started |

## Layout

```
SPEC.md                         the spec, plus how this repo differs from it
bridge/                         the laptop server
  server.py                     aiohttp, one port, serves GLB and the WebSocket
  make_test_cube.py             writes test_cube.glb, exactly 100 mm per side
  push_test_cube.py             pushes it, standard library only
  ws_probe.py                   stands in for the lens, proves the network path
  models/                       pushed GLBs, {id}/{version}.glb
freecad_addon/SpecsLink/        FreeCAD workbench (phase 2)
Spectacles/Holo-CAD/            the Lens Studio project
  Assets/Scripts/BridgeClient.ts
  Assets/Scripts/ModelLoader.ts
  Assets/Scripts/StatusPanel.ts
docs/LENS_SETUP.md              by-hand steps in the Lens Studio editor
tools/                          development helpers, not part of the pipeline
```

## Run the bridge

```powershell
cd C:\GitHub\Holo-CAD\bridge
py -3.14 -m venv .venv                      # first time only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # first time only
.\.venv\Scripts\python.exe server.py
```

It binds `0.0.0.0:8765` and prints the exact url to paste into the lens:

```
Paste this into the lens BridgeClient bridgeUrl input:
  ws://172.16.9.63:8765/ws

  health check   http://172.16.9.63:8765/status
```

That LAN address changes when the laptop moves between networks, so read it off
the banner each time rather than hardcoding it.

Options: `--port 8765`, and `--host-ip` to force the address written into model
urls when the auto-detected one is wrong (it skips loopback and the WSL and
Hyper-V adapters, but a VPN can still fool it).

## Test the bridge without FreeCAD or the glasses

Three terminals, or run the probe in the background:

```powershell
cd C:\GitHub\Holo-CAD\bridge
.\.venv\Scripts\python.exe make_test_cube.py     # writes test_cube.glb
.\.venv\Scripts\python.exe server.py             # terminal 1
.\.venv\Scripts\python.exe ws_probe.py           # terminal 2, pretends to be the lens
.\.venv\Scripts\python.exe push_test_cube.py     # terminal 3
```

The probe should print the `model_update` message and confirm it downloaded
1600 bytes beginning with the glTF magic. Other scale modes:

```powershell
.\.venv\Scripts\python.exe push_test_cube.py --mode ratio --factor 0.1
.\.venv\Scripts\python.exe push_test_cube.py --mode fit --target-mm 300
```

A quick curl push, no multipart needed:

```powershell
curl.exe -s -X POST http://127.0.0.1:8765/push `
  -H "X-Holocad-Meta: {\"id\":\"curl_cube\",\"bbox_mm\":{\"x\":100,\"y\":100,\"z\":100},\"triangles\":12}" `
  --data-binary "@test_cube.glb"
```

## Let the glasses reach the laptop

Two separate things can block this, and they look identical from the lens.

**1. Windows Firewall.** Run this once in an **elevated** PowerShell. Check
which network profile your Wi-Fi is on first, because a rule on the wrong
profile does nothing:

```powershell
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory
```

If the Wi-Fi says `Private`, the spec's rule is the right one:

```powershell
New-NetFirewallRule -DisplayName "Holo-CAD bridge 8765" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
```

If it says `Public` (which is what this laptop reported), either set the network
to Private in Settings > Network and internet > Wi-Fi > the network's
properties, or scope the rule to Public instead:

```powershell
New-NetFirewallRule -DisplayName "Holo-CAD bridge 8765" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8765 -Profile Public
```

To remove it later:

```powershell
Remove-NetFirewallRule -DisplayName "Holo-CAD bridge 8765"
```

**2. Client isolation.** Plenty of Wi-Fi networks, in particular guest,
campus and hotel networks, block traffic between devices on the same SSID. No
setting on the laptop can fix that. Check it from a phone on the same Wi-Fi: open
`http://<laptop LAN ip>:8765/status` in the phone's browser. JSON back means the
path is clear, a hang or a refusal means the network is isolating clients, and
the fix is a different network such as a phone hotspot that both the laptop and
the glasses join.

## The lens

`docs/LENS_SETUP.md` has the by-hand steps in the Lens Studio editor. The short
version:

1. Open `Spectacles/Holo-CAD` in Lens Studio 5.15.4.
2. Put `BridgeClient`, `ModelLoader` and `StatusPanel` on one scene object.
3. Paste the bridge's `ws://...` url into BridgeClient's `bridgeUrl`.
4. Assign a PBR material to ModelLoader's `material`.
5. Turn on Experimental APIs in Project Settings, which plain `ws://` and
   `http://` both need.
6. Send to the glasses, start the bridge, push the test cube.

The lens never trusts the units in the file. It measures the loaded mesh and
compares it to the `bbox_mm` FreeCAD reported, so an exporter writing the wrong
units still comes out the right size. The Logger line to look for is:

```
HoloCAD ModelLoader: test_cube v1 shown at 1:1  size 100.0 x 100.0 x 100.0 mm  (true 100.0 x 100.0 x 100.0 mm)  correction 100.0000  meshes 1  load 412 ms
```

## Notes

- `127.0.0.1` in `bridgeUrl` can never work. The glasses are a separate device
  and need the laptop's LAN address.
- Lens Studio runs its own MCP server on loopback and **picks a different port
  on each restart** (seen moving from 8880 to 8800), while the bearer token
  survives. So a fixed url in `~/.claude.json` goes stale every time the editor
  restarts, and it fails as a bare connection error. `tools/lens_studio_mcp_port.py`
  finds the live port by probing what Lens Studio is listening on, and with
  `--write` repoints the config. Development tooling, not part of the pipeline:

  ```powershell
  py tools\lens_studio_mcp_port.py --write   # then restart Claude Code
  ```

  The bridge stays on 8765, clear of whatever Lens Studio grabs.
