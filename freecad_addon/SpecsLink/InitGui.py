"""Workbench registration. FreeCAD runs this at startup when the GUI is up.

The addon's own directory goes on sys.path so that `specslink` imports as a
package. Keeping the modules in a package matters: FreeCAD puts every
addon's folder on sys.path, so a loose module called settings or service
would be a collision waiting to happen.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class SpecsLinkWorkbench(Workbench):  # noqa: F821, provided by FreeCAD
    MenuText = "Holo-CAD"
    ToolTip = "Send FreeCAD models to Snapchat Spectacles at true size"

    def __init__(self):
        self.__class__.Icon = os.path.join(_HERE, "icons", "workbench.svg")

    def Initialize(self):
        from specslink import commands

        names = commands.register()
        self.appendToolbar("Holo-CAD", names)
        self.appendMenu("Holo-CAD", names)

    def Activated(self):
        from specslink import service, settings

        if settings.get("AutoStart") and not service.server_running():
            try:
                service.start_server()
            except OSError:
                # start_server has already explained itself in the Report
                # view, and a failed port must not block the workbench.
                pass

    def Deactivated(self):
        # The server keeps running on purpose. Switching to Part or Sketcher
        # to do some modelling should not drop the glasses' connection.
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SpecsLinkWorkbench())  # noqa: F821, provided by FreeCAD
