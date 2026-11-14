"""Ponte FreeCAD <-> núcleo: objeto do documento -> malha; prévia -> objetos."""
from __future__ import annotations

import re

import FreeCAD as App
import numpy as np

PREVIEW_GROUP = "LaserCut_Previa"
SHEETS_GROUP = "LaserCut_Chapas"
ROLE_COLORS = {"cut": (0.9, 0.1, 0.1), "engrave": (0.1, 0.2, 0.9), "sheet": (0.55, 0.55, 0.55)}


# ------------------------------------------------------------------ entrada

def is_supported(obj) -> bool:
    return obj.isDerivedFrom("Mesh::Feature") or (
        hasattr(obj, "Shape") and not obj.Shape.isNull() and len(obj.Shape.Faces) > 0)


def object_to_trimesh(obj, deflection: float = 0.1):
    """Malha do objeto em coordenadas globais (mm)."""
    import trimesh
    if obj.isDerivedFrom("Mesh::Feature"):
        points, facets = obj.Mesh.Topology
    else:
        import MeshPart
        import Part
        shape = Part.getShape(obj)  # já com a posição global (inclusive dentro de grupos)
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection,
                                      AngularDeflection=0.0872, Relative=False)
        points, facets = mesh.Topology
    verts = np.array([[p.x, p.y, p.z] for p in points], dtype=float)
    faces = np.array(facets, dtype=np.int64)
    return trimesh.Trimesh(vertices=verts, faces=faces, process=True)


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


# ------------------------------------------------------------------ aparência

def _set_color(vo, rgb, transparency: int | None = None) -> None:
    try:
        mat = App.Material()
        mat.DiffuseColor = tuple(rgb)
        vo.ShapeAppearance = (mat,)
    except Exception:
        try:
            vo.ShapeColor = tuple(rgb)
        except Exception:
            pass
    if transparency is not None:
        try:
            vo.Transparency = transparency
        except Exception:
            pass


def _set_line(vo, rgb, width: float = 1.0) -> None:
    for prop, val in (("LineColor", tuple(rgb)), ("LineWidth", width), ("PointSize", 1.0)):
        try:
            setattr(vo, prop, val)
        except Exception:
            pass


# ------------------------------------------------------------------ prévia

class Preview:
    """Objetos de prévia num grupo próprio do documento."""

    def __init__(self, doc):
        self.doc = doc
        self.objects: dict[str, object] = {}   # nome da peça -> objeto Mesh::Feature

    def _group(self, name: str, label: str):
        grp = self.doc.getObject(name)
        if grp is None:
            grp = self.doc.addObject("App::DocumentObjectGroup", name)
            grp.Label = label
        return grp

    def _clear(self, name: str) -> None:
        grp = self.doc.getObject(name)
        if grp is None:
            return
        for child in list(grp.Group):
            self.doc.removeObject(child.Name)

    def show_items(self, items, colors) -> None:
        import Mesh
        self._clear(PREVIEW_GROUP)
        self.objects.clear()
        grp = self._group(PREVIEW_GROUP, "Prévia corte a laser")
        for it in items:
            obj = self.doc.addObject("Mesh::Feature", "LC_" + safe_name(it.name))
            obj.Label = it.name
            obj.Mesh = Mesh.Mesh(it.mesh.triangles.reshape(-1, 3).tolist())
            grp.addObject(obj)
            vo = getattr(obj, "ViewObject", None)
            if vo is not None:
                _set_color(vo, colors.get(it.kind, (0.8, 0.8, 0.8)))
                try:
                    vo.DisplayMode = "Shaded"
                except Exception:
                    pass
            self.objects[it.name] = obj
        self.doc.recompute()

    def explode(self, items, offsets) -> None:
        for it, off in zip(items, offsets):
            obj = self.objects.get(it.name)
            if obj is not None:
                obj.Placement = App.Placement(App.Vector(*map(float, off)), App.Rotation())

    def show_sheets(self, polylines) -> None:
        import Part
        self._clear(SHEETS_GROUP)
        grp = self._group(SHEETS_GROUP, "Chapas de corte")
        by_role: dict[str, list] = {}
        for pl in polylines:
            pts = [App.Vector(*map(float, p)) for p in pl.points]
            if pl.closed:
                pts.append(pts[0])
            if len(pts) >= 2:
                by_role.setdefault(pl.role, []).append(Part.makePolygon(pts))
        labels = {"sheet": "Chapas (limite)", "cut": "Corte", "engrave": "Gravação"}
        for role, wires in by_role.items():
            obj = self.doc.addObject("Part::Feature", "LC_chapas_" + role)
            obj.Label = labels.get(role, role)
            obj.Shape = Part.makeCompound(wires)
            grp.addObject(obj)
            vo = getattr(obj, "ViewObject", None)
            if vo is not None:
                _set_line(vo, ROLE_COLORS.get(role, (0, 0, 0)), 1.0 if role != "sheet" else 2.0)
        self.doc.recompute()

    def name_of(self, obj) -> str | None:
        for name, o in self.objects.items():
            if o is obj or getattr(o, "Name", None) == getattr(obj, "Name", ""):
                return name
        return None

    def remove_all(self) -> None:
        for gname in (PREVIEW_GROUP, SHEETS_GROUP):
            self._clear(gname)
            if self.doc.getObject(gname) is not None:
                self.doc.removeObject(gname)
        self.objects.clear()
        self.doc.recompute()
