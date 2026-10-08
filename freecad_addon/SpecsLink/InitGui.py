"""Workbench registration. FreeCAD runs this at startup when the GUI is up.

Written defensively, because FreeCAD does not run this file the way Python
normally runs a module, and every way it differs is fatal in the same
silent manner: the workbench simply never appears in the list, with other
addons loading normally beside it.

Two differences matter, both seen for real:

  No ``__file__``.    The file is executed, not imported, so anything that
                      reads ``__file__`` can raise NameError on the first
                      line and take the whole workbench with it.

  Split namespace.    It is exec'd with separate globals and locals. Module
                      level assignments land in locals, while a class body
                      resolves names against globals only. So a class body
                      reading anything defined at module level raises
                      NameError, which is what produced
                      "name '_HERE' is not defined".

Hence: nothing at module level except the class and the registration call,
and nothing in the class body that is not self contained. Paths are worked
out inside methods. `tools/test_initgui.py` runs this file under all four
combinations.
"""


class SpecsLinkWorkbench(Workbench):  # noqa: F821, provided by FreeCAD
    MenuText = "Holo-CAD"
    ToolTip = "Send FreeCAD models to Snapchat Spectacles at true size"

    # Self contained on purpose: an import inside the class body binds into
    # the class namespace, so this works even when module level names do
    # not reach here. Guarded because __file__ may be missing, and losing
    # the icon is better than losing the workbench.
    try:
        import os as _os

        Icon = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "icons",
            "workbench.svg",
        )
        del _os
    except Exception:
        Icon = ""

    def addonDir(self):
        """This addon's folder, however FreeCAD chose to run the file."""
        import inspect
        import os

        try:
            return os.path.dirname(os.path.abspath(__file__))
        except NameError:
            # No __file__, so ask the frame where its code came from. The
            # path compiled into the code object is the real one.
            return os.path.dirname(
                os.path.abspath(inspect.getfile(inspect.currentframe()))
            )

    def Initialize(self):
        # Reported rather than raised. An exception here loses the whole
        # workbench with nothing but a traceback, and the likeliest cause
        # by far is a single bad import.
        import os
        import sys
        import traceback

        try:
            here = self.addonDir()
            if here not in sys.path:
                # So `specslink` imports as a package. It is a package
                # rather than loose modules because FreeCAD puts every
                # addon's folder on sys.path, where a module called
                # settings or service would be a collision waiting to
                # happen.
                sys.path.insert(0, here)

            icon = os.path.join(here, "icons", "workbench.svg")
            if os.path.isfile(icon):
                self.__class__.Icon = icon

            from specslink import commands

            names = commands.register()
            self.appendToolbar("Holo-CAD", names)
            self.appendMenu("Holo-CAD", names)
        except Exception as e:
            try:
                import FreeCAD

                FreeCAD.Console.PrintError(
                    "Holo-CAD could not start: {0}\n{1}\n".format(
                        e, traceback.format_exc()
                    )
                )
            except Exception:
                print("Holo-CAD could not start: {0}".format(e))
                traceback.print_exc()

    def Activated(self):
        try:
            from specslink import service, settings

            if settings.get("AutoStart") and not service.server_running():
                service.start_server()
        except Exception:
            # start_server explains itself in the Report view, and a port
            # that is taken must not block the workbench from opening.
            pass

    def Deactivated(self):
        # The server keeps running on purpose. Switching to Part or Sketcher
        # to do some modelling should not drop the glasses' connection.
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SpecsLinkWorkbench())  # noqa: F821, provided by FreeCAD
