"""Fatiamento da malha em camadas 2D com a espessura do material."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import reduce

import numpy as np
import trimesh
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .config import SliceConfig


@dataclass
class Layer:
    index: int                      # 0 = camada de baixo
    z_cut: float                    # altura (no modelo) onde a seção foi tirada
    islands: list[Polygon] = field(default_factory=list)

    @property
    def shape(self) -> BaseGeometry:
        return MultiPolygon(self.islands) if self.islands else Polygon()


def layer_count(height: float, thickness: float) -> int:
    return max(1, int(round(height / thickness)))


def section_heights(height: float, n_layers: int) -> np.ndarray:
    """Alturas de corte no meio de cada camada, distribuídas na altura real do modelo."""
    step = height / n_layers
    return (np.arange(n_layers) + 0.5) * step


def polygons(geom: BaseGeometry) -> list[Polygon]:
    """Extrai os polígonos de qualquer geometria do shapely."""
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in polygons(g)]
    return []


def path_to_shape(path: trimesh.path.Path2D | None) -> BaseGeometry:
    """Converte os laços fechados de uma seção em área preenchida (regra par/ímpar).

    O XOR dos laços resolve furos, ilhas dentro de furos etc. sem depender
    da orientação dos laços.
    """
    if path is None:
        return Polygon()
    loops = []
    for pts in path.discrete:
        if len(pts) < 3:
            continue
        poly = make_valid(Polygon(pts))
        if not poly.is_empty and poly.area > 0:
            loops.append(poly)
    if not loops:
        return Polygon()
    return make_valid(reduce(lambda a, b: a.symmetric_difference(b), loops))


def clean_shape(shape: BaseGeometry, cfg: SliceConfig) -> list[Polygon]:
    if cfg.min_feature > 0:
        r = cfg.min_feature / 2.0
        shape = shape.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")
    if cfg.simplify > 0:
        shape = shape.simplify(cfg.simplify, preserve_topology=True)
    shape = make_valid(shape)
    islands = [p for p in polygons(shape) if p.area >= cfg.min_area]
    # ordem estável: maior primeiro
    return sorted(islands, key=lambda p: -p.area)


def slice_mesh(mesh: trimesh.Trimesh, cfg: SliceConfig) -> list[Layer]:
    """Fatia uma malha já preparada (base em Z=0, empilhamento em +Z)."""
    height = float(mesh.bounds[1][2])
    n = layer_count(height, cfg.thickness)
    heights = section_heights(height, n)
    # Cada corpo é fatiado separadamente (par/ímpar dentro do corpo) e os
    # resultados são unidos: assim corpos sobrepostos não viram "buracos".
    bodies = list(mesh.split(only_watertight=False)) or [mesh]
    shapes: list[list[BaseGeometry]] = [[] for _ in heights]
    for body in bodies:
        sections = body.section_multiplane(
            plane_origin=[0.0, 0.0, 0.0], plane_normal=[0.0, 0.0, 1.0], heights=heights
        )
        for i, sec in enumerate(sections):
            shape = path_to_shape(sec)
            if not shape.is_empty:
                shapes[i].append(shape)

    layers = []
    for i, z in enumerate(heights):
        merged = make_valid(unary_union(shapes[i])) if shapes[i] else Polygon()
        layers.append(Layer(index=i, z_cut=float(z), islands=clean_shape(merged, cfg)))
    return layers
