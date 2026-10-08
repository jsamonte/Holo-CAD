# Holo-CAD

See your FreeCAD model floating in front of you at its real size, on Snapchat
Spectacles, and watch it update while you edit.

One inch in FreeCAD is one inch on the glasses. The lens measures the model
rather than trusting the units in the file, so a 25.4 mm cube draws 25.4 mm
across and you can hold a ruler up to it.

**Two things to install: this FreeCAD addon, and this lens.** The addon is the
server. It runs inside FreeCAD on your own machine using only FreeCAD's
bundled Python, so there is no separate program, no virtual environment and
nothing to pip install.

```
FreeCAD + the Holo-CAD addon                      your Spectacles
  tessellate to GLB                               connect, listen
  serve it from FreeCAD itself   <------------>   download, measure, show at 1:1
```

Proven end to end on real glasses on 2026-10-07.

## Before you start

| You need | Notes |
| --- | --- |
| **FreeCAD 1.1** or newer | Where your model lives. The addon runs inside it. |
| **Lens Studio 5.15.4** | Free, from Snap. How the lens gets onto your glasses. |
| **Spectacles** (2024) | Paired with Lens Studio already. |
| **cloudflared** | Only for the tunnel route below. Free, no account. |

Windows is the tested platform. The addon itself is plain Python with no
Windows-specific code, but `install.ps1` is PowerShell.

## Install

### 1. The FreeCAD addon

Download or clone this repository, then in PowerShell from the repository
folder:

```powershell
.\freecad_addon\SpecsLink\install.ps1
```

That is all it does: it makes a directory junction from FreeCAD's `Mod` folder
to the addon, so the addon runs from where you put it. No administrator rights
needed. It asks FreeCAD where its user folder is rather than guessing, because
FreeCAD 1.1 versions that path (`%APPDATA%\FreeCAD\v1-1\Mod`).

**Restart FreeCAD**, then pick **Holo-CAD** from the workbench dropdown at the
top of the window. Four buttons appear: **Send to Spectacles**, **Live mode**,
**Share over the internet** and **Settings**.

If **Holo-CAD** is not in that dropdown, the addon did not load. See
[When the workbench does not appear](#when-the-workbench-does-not-appear).

To remove it later: `.\freecad_addon\SpecsLink\install.ps1 -Uninstall`

### 2. The lens

Open `Spectacles/Holo-CAD` in Lens Studio, then send it to your glasses with
Lens Studio's own **Send to Spectacles** button.

The scene is already wired, so there is nothing to assemble. Two settings
depend on which route you pick, and the next section covers them.

## Pick a route: same Wi-Fi, or a tunnel

The glasses have to reach FreeCAD. There are two ways, and the difference is
one address and one checkbox.

| | Same Wi-Fi | Tunnel |
| --- | --- | --- |
| Address | `ws://<your laptop>:8765/ws` | `wss://<four words>.trycloudflare.com/ws` |
| Experimental APIs | **on** | **off** |
| Extra install | none | cloudflared |
| Firewall rule | yes | no |
| Works on a guest or hotel network | often not | yes |
| Can be published to Lens Explorer | no | yes |

**Start with the tunnel.** It is less setup in practice, because it skips both
the firewall rule and the networks that stop devices reaching each other.
Experimental APIs lives in Lens Studio under Project Settings.

### The tunnel route

Install cloudflared once:

```powershell
winget install Cloudflare.cloudflared
```

Then in FreeCAD, press **Share over the internet**. It takes up to twenty
seconds, and the Report view then prints:

```
TUNNEL READY. Type these words into the lens:
    mount-playlist-yarn-orientation
full address https://mount-playlist-yarn-orientation.trycloudflare.com
```

Put those four words into the lens. The `.trycloudflare.com` part is already
built in, so the words are all you type. On the glasses the lens prompts for
them and remembers them afterwards. There is no account and nothing to set up
at Cloudflare.

Pressing the button again closes the tunnel. Closing FreeCAD leaves it open on
purpose, so the words you typed keep working into the next session.

One wrinkle worth knowing: **the words change every time the tunnel restarts.**
A quick tunnel gets a random name. While you leave it running the words stay
good, but after a reboot you will be typing new ones. The lens notices an
address that has stopped answering, gives up on it after three tries, and asks
again.

### The same Wi-Fi route

FreeCAD prints the address when the workbench starts:

```
Paste this into the lens BridgeClient bridgeUrl input:
  ws://192.168.1.56:8765/ws

  health check   http://192.168.1.56:8765/status
```

Put that into the lens's `BridgeClient` `bridgeUrl` input in Lens Studio, and
turn **Experimental APIs** on in Project Settings. That address changes when
your laptop moves between networks, so read it off the banner rather than
writing it down.

`127.0.0.1` can never work here. The glasses are a separate device and need
your laptop's address on the network.

You also need to let the glasses through Windows Firewall, once, in an
**elevated** PowerShell. Check which profile your Wi-Fi is on first, because a
rule on the wrong profile does nothing at all:

```powershell
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory
```

```powershell
New-NetFirewallRule -DisplayName "Holo-CAD 8765" -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 8765 -Profile Public
```

Swap `Public` for whatever that first command reported. To undo it:
`Remove-NetFirewallRule -DisplayName "Holo-CAD 8765"`.

To check the network before involving the glasses at all, open that health
check url in a phone browser on the same Wi-Fi. JSON back means the path is
clear.

## Use it

1. Open your model in FreeCAD.
2. Select the bodies you want. **With nothing selected it sends every visible
   solid in the document.**
3. Press **Send to Spectacles**.
4. Look through the glasses.

The Report view names what went, with its real size, so you can check it
against your model:

```
Holo-CAD: sent Body at 1:1 true size, 120.0 x 40.0 x 15.0 mm, 10302 triangles
```

**Live mode** re-sends on every recompute, half a second after you stop
editing, so the model on the glasses follows your work. Each body is tracked
on its own: editing one bracket re-sends that bracket rather than the whole
assembly, and deleting one removes it from the glasses.

### On the glasses

- **Grab it** with one hand to move and turn it.
- **Pinch with both hands** and pull apart or together to resize, from 0.05x
  to 20x.
- The size label under the model updates as you scale. **Tap the label** to
  snap back to true 1:1, for that one part.
- Both panels can be **dragged** wherever you want them.

Parts arrive in the colours FreeCAD gives them, one colour per body, taken
from the object's Shape colour.

### The controls panel

A second draggable panel carries four buttons, for the things that are
otherwise hard to undo once a part is somewhere you cannot reach:

| Button | What it does |
| --- | --- |
| **Reset size** | Every part back to true size, the assembly included. |
| **Bring to me** | Everything back in front of you, sizes left alone. |
| **Reset all** | Both of the above, plus rotation. |
| **Grab: parts / whole** | Whether a grab moves one part or the whole assembly. |

**Grab: whole** is what to use for an assembly you want to position as one
piece. Only one of the two modes is live at a time, because a grabbable
assembly wrapped around grabbable parts makes a pinch ambiguous, and you
would reach for the assembly and move one bracket instead.

### Scale modes

**Settings** has three, for when true size is not what you want:

- **1:1** true size, the default, and the point of the whole thing.
- **Ratio**, for example 1:10, for something that would not fit in the room.
- **Fit**, which scales whatever you send to a size you name, so a large
  assembly and a small part both arrive the same size on the desk.

## When something does not work

**The lens says `No FreeCAD yet`.** It has no address. On the tunnel route,
press **Share over the internet** in FreeCAD and type the words it prints.

**The lens says `bridge: retrying`.** It has an address and cannot reach it.
On the same Wi-Fi route this is almost always the firewall rule, or a network
that stops devices reaching each other. Many guest, campus and hotel networks
do the latter and nothing on your laptop can change it. The phone browser
check above tells you which of the two you have, and the fix for the second is
a different network, such as a phone hotspot both devices join. On the tunnel
route, check the tunnel is still open and the words still match.

**The lens says `model: waiting for a push`.** Good news, it is connected.
Press **Send to Spectacles**.

**`[WinError 10048] Only one usage of each socket address`.** Something else
already has port 8765, almost always another FreeCAD or a server left running
from earlier. Close it, or change the port in **Settings**. The addon refuses
to share the port rather than quietly starting a second server that gets none
of the traffic, which is a far more confusing failure.

**`every object tessellated to nothing`.** Everything picked for export turned
out to have no surface. Select the bodies you actually want and send again.
With nothing selected the addon skips sketches, datum planes, datum points and
origin axes, and prefers a `PartDesign::Body` over the features inside it, but
an unusual document can still defeat it.

### When the workbench does not appear

A FreeCAD addon that fails to load does not announce itself. It is simply
missing from the workbench dropdown while every other addon loads fine.

1. Check the junction exists. `%APPDATA%\FreeCAD\v1-1\Mod\SpecsLink` should
   point at this repository.
2. Turn on **View > Panels > Report view** in FreeCAD, restart it, and read
   what scrolls past. An error from `InitGui.py` shows up there.
3. Confirm the addon loads at all, without the GUI:

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\python.exe" tools\test_initgui.py
```

## How the sizing works

This is the part worth understanding, because it is what makes the model
trustworthy.

FreeCAD works in millimetres internally whatever display units you have set.
glTF declares metres. Lens Studio works in centimetres. Rather than convert
through that chain and hope, the lens **measures** the model: it instantiates
with unit conversion off, takes the combined bounding box of every mesh, and
derives a correction from the true size the addon sent alongside it.

For a 100 mm cube the correction comes out at exactly 100, and that is right,
because glTF says metres while Lens Studio counts centimetres. Here is the
lens's own log from the first time it came out:

```
HoloCAD ModelLoader: test_cube v12 shown at 1:1  size 100.0 x 100.0 x 100.0 mm
    (true 100.0 x 100.0 x 100.0 mm)  correction 100.0000  meshes 1  load 303 ms
```

The lens compares sorted dimensions and warns past 1%, which catches a dropped
placement without firing on the Z-up to Y-up axis swap.

## What is proven, and what is not

The pipeline works on real hardware: a model from a real FreeCAD document
reaches the glasses and is shown at true size.

Verified by automated tests, on FreeCAD's own interpreter: the sizing
arithmetic including the 1 inch case, all three scale modes, per-body updates,
removals, versioning, the WebSocket handshake against the RFC's test vector,
and the tunnel end to end with the certificate verified.

Not yet measured: sustained frame rate on the glasses, and the lens bundle
size against the 25 MB publishing limit. The lens has not been submitted to
Lens Explorer.

## Layout

```
SPEC.md                              the spec, and how this repo differs from it
freecad_addon/SpecsLink/             the addon, standard library only
  install.ps1                        links it into FreeCAD's Mod folder
  InitGui.py                         the workbench, toolbar and menu
  specslink/holocad_server.py        HTTP + WebSocket, running inside FreeCAD
  specslink/exporter.py              FreeCAD shapes to GLB, and the true bbox
  specslink/service.py               server, send, live mode
  specslink/tunnel.py                runs cloudflared, and adopts a running one
  specslink/commands.py              the four toolbar commands
  specslink/settings.py              preferences, in FreeCAD's own store
Spectacles/Holo-CAD/                 the Lens Studio project
  Assets/Scripts/BridgeClient.ts     WebSocket client, reconnects on its own
  Assets/Scripts/ModelLoader.ts      downloads, measures, scales, swaps in
  Assets/Scripts/ModelPlacement.ts   grab to move, two-handed pinch to resize
  Assets/Scripts/DimensionOverlay.ts the size label, and the reset control
  Assets/Scripts/TunnelPairing.ts    asks for the four words, remembers them
  Assets/Scripts/FloatingPanel.ts    makes a panel world placed and draggable
  Assets/Scripts/ControlPanel.ts     the reset and grab mode buttons
  Assets/Scripts/StatusPanel.ts      connection and model status
docs/LENS_SETUP.md                   the lens side in detail
docs/PUBLISHING.md                   the Lens Explorer checklist
docs/RELAY.md                        the relay, superseded by the tunnel
tools/                               tests and development helpers, not shipped
bridge/, relay/                      development harness, not shipped
```

## Tests

```powershell
.\tools\run_tests.ps1
```

187 checks in seven suites. The first five run on FreeCAD's own Python with
nothing installed, which is the interpreter the addon actually runs on, so a
pass there means a pass where it matters.

- `test_initgui.py` the workbench registers. FreeCAD execs `InitGui.py` with
  separate globals and locals, and sometimes without `__file__`, and each of
  those kills a workbench silently, so this runs the file under all four
  combinations.
- `test_holocad_server.py` the embedded server: the RFC 6455 handshake against
  the spec's own test vector, host rewriting, version pruning, broadcast, a
  frame past the 64 bit length boundary, and the refusals.
- `test_exporter.py` FreeCAD shapes to GLB: the 100 mm cube acceptance
  arithmetic, the 1 inch case under the imperial schema, the Z-up to Y-up
  turn, placements inside an `App::Part`, and which objects get picked.
- `test_addon.py` the whole chain: a FreeCAD object through `service.send()`
  to a WebSocket client and a downloaded GLB, including all three scale modes,
  per-body sends, a deleted body being removed, and a lens that connects late.
- `test_tunnel.py` cloudflared: a real tunnel, a GLB fetched over https with
  the certificate verified, and a second session adopting a running tunnel.
  Needs the internet, and skips itself passing without cloudflared.
- `test_relay.py` and `test_relay_end_to_end.py` the superseded relay. These
  use the `bridge/` venv, since the relay was the one piece allowed
  dependencies.

## Why there is no server to host

A published lens may only use `wss://` and `https://`, which needs a real
certificate on a public hostname. FreeCAD running on your laptop has neither.

The first answer was a relay: one public server that every model passes
through. It works, and it is still in `relay/`, but it means somebody hosts
it, pays for it, and every model travels through it.

The tunnel replaced it. cloudflared opens an outbound connection from your
machine and Cloudflare hands back a real certificate on a public hostname, for
free and without an account. The model still comes from your own machine, and
nobody hosts anything. The cost is the four random words, which change when
the tunnel restarts.

## Development helpers

Not part of the product and not needed to use it.

- `tools/wire_lens_scene.py` rebuilds the lens scene object over the Lens
  Studio MCP server, so losing the wiring is cheap.
- `tools/lens_studio_mcp_port.py` repoints that MCP server's url, which moves
  on every Lens Studio restart.
- `tools/make_lens_icon.py` writes a 320 x 320 lens icon.
- `bridge/` is the original standalone aiohttp server, kept because
  `push_test_cube.py` and `ws_probe.py` drive the lens without FreeCAD.

If you are committing a change to the lens, **leave `TunnelPairing.words`
empty.** It exists because the keyboard never appears in the Spectacles
preview, so it is the only way to test the tunnel on the laptop, and a value
left in it wins over both the keyboard and the remembered words. Shipping one
would point every downloader at a tunnel that no longer exists. They would
recover, since the lens gives up on a dead address after three tries and asks,
but only after a confusing wait.

To run the addon's server without the workbench, for example to test the lens
with no document open:

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\python.exe" freecad_addon\SpecsLink\specslink\holocad_server.py
```

`SPEC.md` is the authority on scope and milestones.
