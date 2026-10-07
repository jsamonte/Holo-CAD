"""The toolbar commands. Everything GUI only lives here.

Each command is deliberately thin: it reads the selection, calls into
service, and turns any failure into a sentence in the Report view rather
than a traceback, because a traceback in the Report view is how a user
concludes the addon is broken.
"""

from __future__ import annotations

import os

import FreeCAD
import FreeCADGui

from . import exporter, service, settings

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")


def _icon(name: str) -> str:
    return os.path.join(ICONS, name)


class SendToSpectacles(object):
    """Export the selection, or everything visible, and push it."""

    def GetResources(self):
        return {
            "Pixmap": _icon("send.svg"),
            "MenuText": "Send to Spectacles",
            "ToolTip": (
                "Send the selected objects to the glasses. With nothing "
                "selected, sends every visible solid in the document."
            ),
            "Accel": "Ctrl+Shift+S",
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        try:
            service.send_selected()
        except exporter.ExportError as e:
            service.warn(str(e))
        except OSError as e:
            service.error("could not start the server: {0}".format(e))
        except Exception as e:
            service.error("send failed: {0}: {1}".format(type(e).__name__, e))


class ToggleLiveMode(object):
    """Re-send on every recompute, debounced."""

    def GetResources(self):
        return {
            "Pixmap": _icon("live.svg"),
            "MenuText": "Live mode",
            "ToolTip": (
                "Re-send automatically whenever the document recomputes, so "
                "the glasses follow your edits."
            ),
            "Checkable": True,
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self, index=None):
        wanted = not service.live_enabled()
        reached = service.set_live(wanted)
        if wanted and reached:
            # One send straight away, so the glasses are not left showing
            # whatever was there before live mode was switched on.
            try:
                service.send_selected()
            except exporter.ExportError as e:
                service.warn(str(e))


class ShareOverInternet(object):
    """Put a cloudflared tunnel in front of the server, and show the words.

    This is what a published lens needs. It may only use wss and https, so
    it cannot reach a plain local address, and the tunnel supplies a real
    certificate on a public hostname without anyone buying a domain or
    hosting a server.
    """

    def GetResources(self):
        return {
            "Pixmap": _icon("tunnel.svg"),
            "MenuText": "Share over the internet",
            "ToolTip": (
                "Open a secure tunnel so a published lens can reach this "
                "machine. Needs cloudflared installed. The Report view shows "
                "the words to type into the lens."
            ),
            "Checkable": True,
        }

    def IsActive(self):
        return True

    def Activated(self, index=None):
        if service.tunnel_handle().running:
            service.stop_tunnel()
            return
        if not service.start_tunnel():
            return
        service.log("waiting for the tunnel to come up, a few seconds")


class ShowSettings(object):
    """Port, scale and mesh quality, plus the url to paste into the lens."""

    def GetResources(self):
        return {
            "Pixmap": _icon("settings.svg"),
            "MenuText": "Settings",
            "ToolTip": "Holo-CAD settings, and the address the lens needs.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            from PySide import QtGui
        except ImportError:
            service.error("settings need the FreeCAD GUI")
            return
        dialog = SettingsDialog(FreeCADGui.getMainWindow())
        dialog.exec_()


def _build_dialog_class():
    """Defined lazily so importing this module never needs Qt."""
    from PySide import QtCore, QtGui

    class _SettingsDialog(QtGui.QDialog):
        def __init__(self, parent=None):
            super(_SettingsDialog, self).__init__(parent)
            self.setWindowTitle("Holo-CAD")
            self.setMinimumWidth(460)
            layout = QtGui.QVBoxLayout(self)

            # What the lens needs, which is the first thing anyone wants.
            box = QtGui.QGroupBox("Address for the lens")
            box_layout = QtGui.QVBoxLayout(box)
            self.url = QtGui.QLineEdit()
            self.url.setReadOnly(True)
            self.url.setText(
                service.bridge().socket_url() if service.server_running()
                else "the server is not running"
            )
            box_layout.addWidget(QtGui.QLabel(
                "Paste this into the lens BridgeClient bridgeUrl input:"))
            box_layout.addWidget(self.url)
            row = QtGui.QHBoxLayout()
            self.status = QtGui.QLabel(self._status_text())
            row.addWidget(self.status)
            row.addStretch(1)
            self.toggle = QtGui.QPushButton(
                "Stop server" if service.server_running() else "Start server")
            self.toggle.clicked.connect(self._toggle_server)
            row.addWidget(self.toggle)
            box_layout.addLayout(row)
            layout.addWidget(box)

            # Relay, for a published lens that cannot reach this machine.
            relay_box = QtGui.QGroupBox("Relay, for a published lens")
            relay_form = QtGui.QFormLayout(relay_box)
            self.use_relay = QtGui.QCheckBox("Also send through a relay")
            self.use_relay.setChecked(bool(settings.get("UseRelay")))
            self.use_relay.setToolTip(
                "A lens installed from Lens Explorer cannot reach this "
                "machine directly, so it talks to a relay instead and this "
                "uploads there as well. Leave off when the lens is on the "
                "same network.")
            relay_form.addRow("", self.use_relay)

            self.relay_url = QtGui.QLineEdit(str(settings.get("RelayUrl")))
            self.relay_url.setPlaceholderText("https://relay.example.com")
            relay_form.addRow("Relay address", self.relay_url)

            self.pairing_code = QtGui.QLineEdit(str(settings.get("PairingCode")))
            self.pairing_code.setPlaceholderText("the code shown on the glasses")
            self.pairing_code.setToolTip(
                "The lens shows a code when it connects to the relay. Type "
                "it here so the relay knows which glasses are yours.")
            relay_form.addRow("Pairing code", self.pairing_code)
            self.use_relay.toggled.connect(self._relay_toggled)
            layout.addWidget(relay_box)

            form = QtGui.QFormLayout()
            self.port = QtGui.QSpinBox()
            self.port.setRange(1024, 65535)
            self.port.setValue(int(settings.get("Port")))
            form.addRow("Port", self.port)

            self.auto_start = QtGui.QCheckBox(
                "Start the server when FreeCAD opens this workbench")
            self.auto_start.setChecked(bool(settings.get("AutoStart")))
            form.addRow("", self.auto_start)

            self.mode = QtGui.QComboBox()
            self.mode.addItems(["true size 1:1", "ratio", "fit to a size"])
            self.mode.setCurrentIndex(
                settings.SCALE_MODES.index(settings.get("ScaleMode"))
                if settings.get("ScaleMode") in settings.SCALE_MODES else 0)
            self.mode.currentIndexChanged.connect(self._mode_changed)
            form.addRow("Scale", self.mode)

            self.factor = QtGui.QDoubleSpinBox()
            self.factor.setDecimals(4)
            self.factor.setRange(0.0001, 1000.0)
            self.factor.setValue(float(settings.get("ScaleFactor")))
            self.factor.setToolTip("0.1 shows the part at 1:10, 2 shows it at 2:1")
            form.addRow("Ratio factor", self.factor)

            self.target = QtGui.QDoubleSpinBox()
            self.target.setDecimals(1)
            self.target.setRange(1.0, 100000.0)
            self.target.setSuffix(" mm")
            self.target.setValue(float(settings.get("FitTargetMm")))
            self.target.setToolTip("The largest dimension becomes this size")
            form.addRow("Fit target", self.target)

            self.quality = QtGui.QComboBox()
            self.quality.addItems(list(settings.QUALITIES))
            self.quality.setCurrentIndex(
                list(settings.QUALITIES).index(settings.quality()))
            self.quality.setToolTip(
                "How finely curves are tessellated. Finer means more triangles "
                "and a slower send.")
            form.addRow("Mesh quality", self.quality)

            self.debounce = QtGui.QSpinBox()
            self.debounce.setRange(0, 10000)
            self.debounce.setSuffix(" ms")
            self.debounce.setValue(int(settings.get("LiveDebounceMs")))
            self.debounce.setToolTip(
                "In live mode, how long the document must be quiet before "
                "sending")
            form.addRow("Live delay", self.debounce)

            self.per_body = QtGui.QCheckBox(
                "Send each object as its own model")
            self.per_body.setChecked(bool(settings.get("PerBody")))
            self.per_body.setToolTip(
                "On, every object becomes its own model and only the ones "
                "that changed are re-sent, which is what makes live mode "
                "cheap on a document with several bodies. Off, the whole "
                "selection goes as a single model.")
            form.addRow("", self.per_body)

            self.prefer_stock = QtGui.QCheckBox(
                "Try FreeCAD's own glTF exporter first")
            self.prefer_stock.setChecked(bool(settings.get("PreferStockExporter")))
            self.prefer_stock.setToolTip(
                "Off by default. The built in exporter has had trouble with "
                "placements inside Links and App::Part, and with mirrored "
                "objects, which the addon's own exporter handles.")
            form.addRow("", self.prefer_stock)

            layout.addLayout(form)

            buttons = QtGui.QDialogButtonBox(
                QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self._mode_changed()
            self._relay_toggled()

        def _relay_toggled(self, *args):
            on = self.use_relay.isChecked()
            self.relay_url.setEnabled(on)
            self.pairing_code.setEnabled(on)

        def _status_text(self):
            if not service.server_running():
                return "Not serving."
            count = service.bridge().lens_count
            return "Serving. {0} lens{1} connected.".format(
                count, "" if count == 1 else "es")

        def _mode_changed(self, *args):
            mode = settings.SCALE_MODES[self.mode.currentIndex()]
            self.factor.setEnabled(mode == "ratio")
            self.target.setEnabled(mode == "fit")

        def _toggle_server(self):
            if service.server_running():
                service.stop_server()
                self.url.setText("the server is not running")
                self.toggle.setText("Start server")
            else:
                settings.set_value("Port", self.port.value())
                try:
                    self.url.setText(service.start_server())
                    self.toggle.setText("Stop server")
                except OSError as e:
                    self.url.setText("could not start: {0}".format(e))
            self.status.setText(self._status_text())

        def _save(self):
            port_changed = int(settings.get("Port")) != self.port.value()
            settings.set_value("Port", self.port.value())
            settings.set_value("AutoStart", self.auto_start.isChecked())
            settings.set_value(
                "ScaleMode", settings.SCALE_MODES[self.mode.currentIndex()])
            settings.set_value("ScaleFactor", self.factor.value())
            settings.set_value("FitTargetMm", self.target.value())
            settings.set_value("Quality", self.quality.currentText())
            settings.set_value("LiveDebounceMs", self.debounce.value())
            settings.set_value("PreferStockExporter", self.prefer_stock.isChecked())
            if bool(settings.get("PerBody")) != self.per_body.isChecked():
                settings.set_value("PerBody", self.per_body.isChecked())
                # The two modes use different model ids, so what the lens is
                # holding no longer matches what would be sent next.
                service.forget_sent()
            settings.set_value("PerBody", self.per_body.isChecked())
            settings.set_value("UseRelay", self.use_relay.isChecked())
            settings.set_value("RelayUrl", self.relay_url.text().strip())
            settings.set_value("PairingCode",
                               self.pairing_code.text().strip().upper())
            if self.use_relay.isChecked() and not settings.relay_target():
                service.warn(
                    "relay sending is on but the address or pairing code is "
                    "missing, so nothing will be uploaded")
            if port_changed and service.server_running():
                service.stop_server()
                service.start_server()
            service.log("settings saved, scale is {0}".format(
                settings.describe_scale()))
            self.accept()

    return _SettingsDialog


class _LazyDialog(object):
    """Stands in for the dialog class until Qt is actually available."""

    _real = None

    def __call__(self, parent=None):
        if _LazyDialog._real is None:
            _LazyDialog._real = _build_dialog_class()
        return _LazyDialog._real(parent)


SettingsDialog = _LazyDialog()

COMMANDS = (
    ("SpecsLink_Send", SendToSpectacles),
    ("SpecsLink_Live", ToggleLiveMode),
    ("SpecsLink_Share", ShareOverInternet),
    ("SpecsLink_Settings", ShowSettings),
)


def register() -> list:
    """Register every command and return their names, in toolbar order."""
    names = []
    for name, cls in COMMANDS:
        FreeCADGui.addCommand(name, cls())
        names.append(name)
    return names
