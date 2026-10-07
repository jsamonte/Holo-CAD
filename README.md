# Holo-CAD

Push a model from FreeCAD onto Snapchat Spectacles at its true size, and keep
it updating while you edit. `SPEC.md` is the authority on scope and milestones.

**Two downloads, nothing else to install.** The FreeCAD addon and the Lens
Studio project, both in this repo. The addon serves the model itself, using
only FreeCAD's bundled Python, so there is no separate server, no virtualenv
and nothing to pip install.

```
FreeCAD + SpecsLink addon                         Spectacles lens
  exporter  ->  embedded HTTP/WebSocket server  <--  ws://<laptop>:8765/ws
                                                <--  GET /models/{id}/{v}.glb
```

## Status

| Phase | What it covers | State |
| ----- | -------------- | ----- |
| 1 | Networking proof: lens loads a model and shows it at 1:1 | **done**, measured at 1:1 in the Lens Studio preview |
| 2 | FreeCAD "Send to Spectacles", 100 mm cube acceptance test | **done**, the arithmetic verified headlessly |
| 3 | Ratio and fit modes, dimension overlay, grab and move | **written.** All three scale modes tested end to end; the overlay and grabbing compile but are untried |
| 4 | Live mode on recompute | **written**: document observer, 500 ms debounce, versioned urls |
| 5 | Per-body updates, status panel, polish | **done.** Each object is its own model, only changed ones are re-sent, deleted ones are removed from the lens |

Everything above is verified as far as a laptop can verify it. **Nothing has
been on the glasses yet**, so the physical ruler check, grabbing and the
overlay remain unproven where it counts.

Phase 1 came out right on 2026-10-07, from the lens's own log:

```
HoloCAD ModelLoader: test_cube v12 shown at 1:1  size 100.0 x 100.0 x 100.0 mm
    (true 100.0 x 100.0 x 100.0 mm)  correction 100.0000  meshes 1  load 303 ms
```

`correction 100.0000` is the point of the exercise. The file holds a cube 0.1
units per side, because glTF says metres and Lens Studio works in centimetres,
and the lens measured its way to 100 mm without being told the units.

## Layout

```
SPEC.md                              the spec, plus how this repo differs from it
freecad_addon/SpecsLink/             the addon, standard library only
  InitGui.py                         the workbench, toolbar and menu
  install.ps1                        links it into FreeCAD's Mod folder
  icons/                             toolbar icons
  specslink/holocad_server.py        HTTP + WebSocket, runs inside FreeCAD
  specslink/exporter.py              FreeCAD shapes to GLB, and the true bbox
  specslink/service.py               server, send, and live mode
  specslink/commands.py              the three toolbar commands
  specslink/settings.py              preferences, in FreeCAD's own store
Spectacles/Holo-CAD/                 the Lens Studio project
  Assets/Scripts/BridgeClient.ts     WebSocket client, reconnects on its own
  Assets/Scripts/ModelLoader.ts      downloads, measures, scales, swaps in
  Assets/Scripts/ModelPlacement.ts   grab, move and turn, via SIK
  Assets/Scripts/DimensionOverlay.ts wireframe box labelled in mm
  Assets/Scripts/StatusPanel.ts      optional on-screen status
relay/relay.py                       the public relay, only for publishing
docs/LENS_SETUP.md                   the lens side, and what to check
docs/RELAY.md                        deploying the relay
tools/                               tests and development helpers, not shipped
bridge/                              development harness, not shipped, see below
```

## Installing and running

```powershell
.\freecad_addon\SpecsLink\install.ps1
```

That links the addon into FreeCAD's `Mod` folder with a directory junction,
which needs no administrator rights and leaves the code running from the
repository. It asks FreeCAD where its user folder is rather than guessing,
because FreeCAD 1.1 versions that path (`%APPDATA%\FreeCAD\v1-1\Mod`).
`install.ps1 -Uninstall` removes the link.

Restart FreeCAD, pick **Holo-CAD** from the workbench dropdown, and the server
starts. Three buttons: **Send to Spectacles**, **Live mode** and **Settings**.
The Report view prints the address the lens needs.

To run the server without the workbench, for example to test the lens with no
document open:

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\python.exe" freecad_addon\SpecsLink\specslink\holocad_server.py
```

Either way it prints the url to paste into the lens:

```
Paste this into the lens BridgeClient bridgeUrl input:
  ws://192.168.1.56:8765/ws

  health check   http://192.168.1.56:8765/status
```

That address changes when the laptop moves between networks, so read it off the
banner rather than hardcoding it. `docs/LENS_SETUP.md` covers the lens side.

To check the whole path without the glasses, open the health check url in a
phone browser on the same Wi-Fi. JSON back means the network is fine.

## Every user runs their own server, and it is the addon

Installing the FreeCAD addon is installing the server: FreeCAD serves the
model from the user's own machine. On your own network nothing else is
involved and no model data leaves the LAN. A published lens is the exception,
and needs the relay below.

## Two builds, and the one difference between them

**Development build.** FreeCAD serves from your own machine and the lens
connects over your own Wi-Fi. Nothing central, no model data off the LAN.
That uses plain `ws://`, which needs **Experimental APIs on**, which is
exactly what stops the lens being published. People get it by opening this
project in Lens Studio and sending it to their own glasses.

**Published build.** A published lens may only use `wss://`, so it cannot
reach anybody's laptop directly: that would need a certificate for a hostname
resolving to their machine, and a lens carries one baked-in hostname anyway.
So it talks to a **relay**, one public server with a real certificate, and
FreeCAD uploads to the same relay. A user then installs the addon, installs
the lens from Lens Explorer, and types the pairing code the glasses show.

| | Experimental APIs | `bridgeUrl` | Users install | Somebody hosts |
| --- | --- | --- | --- | --- |
| Development | **on** | `ws://<laptop>:8765/ws` | FreeCAD, Lens Studio | nothing |
| Published | **off** | `wss://relay/lens` | FreeCAD | the relay |

`py tools\wire_lens_scene.py --relay your-relay.example.com` switches the lens
between them. [docs/PUBLISHING.md](docs/PUBLISHING.md) is the full checklist,
[docs/RELAY.md](docs/RELAY.md) covers deploying the relay.

Could each user run the relay themselves? Technically yes, even on their own
laptop, and the addon can already serve TLS directly with `--cert`. But each
of them would need a domain, a certificate renewed every 90 days, a DNS record
re-pointed whenever they change network, and they would have to type their own
hostname into the glasses. That is harder than installing Lens Studio, which
is the thing it would be replacing.

A published lens may only use `wss://` and `https://`, and that needs a
certificate for a hostname that resolves to whichever laptop is running
FreeCAD. One hostname cannot mean a different machine for every user, so a
published build could only ever talk to the machine it was built for. Making
it work for everyone would need a relay server that all models pass through.
Decided against on 2026-10-07: no server, no domain, no running costs, and
no model data leaving the LAN.

## How someone else runs this

1. Clone this repository.
2. Run `freecad_addon\SpecsLink\install.ps1`, then restart FreeCAD and pick
   the **Holo-CAD** workbench.
3. Open `Spectacles/Holo-CAD` in Lens Studio 5.15.4 and send it to their
   glasses.
4. Paste the url FreeCAD prints into the lens's `bridgeUrl`, and allow
   TCP 8765 through the firewall.

Step 3 means they need Lens Studio, which is free and is the standard
Spectacles tool, but it is worth being straight that it is a third thing to
install beyond FreeCAD and this repo.

## Why Experimental APIs stays on

The lens uses plain `ws://` and `http://` on your own network, which Lens
Studio requires **Experimental APIs** to be on for, and that in turn means the
lens cannot be published to Lens Explorer.

That is deliberate. Publishing forbids Experimental APIs, which forces `wss`
and `https`, which needs a certificate for a hostname resolving to **the user's
own laptop**. No one can ship that to strangers: every user would need their
own domain and certificate, and the only way around it is a relay server that
every model would have to travel through.

Snap's
[Open Source category](https://developers.snap.com/spectacles/spectacles-community/community-challenge)
accepts lenses on GitHub including those using Experimental APIs, with no Lens
Explorer publication, and explicitly covers a lens paired with a companion
desktop app, which is exactly this. So distribution is this repository.

What that costs: the lens is not listed in Lens Explorer, and recordings carry
an Experimental Mode watermark.
[Internet Access](https://developers.snap.com/spectacles/about-spectacles-features/apis/internet-access),
[WebSocket](https://developers.snap.com/spectacles/about-spectacles-features/apis/web-socket).

## Let the glasses reach the laptop

Two things can block this and they look identical from the lens.

**1. Windows Firewall.** Once, in an **elevated** PowerShell. Check which
profile your Wi-Fi is on first, because a rule on the wrong profile does
nothing:

```powershell
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory
```

```powershell
New-NetFirewallRule -DisplayName "Holo-CAD 8765" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8765 -Profile Public
```

Swap `Public` for `Private` if that is what the first command reported. To
remove it later, `Remove-NetFirewallRule -DisplayName "Holo-CAD 8765"`.

**2. Client isolation.** Many guest, campus and hotel networks block traffic
between devices on the same SSID, and nothing on the laptop can fix it. The
phone browser check above tells you which problem you have. The fix is a
different network, such as a phone hotspot both devices join.

## Tests

```powershell
.\tools\run_tests.ps1
```

116 checks in five suites. The first three run on FreeCAD's own Python with
nothing installed, which is the interpreter the addon actually runs on. The
two relay suites use the `bridge/` venv, since the relay runs on a server and
is the one piece allowed dependencies.

- `test_holocad_server.py` the embedded server: the RFC 6455 handshake against
  the spec's own test vector, version pruning, broadcast, a frame past the
  64 bit length boundary, and the refusals.
- `test_exporter.py` FreeCAD shapes to GLB: the 100 mm cube acceptance
  arithmetic, the Z up to Y up turn, placements inside an `App::Part`, the
  selection rules, and mesh quality.
- `test_addon.py` the whole chain: a FreeCAD object through `service.send()`
  to a WebSocket client and a downloaded GLB, including all three scale modes,
  per-body sends, a deleted body being removed, and a lens that connects
  late.
- `test_relay.py` the relay: pairing codes, room isolation (a model cannot be
  fetched from the wrong room), rate limiting, pruning, and idle sweeping.
- `test_relay_end_to_end.py` the published path: the addon's upload thread,
  through a running relay, to a lens client that fetches the GLB from the
  relay rather than from FreeCAD.

## Development harness

Not part of the product and not needed to use it.
- `bridge/` is the original standalone aiohttp server, kept because
  `make_test_cube.py`, `push_test_cube.py` and `ws_probe.py` are useful for
  driving the lens without FreeCAD, and because it carries the optional TLS
  support described in `SPEC.md`. Its venv is a development convenience.
- `tools/wire_lens_scene.py` rebuilds the lens scene object over the Lens
  Studio MCP server.
- `tools/lens_studio_mcp_port.py` repoints that MCP server's url, which moves
  on every Lens Studio restart.
- `tools/make_lens_icon.py` writes a 320 x 320 lens icon.

Quick checks against a running server:

```powershell
cd bridge
.\.venv\Scripts\python.exe push_test_cube.py     # sends the 100 mm cube
.\.venv\Scripts\python.exe ws_probe.py           # pretends to be the lens
```

## Notes

- `127.0.0.1` in `bridgeUrl` can never work. The glasses are a separate device
  and need the laptop's LAN address.
- Lens Studio runs its own MCP server on loopback and picks a different port on
  every restart, so a fixed url in `~/.claude.json` goes stale.
  `py tools\lens_studio_mcp_port.py --write` fixes it.
