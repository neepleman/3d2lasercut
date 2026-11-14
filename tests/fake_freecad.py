"""Simulação mínima da API do FreeCAD usada pelo workbench.

Permite rodar o painel (PySide6, modo offscreen) nos testes sem o FreeCAD.
Só implementa o que o workbench usa; não é uma réplica do FreeCAD.
"""
from __future__ import annotations

import sys
import types

import numpy as np


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Rotation:
    pass


class Placement:
    def __init__(self, base=None, rot=None):
        self.Base = base or Vector()
        self.Rotation = rot or Rotation()


class Material:
    DiffuseColor = (0.8, 0.8, 0.8)


class Console:
    messages: list[str] = []

    @classmethod
    def PrintMessage(cls, text):
        cls.messages.append(text)

    PrintError = PrintWarning = PrintMessage


class ViewObject:
    def __init__(self):
        self.Visibility = True


class DocObject:
    def __init__(self, doc, type_id, name):
        self.Document, self.TypeId, self.Name, self.Label = doc, type_id, name, name
        self.ViewObject = ViewObject()
        self.Placement = Placement()
        self.Group = []

    def isDerivedFrom(self, t):
        return self.TypeId == t

    def addObject(self, obj):
        self.Group.append(obj)


class Document:
    def __init__(self, name="Doc"):
        self.Name = name
        self.objects: dict[str, DocObject] = {}

    def addObject(self, type_id, name):
        base, i = name, 1
        while name in self.objects:
            name, i = f"{base}{i:03d}", i + 1
        obj = DocObject(self, type_id, name)
        self.objects[name] = obj
        return obj

    def getObject(self, name):
        return self.objects.get(name)

    def removeObject(self, name):
        obj = self.objects.pop(name, None)
        for o in self.objects.values():
            if obj in o.Group:
                o.Group.remove(obj)

    def recompute(self):
        pass


class MeshData:
    """Mesh.Mesh: lista plana de pontos, 3 a 3 formam um triângulo."""

    def __init__(self, points=None):
        pts = np.asarray(points if points is not None else [], dtype=float).reshape(-1, 3)
        self.points = pts

    @property
    def Topology(self):
        verts = [Vector(*p) for p in self.points]
        facets = [(i, i + 1, i + 2) for i in range(0, len(self.points), 3)]
        return verts, facets

    @property
    def CountFacets(self):
        return len(self.points) // 3


class Wire:
    def __init__(self, pts):
        self.pts = pts


def install(monkeypatch, doc: Document, selection=None):
    """Registra os módulos falsos em sys.modules (desfeito pelo monkeypatch)."""
    App = types.ModuleType("FreeCAD")
    App.Vector, App.Placement, App.Rotation, App.Material = Vector, Placement, Rotation, Material
    App.Console = Console
    App.ActiveDocument = doc
    App.getUserAppDataDir = lambda: "/tmp/fake_freecad_user/"

    Gui = types.ModuleType("FreeCADGui")

    class Workbench:
        pass

    class Selection:
        observers: list = []

        @staticmethod
        def addObserver(o):
            Selection.observers.append(o)

        @staticmethod
        def removeObserver(o):
            Selection.observers.remove(o)

        @staticmethod
        def getSelection():
            return list(selection or [])

    class Control:
        dialog = None
        closed = 0

        @staticmethod
        def showDialog(d):
            Control.dialog = d

        @staticmethod
        def activeDialog():
            return Control.dialog is not None

        @staticmethod
        def closeDialog():
            Control.dialog = None
            Control.closed += 1

    Gui.Workbench, Gui.Selection, Gui.Control = Workbench, Selection, Control
    Gui.commands = {}
    Gui.addCommand = lambda name, cmd: Gui.commands.__setitem__(name, cmd)
    Gui.addWorkbench = lambda wb: None
    Gui.SendMsgToActiveView = lambda msg: None
    Gui.getMainWindow = lambda: None

    Mesh = types.ModuleType("Mesh")
    Mesh.Mesh = MeshData
    MeshPart = types.ModuleType("MeshPart")
    Part = types.ModuleType("Part")
    Part.makePolygon = lambda pts: Wire(pts)
    Part.makeCompound = lambda ws: list(ws)
    Part.getShape = lambda obj: obj.Shape

    from PySide6 import QtCore, QtGui, QtWidgets
    PySide = types.ModuleType("PySide")
    PySide.QtCore, PySide.QtGui, PySide.QtWidgets = QtCore, QtGui, QtWidgets

    for name, mod in (("FreeCAD", App), ("FreeCADGui", Gui), ("Mesh", Mesh),
                      ("MeshPart", MeshPart), ("Part", Part), ("PySide", PySide),
                      ("PySide.QtCore", QtCore), ("PySide.QtGui", QtGui),
                      ("PySide.QtWidgets", QtWidgets)):
        monkeypatch.setitem(sys.modules, name, mod)
    return App, Gui


def mesh_object(doc: Document, tm, label="Arco"):
    """Um Mesh::Feature com a malha do trimesh `tm`."""
    obj = doc.addObject("Mesh::Feature", label)
    obj.Mesh = MeshData(tm.triangles.reshape(-1, 3))
    return obj
