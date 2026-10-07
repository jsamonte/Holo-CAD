# FreeCAD to Spectacles Live Viewer

## Goal

Build a pipeline that pushes a model from FreeCAD into a Snapchat Spectacles Lens at a chosen scale (true 1:1 size by default), with an optional live mode that re-sends the model every time the FreeCAD document recomputes.

## Environment

- Windows laptop, VS Code, Claude Code.
- FreeCAD, latest stable 1.x. The addon runs inside FreeCAD's bundled Python.
- Lens Studio **5.15.x**, targeting Spectacles (2024). Do not upgrade Lens Studio. Newer versions target the new Specs hardware and are not supported by Spectacles (2024) firmware.
- Spectacles and the laptop are on the same Wi-Fi network.
- The Lens Studio MCP server may be connected to Claude Code as `lens-studio`. If it is, use it for scene setup (creating objects, adding components, wiring inputs). If it isn't, give me a precise step-by-step checklist for anything that has to be done by hand in the Lens Studio editor.

## Repo layout

```
cad-to-specs/
  SPEC.md
  README.md              (keep updated with setup and run steps)
  freecad_addon/SpecsLink/
  bridge/
  lens/                  (Lens Studio project I create from the Spectacles template)
```

## Architecture

```
FreeCAD addon  --HTTP POST /push-->  Bridge server (laptop)  <--WebSocket /ws--  Spectacles Lens
                                           |                                          |
                                           +------ HTTP GET /models/{id}/{v}.glb <----+
```

### 1. FreeCAD addon (`freecad_addon/SpecsLink/`)

- Python, **standard library only**. Do not pip install anything into FreeCAD's Python.
- Commands on a toolbar:
  - **Send to Spectacles**: sends the selected objects, or all visible solids if nothing is selected.
  - **Live mode** (toggle).
  - **Settings**: bridge URL, scale mode, scale factor, fit target size, mesh quality.
- Export: start with the stock glTF exporter (`ImportGui.export(objs, "file.glb")`). The stock exporter has had known issues (some placements inside Links/App::Part not respected, mirrored objects skipped). If those show up in testing, write a fallback exporter that tessellates the globally placed shapes with `MeshPart.meshFromShape(Shape=..., LinearDeflection=..., AngularDeflection=...)` and writes a minimal GLB by hand with `struct` (positions, normals, indices, one material per object color). Keep the fallback dependency free.
- Compute the true bounding box in mm from the shapes' global `BoundBox` before export and include it in the metadata.
- All network calls run on a background thread so the FreeCAD UI never freezes. Report success and errors in the Report view.
- Live mode: register `FreeCAD.addDocumentObserver` and hook `slotRecomputedDocument` for the active document. Debounce with a single-shot `QTimer` (about 500 ms, restarted on each recompute) so the export runs once on the GUI thread after a burst of recomputes. Remove the observer when live mode is turned off or FreeCAD closes.
- Install: develop in the repo and link it into FreeCAD's `Mod` folder with a directory junction (`mklink /J`). Find the user data folder from the FreeCAD Python console with `FreeCAD.getUserAppDataDir()`. Write an `install.ps1` that creates the junction.
- Simple SVG icons for the commands.

### 2. Bridge server (`bridge/`)

- Python 3.11+, `aiohttp`, with a `requirements.txt` and a venv.
- One port (default 8765), bound to `0.0.0.0`:
  - `POST /push`: GLB bytes plus JSON metadata from FreeCAD. Stores the file as `bridge/models/{id}/{version}.glb` and keeps the last few versions per id.
  - `GET /models/{id}/{version}.glb`: serves the file.
  - `GET /ws`: WebSocket. On each push, broadcast the metadata to every connected Lens. When a Lens connects, send it the latest metadata for every model so a freshly opened Lens catches up.
  - `GET /status`: health check.
- On startup, print the laptop's LAN IP and the exact `ws://` URL to paste into the Lens.
- Include a `test_cube.glb` (exactly 100 x 100 x 100 mm) and a script that pushes it, for testing without FreeCAD.
- README must include the PowerShell command to allow inbound TCP on the port for Private networks in Windows Firewall, and a note that some Wi-Fi networks block device-to-device traffic (client isolation).

### 3. Lens (`lens/`)

TypeScript components (`@component`). Before writing anything that touches Lens Studio or SIK APIs, read the actual type definitions in the project (Lens Studio generates `.d.ts` files) and the SIK package source so the API names match Lens Studio 5.15. Do not guess API names.

- **BridgeClient**: `@input internetModule`, `@input bridgeUrl` (string, e.g. `ws://192.168.1.50:8765/ws`). Uses `internetModule.createWebSocket`. Parses messages, emits events, and auto-reconnects with backoff (sockets drop when Spectacles sleeps).
- **ModelLoader**: `@input remoteMediaModule`, `@input internetModule`, `@input material` (PBR material holder). On `model_update`: `internetModule.makeResourceFromUrl(url)` then `remoteMediaModule.loadResourceAsGltfAsset(...)`, then instantiate (use `tryInstantiateAsync` if available in 5.15, otherwise `tryInstantiateWithSetting`) into a hidden staging object, apply scale and recentering, show the new one, then destroy the old one. One root per model id. Drop stale results (if version 11 finishes loading after version 12, discard 11).
- **ModelPlacement**: on first load, spawn the model about 50 cm in front of the user, slightly below eye level. After that, keep its position and rotation across updates and only re-place it when I press a "Re-place" button. Use SIK Interactable plus InteractableManipulation so I can grab, move, and rotate it. Disable scaling through manipulation (scale comes from FreeCAD) unless a "Free scale" toggle is on.
- **DimensionOverlay**: wireframe bounding box with labels showing the true dimensions in mm and the current scale ("1:1", "1:10", etc.). Toggle with a button.
- **StatusPanel**: connection state, model id, version, triangle count, and time from push to display.
- Project settings: Experimental APIs must be on (required for `ws://` and `http://` on the LAN). Also enable any Internet Access capability setting in Project Settings.

## Message format

Bridge to Lens, one JSON message per update:

```json
{
  "type": "model_update",
  "id": "bracket",
  "version": 12,
  "url": "http://192.168.1.50:8765/models/bracket/12.glb",
  "bbox_mm": { "x": 120.0, "y": 40.0, "z": 15.0 },
  "scale": { "mode": "true_size", "factor": 1.0, "target_mm": null },
  "triangles": 18234
}
```

- `id`: slugified FreeCAD document or object name.
- `scale.mode`:
  - `true_size`: 1:1.
  - `ratio`: multiply by `factor` (0.1 means 1:10, 2.0 means 2:1).
  - `fit`: scale so the largest dimension equals `target_mm`.

## Scale correctness (most important requirement)

- Units: FreeCAD is mm, the glTF spec is meters, Lens Studio world units are cm. Do not trust whatever units end up in the exported file.
- In the Lens: instantiate under an inner wrapper with no unit conversion, compute the combined local AABB of every mesh visual in the instantiated hierarchy, and compare its largest dimension to the largest dimension of `bbox_mm` (converted to cm). That gives the correction factor. Also compare the sorted dimensions and log a warning if they disagree by more than 1% (catches axis or export problems; FreeCAD is Z-up, glTF is Y-up).
- Final scale = correction factor times the requested scale factor (or the computed fit factor).
- Recenter so the pivot is the bottom center of the bounding box, so the model sits flat on surfaces.
- **Acceptance test:** a 100 mm cube made in FreeCAD measures 100 mm against a physical ruler while wearing the glasses, and the overlay reads 100 x 100 x 100 mm at 1:1.

## Build phases

Stop after each phase, tell me exactly how to test it, and wait for me to confirm before moving on.

> Status 2026-10-07: 1 and 2 done and tested on the laptop, 3 and 4 written
> and compiling, 5 part done. Nothing has been on the glasses yet, so the
> ruler check, grabbing and the overlay are unproven. README.md has the
> table.

1. **Networking proof**: bridge serves `test_cube.glb`; the Lens connects, loads it, and shows it at 1:1. Includes Lens Studio setup and firewall steps.
2. **FreeCAD send**: Send to Spectacles with `true_size`; pass the 100 mm cube acceptance test.
3. **Scale and interaction**: ratio and fit modes, dimension overlay, placement, grab/move/rotate.
4. **Live mode**: observer, debounce, versioned URLs, seamless swap without the model jumping.
5. **Polish**: per-body updates for multi-body documents (each body its own id and GLB, only changed bodies resent), status panel, reconnect handling, clear error messages in FreeCAD.

## Later (do not build yet)

- ~~HTTPS/WSS through a tunnel (for example cloudflared) so it works off the LAN and the Lens could be published without Experimental APIs.~~ **Pulled into scope on 2026-10-07**, because publishing turned out to be a requirement rather than a nice to have. See "Publishing" below.
- Parameter sliders in the Lens that write back to a FreeCAD Spreadsheet so I can resize the part from the glasses.

## Working rules

- Never use em dashes in anything you write: code comments, docs, UI strings, commit messages.
- Keep README.md current with setup and run steps.
- Call out clearly whenever a step needs me to do something by hand in Lens Studio, FreeCAD, or on the glasses.

## This repo, as actually built

Everything above is the authority. These are the points where the real repo
differs from the layout sketch, plus the facts that were measured rather than
assumed.

- The repo is `C:\GitHub\Holo-CAD`, not `cad-to-specs`.
- **The lens lives at `Spectacles/Holo-CAD/`, not `lens/`.** The Lens Studio
  project already existed there and was open in the editor, so it was kept
  where it was. Lens scripts go in its `Assets/Scripts/`.
- The bridge port is the spec's default, **8765**. Lens Studio's own MCP server
  holds `127.0.0.1:8880` while the editor is open, so that port stays clear.
- Installed and verified: FreeCAD 1.1.4 (`C:\Program Files\FreeCAD 1.1`, bundled
  Python 3.11), Lens Studio 5.15.4, aiohttp 3.14.3 on Python 3.14.5 (x64).
- FreeCAD 1.1 keeps user data in a **versioned** folder, so the junction the
  addon install needs is `C:\Users\jared\AppData\Roaming\FreeCAD\v1-1\Mod\SpecsLink`,
  not the unversioned `...\Roaming\FreeCAD\Mod` that older guides mention.
  `FreeCAD.getUserAppDataDir()` returns `...\Roaming\FreeCAD\v1-1\` and the `Mod`
  folder does not exist until something creates it.
- Headless FreeCAD for testing without the GUI:
  `"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" script.py`. Verified
  building a 100 mm `Part::Box` and reading back `BoundBox` 100 x 100 x 100.
- The bridge accepts a push either as `multipart/form-data` (a `metadata` JSON
  part plus a `glb` file part, which is what the addon sends) or as raw GLB
  bytes with the metadata in an `X-Holocad-Meta` header, which keeps curl tests
  short.
- Two fields were added to the `model_update` message: `pushed_ms` (laptop epoch
  milliseconds) and `bytes`. Both are optional and the lens tolerates their
  absence.
- One message was added, for phase 5's per body updates:

  ```json
  { "type": "model_remove", "id": "bracket" }
  ```

  Without it, deleting or hiding a body in FreeCAD leaves it hanging in the
  air, because nothing else ever tells the lens it went away. The lens
  destroys that model's root and forgets it.
- Per body sends skip an object whose export is unchanged. "Unchanged" covers
  the GLB bytes **and** the requested scale, because switching 1:1 to 1:10
  leaves the geometry identical and would otherwise be silently dropped.
  Pressing Send always sends; the skipping exists for live mode.

### The deliverable is two downloads, and distribution is GitHub

Decided 2026-10-07, and it overrides the repo layout above. **Anyone must be
able to download the FreeCAD addon and the Lens, and have it work, with no
other download or install.** That has two consequences.

**The separate bridge server is gone.** A `bridge/` with its own venv and
`pip install aiohttp` is an install, so the server moves **inside the FreeCAD
addon** and obeys the addon's existing rule: standard library only, nothing
pip installed into FreeCAD's Python. FreeCAD itself serves the GLB over HTTP
and speaks the WebSocket. The lens is unchanged, since it only ever knew about
a url. `bridge/` survives as a development harness, not as something a user
touches.

**Nobody hosts anything. That is the shipped design**, settled 2026-10-07
after a long back and forth.

On the user's own network the lens talks to FreeCAD directly over `ws://`,
which requires Experimental APIs, which blocks publishing. Distribution is
then this repository, which Snap's
[Open Source category](https://developers.snap.com/spectacles/spectacles-community/community-challenge)
explicitly allows for lenses using Experimental APIs. The catch is that the
recipient needs Lens Studio to put the lens on their glasses, which is a third
install.

**Publishing was explored and is not being used.** A published lens may only
use `wss`, which needs a certificate for a hostname resolving to one specific
machine, and no user's laptop can have one. One published build carries one
hostname, so it cannot point at each user's own machine either. Publishing
therefore requires a single shared relay that every model passes through, run
and paid for by somebody. Rejected: the point of the project is that each
person runs their own.

A working relay is in `relay/` anyway, with `docs/RELAY.md`, because it also
covers reaching your own glasses from outside your own network, and because
the decision may be revisited. The addon can serve locally and upload to a
relay at the same time, so it costs nothing to leave in place. Note that
self-hosting a relay does **not** make a lens publishable for anyone but
whoever builds the lens against it.
