"""The addon's moving parts: one server, one send, and live mode.

Kept apart from the FreeCAD command classes so that everything here can be
exercised without the GUI, which is how the exporter and the server are
tested.

Live mode is the subtle one. A single edit can make FreeCAD recompute several
times in a row, and each recompute would otherwise mean a full tessellation
and a push. So recomputes only restart a timer, and the export happens once
the document has been quiet for a moment. The timer is a Qt single shot on
the GUI thread, because touching FreeCAD objects from anywhere else is not
safe.
"""

from __future__ import annotations

import hashlib

import FreeCAD

from . import exporter, holocad_server, relay_client, settings, tunnel
from .holocad_server import Bridge

_bridge = None
_relay = None
_tunnel = None
_observer = None
_timer = None
_pending = set()
# model id -> digest of the GLB last sent, so an unchanged body is skipped
_digests = {}


# ----------------------------------------------------------------- logging


def log(message: str) -> None:
    FreeCAD.Console.PrintMessage("Holo-CAD: {0}\n".format(message))


def warn(message: str) -> None:
    FreeCAD.Console.PrintWarning("Holo-CAD: {0}\n".format(message))


def error(message: str) -> None:
    FreeCAD.Console.PrintError("Holo-CAD: {0}\n".format(message))


# ------------------------------------------------------------------ server


def relay() -> relay_client.RelayClient:
    global _relay
    if _relay is None:
        _relay = relay_client.RelayClient(log=log, warn=warn)
    target = settings.relay_target()
    _relay.configure(target[0] if target else "", target[1] if target else "")
    return _relay


def tunnel_handle() -> tunnel.Tunnel:
    global _tunnel
    if _tunnel is None:
        import os

        # Beside FreeCAD's other user data, so a tunnel started by one
        # FreeCAD session can be found again by the next one.
        state = os.path.join(FreeCAD.getUserAppDataDir(), "holocad_tunnel.json")
        _tunnel = tunnel.Tunnel(log=log, warn=warn, on_ready=_tunnel_ready,
                                state_path=state)
    return _tunnel


def _tunnel_ready(public_base: str) -> None:
    """Point the server's model urls at the tunnel once it has a hostname."""
    bridge().public_base = public_base
    log("the glasses can now reach this machine at {0}".format(
        bridge().socket_url()))


def start_tunnel() -> bool:
    """Expose the local server over https, so a published lens can reach it."""
    server = bridge()
    if not server.running:
        start_server()
    return tunnel_handle().start(server.port)


def stop_tunnel() -> None:
    if _tunnel is not None:
        _tunnel.stop()
    bridge().public_base = ""


def tunnel_words() -> str:
    """The part of the tunnel hostname a person types into the lens."""
    return tunnel_handle().words if _tunnel is not None else ""


def bridge() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge(int(settings.get("Port")), log=log)
    return _bridge


def start_server() -> str:
    """Start serving and return the url to paste into the lens."""
    server = bridge()
    if server.running:
        return server.socket_url()
    try:
        url = server.start()
    except OSError as e:
        error(
            "could not listen on port {0}: {1}".format(server.port, e)
        )
        error(
            "Something else already has that port, very likely another "
            "FreeCAD or a server left running from earlier. Close it, or "
            "change the port in Holo-CAD Settings."
        )
        raise
    log("ready. Paste this into the lens BridgeClient bridgeUrl input:")
    log("    {0}".format(url))
    log("    health check in a browser on the same Wi-Fi: {0}/status".format(
        server.base_url()))
    return url


def stop_server() -> None:
    global _bridge
    if _bridge is not None and _bridge.running:
        _bridge.stop()
    _bridge = None


def server_running() -> bool:
    return _bridge is not None and _bridge.running


# -------------------------------------------------------------------- send


def _digest(blob: bytes, scale: dict) -> str:
    """What "unchanged" means: same geometry and same requested scale.

    Hashing only the GLB was wrong. Switching from 1:1 to 1:10 leaves the
    bytes identical, so a scale change was silently dropped and pressing
    Send appeared to do nothing.
    """
    marker = "{0}|{1}|{2}".format(
        scale.get("mode"), scale.get("factor"), scale.get("target_mm"))
    digest = hashlib.sha1(blob)
    digest.update(marker.encode("utf-8"))
    return digest.hexdigest()


def send(objs=None, doc=None, force=False) -> list:
    """Export and hand to every connected lens. Returns what was sent.

    Runs on the calling thread, which is the GUI thread, because FreeCAD
    objects cannot be touched from another one. The push itself is cheap:
    the lens is told a url and fetches the bytes from the server thread, so
    nothing large travels through here.

    makes it cheap.

    With PerBody on, which is the default, every object becomes its own
    model with its own id, and an object whose GLB is byte for byte what
    went last time is skipped. That is what makes live mode usable on a
    document with several bodies: editing one bracket re-sends one bracket,
    not the whole assembly.
    """
    server = bridge()
    if not server.running:
        start_server()

    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise exporter.ExportError("no document is open")

    chosen = objs or exporter.collect_objects(doc)
    scale = settings.scale_spec()
    quality = settings.quality()
    prefer_stock = bool(settings.get("PreferStockExporter"))
    per_body = bool(settings.get("PerBody"))
    groups = [[obj] for obj in chosen] if per_body else [chosen]

    # One origin for the whole send, so every part reports where it sits
    # relative to the same point and the lens can rebuild the assembly.
    # Without this each part was centred on itself and they all arrived on
    # top of one another.
    origin_mm = exporter.bottom_centre_mm(exporter.bounding_box_doc(chosen))

    sent = []
    unchanged = 0
    for group in groups:
        result = exporter.export(group, quality=quality, prefer_stock=prefer_stock,
                                 origin_mm=origin_mm)
        digest = _digest(result["glb"], scale)
        slug = holocad_server.slugify(result["id"])
        if not force and _digests.get(slug) == digest:
            unchanged += 1
            continue
        metadata = server.publish(
            result["id"],
            result["glb"],
            result["bbox_mm"],
            scale=scale,
            triangles=result["triangles"],
            colours=result.get("colours"),
            offset_mm=result.get("offset_mm"),
            cm_per_unit=result.get("cm_per_unit"),
        )
        _digests[metadata["id"]] = digest
        sent.append(metadata)
        # The relay gets the same model, on a worker thread. It builds its
        # own url, so only the facts about the model travel.
        uploader = relay()
        if uploader.configured:
            uploader.push(metadata["id"], result["glb"], {
                "id": metadata["id"],
                "bbox_mm": metadata["bbox_mm"],
                "scale": metadata["scale"],
                "triangles": metadata["triangles"],
                "colours": metadata["colours"],
                "offset_mm": metadata["offset_mm"],
                "cm_per_unit": metadata["cm_per_unit"],
            })
        bbox = result["bbox_mm"]
        log(
            "sent {0} at {1}, {2:.1f} x {3:.1f} x {4:.1f} mm, {5} triangles, "
            "{6} exporter".format(
                metadata["id"], settings.describe_scale(),
                bbox[0], bbox[1], bbox[2], result["triangles"], result["exporter"],
            )
        )

    if per_body:
        _remove_vanished(server, chosen)

    if unchanged:
        log("{0} unchanged, not re-sent".format(unchanged))
    if sent and server.lens_count == 0:
        warn(
            "no lens is connected yet, so nothing saw that. The lens will "
            "catch up as soon as it connects."
        )
    return sent


def _remove_vanished(server, chosen) -> None:
    """Tell the lens about anything no longer being exported.

    A body deleted or hidden in FreeCAD would otherwise hang in the air,
    because nothing else tells the lens it went away.
    """
    live = set()
    for obj in chosen:
        live.add(holocad_server.slugify(obj.Label or obj.Name))
    for model_id in list(_digests):
        if model_id in live:
            continue
        if server.remove(model_id):
            log("removed {0}, no longer in the document".format(model_id))
        uploader = relay()
        if uploader.configured:
            uploader.remove(model_id)
        _digests.pop(model_id, None)


def forget_sent() -> None:
    """Drop the change tracking, so the next send pushes everything again."""
    _digests.clear()


def send_selected() -> list:
    """What the toolbar button calls: the selection, or everything visible."""
    selection = []
    try:
        import FreeCADGui

        selection = [
            o for o in FreeCADGui.Selection.getSelection()
            if exporter.has_shape(o)
        ]
    except Exception:
        pass
    doc = FreeCAD.ActiveDocument
    objs = exporter.collect_objects(doc, selection=selection)
    # Pressing the button always sends. Skipping unchanged bodies is for
    # live mode, where it is the whole point.
    return send(objs, doc, force=True)


# --------------------------------------------------------------- live mode


class _RecomputeObserver(object):
    """Restarts the debounce timer whenever a document recomputes."""

    def slotRecomputedDocument(self, doc):
        _queue(doc)

    # A document closing while queued would leave a dangling reference.
    def slotDeletedDocument(self, doc):
        _pending.discard(getattr(doc, "Name", None))


def _queue(doc) -> None:
    name = getattr(doc, "Name", None)
    if name is None:
        return
    _pending.add(name)
    if _timer is not None:
        # Restarting rather than starting is what collapses a burst of
        # recomputes into a single export.
        _timer.start(int(settings.get("LiveDebounceMs")))


def _flush() -> None:
    names = list(_pending)
    _pending.clear()
    for name in names:
        doc = FreeCAD.getDocument(name) if name in [
            d.Name for d in FreeCAD.listDocuments().values()
        ] else None
        if doc is None:
            continue
        try:
            send(doc=doc)
        except exporter.ExportError as e:
            warn("live mode skipped {0}: {1}".format(name, e))
        except Exception as e:
            error("live mode failed on {0}: {1}".format(name, e))


def live_enabled() -> bool:
    return _observer is not None


def set_live(enabled: bool) -> bool:
    """Turn live mode on or off. Returns the state actually reached."""
    global _observer, _timer

    if enabled and _observer is None:
        try:
            from PySide import QtCore
        except ImportError:
            error("live mode needs the FreeCAD GUI")
            return False
        if not server_running():
            start_server()
        _timer = QtCore.QTimer()
        _timer.setSingleShot(True)
        _timer.timeout.connect(_flush)
        _observer = _RecomputeObserver()
        FreeCAD.addDocumentObserver(_observer)
        log("live mode on. Every recompute re-sends, {0} ms after the last "
            "one.".format(int(settings.get("LiveDebounceMs"))))
        return True

    if not enabled and _observer is not None:
        FreeCAD.removeDocumentObserver(_observer)
        _observer = None
        if _timer is not None:
            _timer.stop()
            _timer = None
        _pending.clear()
        log("live mode off")
        return False

    return _observer is not None


def shutdown() -> None:
    """Called when FreeCAD closes, so no observer or socket is left behind."""
    set_live(False)
    # Deliberately not stop_tunnel(): the hostname is what keeps the words
    # already typed into the lens valid, so closing FreeCAD leaves the
    # tunnel up and the next session adopts it. The toolbar button ends it.
    if _tunnel is not None:
        _tunnel.detach()
    stop_server()
    if _relay is not None:
        _relay.stop()
    # Without this, starting the server again in the same session would find
    # every digest unchanged and send nothing, to a lens that has nothing.
    forget_sent()
