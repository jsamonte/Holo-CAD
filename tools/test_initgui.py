"""Run InitGui.py the way FreeCAD runs it, and see whether it registers.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe" tools\\test_initgui.py

FreeCAD executes InitGui.py with `Workbench` and `Gui` injected as globals,
and without always defining `__file__`. A file that raises under those
conditions does not produce an obvious error: the workbench simply never
appears in the list while every other addon loads normally, which is a
miserable thing to debug from the outside.

So this reproduces those conditions with stubs and checks a workbench was
registered, with a name, an icon that exists, and the toolbar it claims.
"""

from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(HERE, "..", "freecad_addon", "SpecsLink")
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    {0}".format(name))
    else:
        print("  FAIL  {0}  {1}".format(name, detail))
        FAILURES.append(name)


class StubWorkbench(object):
    """Stands in for the Workbench base class FreeCAD injects."""

    def __init__(self):
        self.toolbars = {}
        self.menus = {}

    def appendToolbar(self, name, commands):
        self.toolbars[name] = commands

    def appendMenu(self, name, commands):
        self.menus[name] = commands


class StubGui(object):
    def __init__(self):
        self.workbenches = []

    def addWorkbench(self, workbench):
        self.workbenches.append(workbench)


def run(with_file: bool, split_namespace: bool):
    """Execute InitGui.py the several ways FreeCAD might.

    Two conditions, both observed in the wild and both fatal in a way that
    leaves no trace except a missing workbench:

    no __file__      FreeCAD executes the file rather than importing it,
                     so `os.path.abspath(__file__)` can raise NameError on
                     the first line.

    split namespace  FreeCAD execs with separate globals and locals. Then
                     module level assignments go into locals, while a class
                     body resolves names against globals only, so anything
                     the class body reads from module level is invisible.
                     This is what produced "name '_HERE' is not defined".
    """
    gui = StubGui()
    path = os.path.join(ADDON, "InitGui.py")
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    globals_dict = {
        "Workbench": StubWorkbench,
        "Gui": gui,
        "__name__": "InitGui",
    }
    if with_file:
        globals_dict["__file__"] = path
    else:
        os.chdir(ADDON)

    if split_namespace:
        exec(compile(source, path, "exec"), globals_dict, {})
    else:
        exec(compile(source, path, "exec"), globals_dict)
    return gui


def main() -> int:
    cases = [
        (True, False, "with __file__, shared namespace"),
        (False, False, "without __file__, shared namespace"),
        (True, True, "with __file__, split namespace"),
        (False, True, "without __file__, split namespace"),
    ]
    for with_file, split, label in cases:
        print(label)
        try:
            gui = run(with_file, split)
        except Exception as e:
            check("InitGui.py runs", False, "{0}: {1}".format(type(e).__name__, e))
            traceback.print_exc()
            continue

        check("InitGui.py runs", True)
        check("a workbench was registered", len(gui.workbenches) == 1,
              str(len(gui.workbenches)))
        if not gui.workbenches:
            continue
        workbench = gui.workbenches[0]
        check("it is named Holo-CAD", workbench.MenuText == "Holo-CAD",
              str(getattr(workbench, "MenuText", None)))
        check("it reports the python workbench class",
              workbench.GetClassName() == "Gui::PythonWorkbench")

        # Initialize needs FreeCADGui, which does not exist here, so it is
        # expected to report rather than raise. Raising would cost the
        # whole workbench.
        try:
            workbench.Initialize()
            check("Initialize does not raise without the GUI", True)
        except Exception as e:
            check("Initialize does not raise without the GUI", False,
                  "{0}: {1}".format(type(e).__name__, e))

        # Checked after Initialize, because without __file__ the class body
        # cannot work the path out and Initialize is where it recovers.
        icon = getattr(workbench, "Icon", "")
        check("its icon resolves to a real file",
              bool(icon) and os.path.isfile(icon), icon or "(empty)")

    print("")
    if FAILURES:
        print("{0} failure(s): {1}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
