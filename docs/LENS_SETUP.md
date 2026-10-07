# Lens setup

The lens project is `Spectacles/Holo-CAD`, built from the **Spectacles**
template in Lens Studio 5.15.4. Most of this is already done and committed;
the list is here so it can be rebuilt or checked.

Steps marked **(by hand)** can only be done in the editor. The rest
`tools/wire_lens_scene.py` does over the Lens Studio MCP server.

## The short version

```powershell
py tools\wire_lens_scene.py --delete      # if a HoloCAD object already exists
py tools\wire_lens_scene.py
```

Then in Lens Studio: **Ctrl+S**, check **Experimental APIs** is on, and send to
the glasses. The scene edits live only in the editor until you save, because
the MCP server has no save tool.

## What the scene needs

One object named `HoloCAD` at the scene root, carrying five script
components. They find each other with `getComponent(X.getTypeName())` on their
own object, which is how SIK does it, so they have to sit together.

| Component | Does | Inputs that matter |
| --------- | ---- | ------------------ |
| `BridgeClient` | WebSocket to FreeCAD, reconnects on its own | `bridgeUrl`, `internetModule` |
| `ModelLoader` | downloads each GLB, measures it, scales it to true size | `material`, `internetModule`, `remoteMediaModule` |
| `ModelPlacement` | grab, move, turn, and two handed resize | `allowResize`, `minScale`, `maxScale` |
| `DimensionOverlay` | wireframe box with the size in mm | `lineMaterial` |
| `StatusPanel` | connection and model state, optional | `statusText` |

Plus three assets in `Assets/`: an **Internet Module**, a **Remote Media
Module**, and two materials (PBR for imported meshes, Unlit for the
wireframe).

## The module assets are not optional

`require("LensStudio:InternetModule")` does **not** resolve on 5.15. Measured:
the build logs

```
Failed to resolve dependency InternetModule in asset Assets/Scripts/BridgeClient.ts
```

whether or not the assets exist. The error is survivable, the scripts still
run, but the require yields nothing, so a component with those inputs left
empty reports `no InternetModule, nothing can be downloaded` and stops. **Wire
the inputs.** The assets have to exist for the inputs to point at.

Two things make this hard to spot. Those errors go to the Lens Studio log file
under `%LOCALAPPDATA%\Snap\Lens Studio\logs`, never the Logger panel. And the
MCP cannot create module assets, since its asset types are only RenderTarget,
ObjectPrefab, Material, FileTexture, FileAudioTrack and AnimatedTexture.
Writing the two files straight into `Assets/` does work, and Lens Studio
imports them and assigns its own ids, which is what the wiring tool relies on.

## Handling the model

**Move and turn it** by grabbing: pinch and drag on the glasses, click and
drag with the mouse in the Lens Studio preview, which the Spectacles template's
MouseInteractor provides.

**Resize it** by pinching with both hands and pulling apart or together.
Limited to between 0.05x and 20x true size.

**The label under the model always says how big it is.** At true size it
reads the real measurement and the scale, for example
`100.0 x 100.0 x 100.0 mm` and `1:1, true size`. Once it has been stretched
it reads the size as drawn and `2.00x true size, tap to reset`.

**Tap that label to go back to true size.** The readout is the reset control,
so the thing that tells you the model is no longer life sized is the thing
that fixes it.

Resizing by hand is kept separate from the scale FreeCAD asks for.
ModelLoader puts the true size correction on an inner wrapper and the hands
only ever scale the outer root, so resetting to 1 is exact rather than
approximate, and a model edited in FreeCAD keeps whatever size you stretched
it to.

## True size, and what guarantees it

A part measures the same through the glasses as it does in FreeCAD. One inch
in FreeCAD is one inch in front of you, and `tools/test_exporter.py` checks
exactly that: a 1 inch cube, with FreeCAD set to an imperial schema, comes out
as 25.4 mm and is drawn 25.4 mm across.

It holds because nothing trusts the units in the file. FreeCAD reports the
true bounding box in millimetres, the lens measures the mesh it actually
received, and the ratio between them is the correction. An exporter writing
metres, millimetres or anything else still lands at the right size.

## bridgeUrl

The address FreeCAD prints in its Report view when the server starts, for
example `ws://192.168.1.56:8765/ws`. **Never `127.0.0.1`**: the glasses are a
separate device. It changes when the laptop changes network, so read it off
the Report view rather than trusting an old value.

## Project Settings (by hand)

**Experimental APIs: on.** Plain `ws://` and `http://` both need it, and the
engine says so plainly when it is off:

```
InternalError: URL is not secure (Experimental API must be enabled to access insecure URLs)
```

This also means the lens cannot be published to Lens Explorer, which is a
deliberate trade. The README explains why, and `lensDescriptors` in the
`.esproj` is the giveaway: `- EXPERIMENTAL_API` present means testing mode,
empty `[]` means publishable.

## Test

1. Start the server. In FreeCAD, switch to the **Holo-CAD** workbench, which
   starts it, or run it directly:
   `& "C:\Program Files\FreeCAD 1.1\bin\python.exe" freecad_addon\SpecsLink\specslink\holocad_server.py`
2. Check the url it prints matches `bridgeUrl`.
3. Open the lens. The server should log
   `lens connected from 192.168.1.x (1 total)`.
4. Send something: the toolbar button in FreeCAD, or without FreeCAD,
   `cd bridge; .\.venv\Scripts\python.exe push_test_cube.py`.
5. A cube appears about 50 cm in front of you, slightly below eye level.
6. Hold a ruler to it. Each edge measures 100 mm.

The line that proves the arithmetic:

```
HoloCAD ModelLoader: test_cube v12 shown at 1:1  size 100.0 x 100.0 x 100.0 mm  (true 100.0 x 100.0 x 100.0 mm)  correction 100.0000  meshes 1  load 303 ms
```

`correction 100` is expected. The file holds a cube 0.1 units per side because
glTF says metres and Lens Studio works in centimetres, so the measured 0.1 has
to become 10. If the exporter ever starts writing millimetres instead, the
correction becomes 0.1 and the cube still measures 100 mm. That is the point:
nothing trusts the units in the file.

## When nothing appears

Ordered by how often each one is the cause.

| Symptom | Cause |
| ------- | ----- |
| `URL is not secure (Experimental API must be enabled...)` | Experimental APIs is off |
| `connecting` then `closed (code ...)` on repeat | the laptop is not reachable: Windows Firewall or Wi-Fi client isolation, see the README |
| `no InternetModule, nothing can be downloaded` | the `internetModule` input is empty, see above |
| `the material input is empty` | ModelLoader needs a PBR material |
| `no BridgeClient found` | the components are on different scene objects |
| nothing at all in the Logger | check the Lens Studio **log file**, not just the Logger. Script conversion errors only appear there |
| `download failed for http://...` | the socket got through but the HTTP fetch did not. Same firewall question, same port |
| `contains no mesh visuals` | the GLB loaded but held no geometry, so look at the exporter |
| `WARNING ... proportions disagree with bbox_mm` | the file and FreeCAD's bounding box describe different shapes. The largest axis is still right, but something in the export was dropped or moved |

## Rebuilding the lens project from scratch

If the project ever has to be recreated, it must come from the **Spectacles**
template. A Default template project says `lensClientCompatibilities: Mobile,
Web`, carries no packages, and cannot be pushed to the glasses at all.
Recreating also wipes `Assets/Scripts`, so restore the five `.ts` files from
git afterwards, then run the wiring tool.
