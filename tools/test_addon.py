"""The addon end to end, without the GUI.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" tools\\test_addon.py

Builds a part in FreeCAD, calls the same service.send() the toolbar button
calls, and checks that a WebSocket client receives the announcement and can
fetch the GLB. That is every link in the chain except the Qt layer and the
glasses.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "freecad_addon", "SpecsLink"))

from specslink import exporter, service, settings  # noqa: E402
from ws_client import WebSocketClient  # noqa: E402

PORT = 8793
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


def main() -> int:
    settings.set_value("Port", PORT)
    settings.set_value("ScaleMode", "true_size")
    settings.set_value("Quality", "normal")
    settings.set_value("PreferStockExporter", False)
    # The first half of this file predates per body sends and asserts one
    # model per send, so it runs with the whole document as one model.
    settings.set_value("PerBody", False)

    doc = FreeCAD.newDocument("holocad_addon_test")
    box = doc.addObject("Part::Box", "Bracket")
    box.Length, box.Width, box.Height = 120.0, 40.0, 15.0
    doc.recompute()

    print("server")
    url = service.start_server()
    check("start_server returns a ws url", url.startswith("ws://") and url.endswith("/ws"), url)
    check("reports itself running", service.server_running())
    time.sleep(0.3)

    print("a lens connects before anything is sent")
    client = WebSocketClient("127.0.0.1", PORT)
    try:
        check("handshake accepted", client.upgraded, client.response.split("\r\n")[0])
        hello = client.read_json()
        check("greeted with hello", hello.get("type") == "hello", str(hello))

        print("send, the way the toolbar button does")
        metadata = service.send(doc=doc)[0]
        # SPEC: the id is the slugified document or object name. One object
        # means its own label, so a part keeps its identity in the lens
        # across sends.
        check("a single object sends under its own label",
              metadata["id"] == "Bracket", metadata["id"])
        check("true size reported in mm",
              [round(metadata["bbox_mm"][k], 3) for k in ("x", "y", "z")]
              == [120.0, 40.0, 15.0], str(metadata["bbox_mm"]))
        check("scale is 1:1 by default", metadata["scale"]["mode"] == "true_size")
        check("triangle count present", metadata["triangles"] == 12,
              str(metadata["triangles"]))

        update = client.read_json()
        check("the lens was told", update.get("type") == "model_update")
        check("same version as the publish", update["version"] == metadata["version"])

        with urllib.request.urlopen(
                update["url"].replace(service.bridge().host_ip, "127.0.0.1"),
                timeout=5) as r:
            blob = r.read()
        check("GLB fetched over http", blob[:4] == b"glTF", str(blob[:8]))
        check("byte count matches the announcement", len(blob) == update["bytes"],
              "{0} vs {1}".format(len(blob), update["bytes"]))

        print("more than one object falls back to the document name")
        second = doc.addObject("Part::Box", "Plate")
        second.Length, second.Width, second.Height = 60.0, 60.0, 5.0
        second.Placement = FreeCAD.Placement(
            FreeCAD.Vector(0, 0, 200), FreeCAD.Rotation())
        doc.recompute()
        multi = service.send(doc=doc)[0]
        client.read_json()
        check("id falls back to the document",
              multi["id"] == "holocad_addon_test", multi["id"])
        check("bbox spans both parts",
              round(multi["bbox_mm"]["z"], 3) == 205.0, str(multi["bbox_mm"]))
        check("triangles counted across both", multi["triangles"] == 24,
              str(multi["triangles"]))

        print("per body: one model per object, and only what changed")
        settings.set_value("PerBody", True)
        service.forget_sent()
        sent = service.send(doc=doc)
        ids = sorted(m["id"] for m in sent)
        check("one model per object", ids == ["Bracket", "Plate"], str(ids))
        seen = {}
        for _ in range(2):
            message = client.read_json()
            seen[message["id"]] = message
        check("both announced", sorted(seen) == ["Bracket", "Plate"], str(sorted(seen)))

        again2 = service.send(doc=doc)
        check("nothing changed means nothing sent", again2 == [], str(again2))

        second.Length = 75.0
        doc.recompute()
        changed = service.send(doc=doc)
        check("only the edited body is re-sent",
              [m["id"] for m in changed] == ["Plate"], str([m["id"] for m in changed]))
        message = client.read_json()
        check("and that is what the lens hears", message["id"] == "Plate",
              message["id"])
        check("its new size came through",
              round(message["bbox_mm"]["x"], 3) == 75.0, str(message["bbox_mm"]))

        print("a deleted body is taken out of the lens")
        doc.removeObject(second.Name)
        doc.recompute()
        service.send(doc=doc)
        message = client.read_json()
        check("removal is announced", message.get("type") == "model_remove",
              str(message)[:100])
        check("naming the body that went", message.get("id") == "Plate",
              str(message.get("id")))

        print("scale modes reach the lens")
        print("a scale change alone is still a change")
        settings.set_value("PerBody", False)
        service.forget_sent()
        service.send(doc=doc)
        client.read_json()
        settings.set_value("ScaleMode", "ratio")
        settings.set_value("ScaleFactor", 0.1)
        rescaled = service.send(doc=doc)
        check("same geometry at a new scale is re-sent", len(rescaled) == 1,
              str(rescaled))
        client.read_json()
        settings.set_value("ScaleMode", "true_size")
        settings.set_value("ScaleMode", "ratio")
        service.send(doc=doc, force=True)
        update = client.read_json()
        check("ratio carried through",
              update["scale"]["mode"] == "ratio" and update["scale"]["factor"] == 0.1,
              str(update["scale"]))
        check("1:10 is described for the Report view",
              settings.describe_scale() == "1:10", settings.describe_scale())

        settings.set_value("ScaleMode", "fit")
        settings.set_value("FitTargetMm", 300.0)
        service.send(doc=doc, force=True)
        update = client.read_json()
        check("fit target carried through",
              update["scale"]["mode"] == "fit" and update["scale"]["target_mm"] == 300.0,
              str(update["scale"]))

        print("versions climb and the old ones stay fetchable for a while")
        first = update["version"]
        service.send(doc=doc, force=True)
        again = client.read_json()
        check("version incremented", again["version"] == first + 1,
              "{0} then {1}".format(first, again["version"]))
    finally:
        client.close()

    print("a lens that connects late is caught up")
    time.sleep(0.2)
    late = WebSocketClient("127.0.0.1", PORT)
    try:
        hello = late.read_json()
        check("late lens greeted", hello.get("type") == "hello")
        # One catch up message per model the server still holds. Comparing
        # against the server's own view rather than a version remembered
        # earlier, because toggling PerBody removes models and version
        # numbering starts again when an id comes back.
        expected = {m["id"]: m["version"] for m in service.bridge().store.latest()}
        caught = {}
        for _ in range(len(expected)):
            message = late.read_json()
            if message.get("type") == "model_update":
                caught[message["id"]] = message["version"]
        check("late lens is caught up on every model",
              sorted(caught) == sorted(expected),
              "{0} vs {1}".format(sorted(caught), sorted(expected)))
        check("and on the newest version of each", caught == expected,
              "{0} vs {1}".format(caught, expected))
    finally:
        late.close()

    print("failure is reported, not raised at the user")
    empty = FreeCAD.newDocument("holocad_addon_empty")
    try:
        service.send(doc=empty)
        check("an empty document refuses to send", False, "it did not raise")
    except exporter.ExportError:
        check("an empty document refuses to send", True)

    print("shutdown leaves nothing behind")
    service.shutdown()
    check("server stopped", not service.server_running())
    try:
        urllib.request.urlopen("http://127.0.0.1:{0}/status".format(PORT), timeout=2)
        check("port released", False, "something still answers")
    except Exception:
        check("port released", True)

    FreeCAD.closeDocument(doc.Name)
    FreeCAD.closeDocument(empty.Name)
    settings.set_value("ScaleMode", "true_size")
    settings.set_value("PerBody", True)
    settings.set_value("Port", 8765)

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


# freecadcmd does not set __name__ to "__main__", so the usual guard would
# make this file do nothing and still exit 0, which looks like a pass.
# freecadcmd neither sets __name__ to "__main__" nor prints a traceback for
# an escaping exception, so an unguarded failure looks like the suite simply
# stopping. Catching it here is the difference between a diagnosis and a
# mystery.
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
