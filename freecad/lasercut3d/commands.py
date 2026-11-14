"""Comandos da barra de ferramentas."""
from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide import QtCore, QtWidgets
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets

from . import ICON, deps


def _main_window():
    try:
        return Gui.getMainWindow()
    except Exception:
        return None


def install_dependencies_dialog() -> bool:
    problem = deps.numpy_problem()
    if problem:
        QtWidgets.QMessageBox.critical(_main_window(), "3D2LaserCut", problem)
        return False
    pkgs = deps.missing()
    if not pkgs:
        QtWidgets.QMessageBox.information(_main_window(), "3D2LaserCut",
                                          "Todas as dependências já estão instaladas.")
        return True
    ans = QtWidgets.QMessageBox.question(
        _main_window(), "3D2LaserCut — instalar dependências",
        "Para funcionar, o 3D2LaserCut precisa destes pacotes Python, que ainda não estão "
        "no FreeCAD:\n\n  " + ", ".join(pkgs) +
        f"\n\nEles serão instalados (via pip, precisa de internet) em:\n{deps.target_dir()}\n\n"
        "O numpy do FreeCAD não é alterado. Instalar agora?")
    if ans != QtWidgets.QMessageBox.Yes:
        return False
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        ok, out = deps.install(pkgs)
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()
    App.Console.PrintMessage(out + "\n")
    if ok:
        QtWidgets.QMessageBox.information(_main_window(), "3D2LaserCut",
                                          "Dependências instaladas. Pode usar o workbench.")
    else:
        QtWidgets.QMessageBox.warning(
            _main_window(), "3D2LaserCut",
            "A instalação não terminou bem. Veja os detalhes na janela Relatório "
            "(Exibir → Painéis → Relatório).\n\nFaltando: " + ", ".join(deps.missing()))
    return ok


class OpenPanel:
    def GetResources(self):  # noqa: N802 (API do FreeCAD)
        return {"Pixmap": ICON,
                "MenuText": "Preparar para corte a laser",
                "ToolTip": "Selecione um sólido ou malha e gere as peças para a laser, "
                           "com prévia ao vivo"}

    def IsActive(self):  # noqa: N802
        return App.ActiveDocument is not None

    def Activated(self):  # noqa: N802
        if deps.missing() and not install_dependencies_dialog():
            return
        sel = Gui.Selection.getSelection()
        from .fcbridge import is_supported
        if len(sel) != 1 or not is_supported(sel[0]):
            QtWidgets.QMessageBox.information(
                _main_window(), "3D2LaserCut",
                "Selecione um (1) objeto sólido ou malha na árvore ou na vista 3D "
                "e clique de novo.")
            return
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        from .panel import LaserCutPanel
        Gui.Control.showDialog(LaserCutPanel(sel[0]))


class InstallDeps:
    def GetResources(self):  # noqa: N802
        return {"Pixmap": ICON,
                "MenuText": "Instalar dependências",
                "ToolTip": "Instala no FreeCAD os pacotes Python que o 3D2LaserCut usa"}

    def IsActive(self):  # noqa: N802
        return True

    def Activated(self):  # noqa: N802
        install_dependencies_dialog()


Gui.addCommand("LaserCut_Open", OpenPanel())
Gui.addCommand("LaserCut_InstallDeps", InstallDeps())
