# Lens setup, by hand in Lens Studio

Everything here happens in the Lens Studio editor. Lens Studio 5.15.4, project
at `C:\GitHub\Holo-CAD\Spectacles\Holo-CAD`.

Steps marked **(by hand)** cannot be driven from outside the editor. Steps
marked **(automated)** can be done through the Lens Studio MCP server, so ask
rather than clicking.

## 0. The project must target Spectacles (by hand)

The project as it stands was created from the **Default** template, not the
Spectacles one. Its `.esproj` says:

```
lensClientCompatibilities:
  - Mobile
  - Web
fromTemplateName: Default
```

A Spectacles project says:

```
lensClientCompatibilities:
  - Spectacles
fromTemplateName: Spectacles
```

and ships `SpectaclesInteractionKit.lspkg` and `SpectaclesUIKit.lspkg` in
`Packages/`, which phase 3 needs for grab and move.

So before pushing anything to the glasses: in the Lens Studio home screen,
create a new project from the **Spectacles** template and save it over
`C:\GitHub\Holo-CAD\Spectacles\Holo-CAD`. Nothing is lost, the current project
holds only the stock template scene. Then copy `Assets/Scripts` across if the
new project does not already contain it.

## 1. The scripts

Three files, already in `Assets/Scripts`, already compiling:

| File | Does |
| ---- | ---- |
| `BridgeClient.ts` | WebSocket to the bridge, parses `model_update`, reconnects with backoff |
| `ModelLoader.ts` | downloads each GLB, measures it, scales it to true size, swaps it in |
| `StatusPanel.ts` | optional, writes connection and model state into a Text |

Lens Studio imports `.ts` files from `Assets/` automatically and generates the
`.ts.meta` beside them. If the components do not appear in the Add Component
list, click into the Lens Studio window to let it pick up the folder.

## 2. One scene object holds all three (automated)

The components find each other with `getComponent(BridgeClient.getTypeName())`
on their own scene object, so they must sit together.

1. Create a scene object at the scene root, named `HoloCAD`.
2. Add component > Script > `BridgeClient`.
3. Add component > Script > `ModelLoader`.
4. Add component > Script > `StatusPanel` (optional).

Models are spawned as children of whatever object holds `ModelLoader`, unless
`modelsParent` is set.

## 3. Inputs to fill in

### BridgeClient

| Input | Value |
| ----- | ----- |
| `bridgeUrl` | the `ws://...` line the bridge prints, for example `ws://172.16.9.63:8765/ws`. **Never `127.0.0.1`**, the glasses are a separate device |
| `internetModule` | leave empty, the module is obtained in code |
| `verbose` | on while testing, it logs every message |
| `pingSeconds` | 15 is fine |

### ModelLoader

| Input | Value |
| ----- | ----- |
| `material` | **required.** A PBR material asset. Create one in the Asset Browser (+ > Material > PBR) and drop it here. Imported glTF meshes need a material as a template |
| `internetModule`, `remoteMediaModule` | leave empty |
| `bridgeClientObject` | leave empty when BridgeClient is on the same object |
| `modelsParent` | leave empty to parent models under this object |
| `cameraObject` | leave empty to find the main camera |
| `spawnDistanceCm` | 50 |
| `spawnDropCm` | 15 |
| `axisTolerance` | 0.01, which is the 1 percent the spec asks for |

### StatusPanel

| Input | Value |
| ----- | ----- |
| `statusText` | optional Text component. Without it the status only goes to the Logger |
| `sourceObject` | leave empty when all three are on one object |

## 4. Project Settings (by hand)

- **Experimental APIs: on.** Plain `ws://` and `http://` both need it. Without
  it the socket fails to open and `makeResourceFromUrl` refuses the url.
- **Internet access**, if your Project Settings shows an extended permission or
  capability for it, on.

Consequence worth knowing: a lens with Experimental APIs on cannot be
published. That is accepted for now. `SPEC.md` has the tunnel plan for later.

## 5. Push to the glasses (by hand)

Send to the Spectacles the usual way, with the glasses awake and paired.

## 6. Test

1. Start the bridge on the laptop:
   `cd C:\GitHub\Holo-CAD\bridge; .\.venv\Scripts\python.exe server.py`
2. Read the `ws://` line off its banner, check it matches `bridgeUrl`.
3. Open the lens on the glasses. The bridge should print
   `lens connected from 172.16.x.x (1 total)`.
4. Push the cube: `.\.venv\Scripts\python.exe push_test_cube.py`
5. A blue 100 mm cube should appear about 50 cm in front of you, slightly below
   eye level, sitting on nothing in particular.
6. Hold a ruler up to it. Each edge should measure 100 mm.

The Logger line that proves the scale maths, for the test cube:

```
HoloCAD ModelLoader: test_cube v1 shown at 1:1  size 100.0 x 100.0 x 100.0 mm  (true 100.0 x 100.0 x 100.0 mm)  correction 100.0000  meshes 1  load 412 ms
```

`correction 100` is expected and is the point of the exercise. The file holds a
cube 0.1 units per side because glTF says metres, Lens Studio works in
centimetres, so the measured 0.1 has to become 10. If the exporter ever starts
writing millimetres instead, the correction becomes 0.1 and the cube still
measures 100 mm.

## When nothing appears

Work down this list, it is ordered by how often each one is the cause.

| Symptom in the Logger | Cause |
| --------------------- | ----- |
| `connecting` then `closed (code ...)` repeatedly | the laptop is not reachable. Windows Firewall or Wi-Fi client isolation, see README |
| `createWebSocket rejected` | Experimental APIs is off, or `bridgeUrl` is malformed |
| nothing at all from BridgeClient | the component is not on an enabled scene object, or the lens on the glasses is an older build |
| `download failed for http://...` | the WebSocket got through but the HTTP fetch did not. Same firewall question, on the same port |
| `the material input is empty` | step 3, ModelLoader needs a PBR material |
| `no BridgeClient found` | the two components are on different scene objects |
| `contains no mesh visuals` | the GLB loaded but held no geometry, so look at the exporter |
| `WARNING ... proportions disagree with bbox_mm` | the file and FreeCAD's bounding box describe different shapes. The largest axis is still correct, but the export dropped or moved something |

The bridge's own terminal is the other half of the picture: it logs every lens
connect, every push, and every GLB it serves.
