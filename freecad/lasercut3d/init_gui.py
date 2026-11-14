"""Registro do workbench (carregado pelo FreeCAD ao iniciar)."""
import FreeCADGui as Gui

from . import ICON, deps


class LaserCutWorkbench(Gui.Workbench):
    MenuText = "3D2LaserCut"
    ToolTip = "Transforma peças 3D em peças para corte a laser (MDF, acrílico)"
    Icon = ICON

    def Initialize(self):  # noqa: N802 (API do FreeCAD)
        deps.ensure_paths()
        from . import commands  # noqa: F401  (registra os comandos)
        cmds = ["LaserCut_Open", "LaserCut_InstallDeps"]
        self.appendToolbar("3D2LaserCut", cmds)
        self.appendMenu("3D2LaserCut", cmds)

    def Activated(self):  # noqa: N802
        pass

    def Deactivated(self):  # noqa: N802
        pass

    def GetClassName(self):  # noqa: N802
        return "Gui::PythonWorkbench"


Gui.addWorkbench(LaserCutWorkbench())
