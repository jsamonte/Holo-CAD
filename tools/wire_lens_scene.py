"""Build the HoloCAD scene object in the open Lens Studio project.

Development tooling, not part of the Holo-CAD pipeline. Run it with Lens Studio
open on the project, then press Ctrl+S in Lens Studio, because the MCP server
has no save tool and these edits live only in the editor until you do.

    py tools\\wire_lens_scene.py                       detect the bridge ip
    py tools\\wire_lens_scene.py --bridge-ip 192.168.1.56
    py tools\\wire_lens_scene.py --delete              remove it and start over

What it creates:
  Internet Module and Remote Media Module assets, which are a hard requirement.
    Without them the script converter fails on
    require("LensStudio:InternetModule") at build time and the components never
    run. See docs/LENS_SETUP.md step 2.
  A PBR material, which imported glTF meshes need as a template.
  One HoloCAD scene object carrying BridgeClient, ModelLoader and StatusPanel,
    with their inputs filled in.

Response shapes it depends on, learned by trial:
  CreateLensStudioSceneObject    -> {"objectUUID": ...}
  CreateLensStudioComponent      -> {"newComponent": {"id": ...}}
  CreateLensStudioAsset          -> {"assetUUID": ...} or {"assetId": ...}
  GetLensStudioSceneObjectById   -> {"object": {"components": [...]}}
  GetLensStudioSceneObjectByName -> {"objects": [...]}
and the script reference property is "scriptAsset", lower case s.

Components are created by a bounded loop and their ids come straight from the
create call. Nothing re-reads the object to decide whether to create more: when
that read misses, the loop never stops, and it once left 501 empty components
on the object.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.request

CONFIG = os.path.join(os.path.expanduser("~"), ".claude.json")
OBJECT_NAME = "HoloCAD"
STATUS_OBJECT = "HoloCAD Status"
MATERIAL_NAME = "HoloCAD Model Material"
LINE_MATERIAL_NAME = "HoloCAD Wireframe Material"
SCRIPTS = ["BridgeClient", "ModelLoader", "ModelPlacement", "DimensionOverlay",
           "StatusPanel"]
MODULES = [
    ("InternetModule", "Internet Module", "internetModule"),
    ("RemoteMediaModule", "Remote Media Module", "remoteMediaModule"),
]

_id = [0]
_endpoint = {"url": None, "token": None}


def load_endpoint():
    cfg = json.load(open(CONFIG, encoding="utf-8"))["mcpServers"]["lens-studio"]
    _endpoint["url"] = cfg["url"]
    _endpoint["token"] = cfg["headers"]["Authorization"]


def rpc(method, params=None):
    _id[0] += 1
    payload = {"jsonrpc": "2.0", "id": _id[0], "method": method}
    if params is not None:
        payload["params"] = params
    req = urllib.request.Request(
        _endpoint["url"],
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": _endpoint["token"],
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode()
    for line in body.splitlines():
        if line.startswith("data:"):
            body = line[5:].strip()
            break
    return json.loads(body)


def tool(name, args):
    out = rpc("tools/call", {"name": name, "arguments": args})
    blocks = out.get("result", {}).get("content", [])
    raw = blocks[0].get("text", "") if blocks else ""
    try:
        return json.loads(raw)
    except Exception:
        return raw


def set_prop(target, path, value, value_type):
    r = tool(
        "SetLensStudioProperty",
        {"objectUUID": target, "propertyPath": path, "value": value, "valueType": value_type},
    )
    msg = r.get("message") if isinstance(r, dict) else str(r)
    print("    {0:<20} {1}".format(path, str(msg)[:90]))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def asset_id(result):
    if not isinstance(result, dict):
        return None
    for key in ("assetUUID", "assetId", "id"):
        if result.get(key):
            return result[key]
    return (result.get("asset") or {}).get("id")


def find_asset(name, kind=None):
    res = tool("GetLensStudioAssetsByName", {"name": name})
    for a in res.get("assets", []) if isinstance(res, dict) else []:
        if kind is None or a.get("type") == kind:
            return a["id"]
    return None


def object_id(name):
    res = tool("GetLensStudioSceneObjectByName", {"name": name})
    objs = res.get("objects") if isinstance(res, dict) else None
    if objs:
        return objs[0].get("id") or objs[0].get("objectUUID")
    return None


def find_camera_object():
    """The scene object carrying the Camera, so the panel can be head locked."""
    graph = tool("GetLensStudioSceneGraph", {})
    found = []

    def walk(node):
        for component in node.get("components", []):
            if component.get("type") == "Camera":
                found.append(node.get("id"))
        for child in node.get("children", []):
            walk(child)

    for root in graph.get("sceneTree", {}).get("children", []):
        walk(root)
    return found[0] if found else None


def ensure_status_text():
    """A head locked Text showing the pairing code and connection state.

    Parented to the camera so it stays in view: a code the user has to read
    and type is useless if they have to go looking for it.
    """
    print("")
    print("status panel:")
    existing = tool("GetLensStudioSceneObjectByName", {"name": STATUS_OBJECT})
    if existing.get("objects"):
        holder = existing["objects"][0]
        oid = holder.get("id") or holder.get("objectUUID")
        print("  reusing {0}".format(oid))
    else:
        camera = find_camera_object()
        if camera is None:
            print("  FAIL no camera in the scene, cannot place the panel")
            return None
        created = tool("CreateLensStudioSceneObject",
                       {"name": STATUS_OBJECT, "parentUUID": camera})
        oid = created["objectUUID"]
        print("  created {0} under the camera".format(oid))
        # In front of the viewer and a little low. The camera looks along
        # its own negative Z, so forward is a negative z here.
        for axis, value in (("x", 0.0), ("y", -8.0), ("z", -60.0)):
            set_prop(oid, "localTransform.position." + axis, value, "number")

    obj = tool("GetLensStudioSceneObjectById", {"objectUUID": oid})["object"]
    for component in obj.get("components", []):
        if component.get("type") == "Text":
            print("  text component {0}".format(component["id"]))
            return component["id"]

    made = tool("CreateLensStudioComponent",
                {"objectUUID": oid, "componentType": "Text"})
    text_id = (made.get("newComponent") or {}).get("id")
    if text_id is None:
        print("  FAIL could not add a Text: {0}".format(json.dumps(made)[:200]))
        return None
    print("  added a Text component {0}".format(text_id))
    set_prop(text_id, "text", "Holo-CAD starting", "string")
    set_prop(text_id, "size", 48, "number")
    return text_id


def main() -> int:
    ap = argparse.ArgumentParser(description="Wire the HoloCAD lens scene")
    ap.add_argument("--bridge-ip", default=None, help="default is auto detected")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--relay",
        default=None,
        help=(
            "Point the lens at a relay instead of a machine on this network, "
            "for example https://relay.example.com or relay.example.com. "
            "Required for a lens that will be published, since a published "
            "lens cannot use ws or http at all."
        ),
    )
    ap.add_argument("--delete", action="store_true", help="delete the object and exit")
    args = ap.parse_args()

    load_endpoint()
    try:
        rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "wire-lens-scene", "version": "1.0"}})
    except Exception as e:
        print("cannot reach the Lens Studio MCP server at {0}: {1}".format(_endpoint["url"], e))
        print("Is Lens Studio open? Its port moves on restart, so run")
        print("  py tools\\lens_studio_mcp_port.py --write")
        return 1

    existing = object_id(OBJECT_NAME)

    if args.delete:
        if existing is None:
            print("no {0} object to delete".format(OBJECT_NAME))
            return 0
        tool("DeleteLensStudioSceneObject", {"objectUUID": existing})
        print("deleted {0}".format(OBJECT_NAME))
        return 0

    if existing is not None:
        print("{0} already exists ({1}). Re-run with --delete first.".format(OBJECT_NAME, existing))
        return 1

    if args.relay:
        host = args.relay.replace("https://", "").replace("http://", "").strip("/")
        bridge_url = "wss://{0}/lens".format(host)
        print("relay url: {0}".format(bridge_url))
        print("  a published lens needs Experimental APIs OFF, which this url")
        print("  allows. The lens will show a pairing code to type into FreeCAD.")
    else:
        ip = args.bridge_ip or lan_ip()
        bridge_url = "ws://{0}:{1}/ws".format(ip, args.port)
        print("bridge url: {0}".format(bridge_url))
        print("  plain ws, so Experimental APIs must be ON and the lens")
        print("  cannot be published. Pass --relay for a publishable build.")

    # 1. The module assets. Reuse them when a previous run made them.
    print("")
    print("module assets:")
    modules = {}
    for kind, asset_name, input_name in MODULES:
        found = find_asset(asset_name, kind)
        if found:
            print("  {0:<20} reusing {1}".format(asset_name, found))
        else:
            res = tool("CreateLensStudioAsset", {"assetType": kind, "name": asset_name})
            found = asset_id(res)
            if found is None:
                print("  FAIL creating {0}: {1}".format(kind, json.dumps(res)[:200]))
                return 1
            print("  {0:<20} created {1}".format(asset_name, found))
        modules[input_name] = found

    # 2. A PBR material for imported meshes, and an unlit one for the
    #    dimension wireframe, which should not be shaded like a surface.
    mat = find_asset(MATERIAL_NAME)
    if mat is None:
        mat = asset_id(tool("CreateAssetFromPresetTool",
                            {"preset": "PBRMaterialPreset", "name": MATERIAL_NAME}))
    print("  {0:<20} {1}".format(MATERIAL_NAME, mat))
    if mat is None:
        print("  FAIL no material, ModelLoader cannot instantiate anything without one")
        return 1

    line_mat = find_asset(LINE_MATERIAL_NAME)
    if line_mat is None:
        line_mat = asset_id(tool("CreateAssetFromPresetTool",
                                 {"preset": "UnlitMaterialPreset",
                                  "name": LINE_MATERIAL_NAME}))
    print("  {0:<20} {1}".format(LINE_MATERIAL_NAME, line_mat))

    # 3. Script assets.
    scripts = {}
    for name in SCRIPTS:
        found = find_asset(name, "TypeScriptAsset")
        if found is None:
            print("FAIL no TypeScriptAsset named {0}. Is Assets/Scripts present?".format(name))
            return 1
        scripts[name] = found

    # 4. The object and its components.
    oid = tool("CreateLensStudioSceneObject", {"name": OBJECT_NAME})["objectUUID"]
    print("")
    print("object {0} = {1}".format(OBJECT_NAME, oid))

    comps = {}
    for name in SCRIPTS:
        res = tool("CreateLensStudioComponent",
                   {"objectUUID": oid, "componentType": "ScriptComponent"})
        cid = (res.get("newComponent") or {}).get("id")
        if cid is None:
            print("FAIL creating component for {0}: {1}".format(name, json.dumps(res)[:200]))
            return 1
        comps[name] = cid
        print("  {0:<13} {1}".format(name, cid))
        set_prop(cid, "scriptAsset", scripts[name], "reference")
        set_prop(cid, "name", name, "string")

    # 5. Inputs. These have to come after scriptAsset, because a component does
    #    not expose its inputs until the editor has processed the assignment.
    print("")
    print("inputs:")
    print("  BridgeClient")
    set_prop(comps["BridgeClient"], "bridgeUrl", bridge_url, "string")
    set_prop(comps["BridgeClient"], "verbose", True, "boolean")
    set_prop(comps["BridgeClient"], "internetModule", modules["internetModule"], "reference")
    print("  ModelLoader")
    set_prop(comps["ModelLoader"], "material", mat, "reference")
    set_prop(comps["ModelLoader"], "internetModule", modules["internetModule"], "reference")
    set_prop(comps["ModelLoader"], "remoteMediaModule", modules["remoteMediaModule"], "reference")
    if line_mat:
        print("  DimensionOverlay")
        set_prop(comps["DimensionOverlay"], "lineMaterial", line_mat, "reference")

    # A visible status panel. Without one the pairing code has nowhere to
    # appear, and a relay pairing cannot be completed at all.
    text_id = ensure_status_text()
    if text_id:
        set_prop(comps["StatusPanel"], "statusText", text_id, "reference")

    obj = tool("GetLensStudioSceneObjectById", {"objectUUID": oid})["object"]
    print("")
    print("components on object: {0}".format(len(obj.get("components", []))))
    print("")
    print("Now press Ctrl+S in Lens Studio. Nothing above is on disk until you do.")
    print("Then confirm with:")
    print("  grep -c ScriptComponent \"Spectacles/Holo-CAD/Assets/Scene.scene\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
