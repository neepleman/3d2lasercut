"""Montagem 3D: cada peça cortada posicionada onde fica no modelo montado.

Serve para a prévia em STL, para a prévia ao vivo no FreeCAD (com vista
explodida) e para os testes de interferência. As coordenadas são as do
modelo *preparado* (eixo escolhido -> +Z, escala, base em Z = 0); use
`Result.transform` para voltar às coordenadas originais.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from .plates import plate_solid
from .waffle import waffle_preview

KINDS = ("layer", "slice", "plate", "skin", "rib")


@dataclass
class AssemblyItem:
    name: str
    kind: str                       # um de KINDS
    mesh: trimesh.Trimesh
    normal: np.ndarray | None = None  # direção "para fora" (placas), p/ vista explodida

    @property
    def center(self) -> np.ndarray:
        return np.asarray(self.mesh.bounds).mean(axis=0)


def assembly_items(result, cfg) -> list[AssemblyItem]:
    t = cfg.thickness
    items: list[AssemblyItem] = []
    if result.plates is not None:
        pi = result.plates
        for P in pi.plates:
            m = plate_solid(P, cfg)
            if m is not None:
                items.append(AssemblyItem(P.name, "plate", m, np.asarray(P.normal)))
        for sk in pi.skins:
            m = sk.solid(t)
            if m is not None:
                items.append(AssemblyItem(sk.name, "skin", m))
        if pi.ribs is not None:
            for part, pl in zip(pi.ribs.parts, pi.ribs.placements):
                m = waffle_preview([pl], t)
                if m is not None:
                    items.append(AssemblyItem(part.name, "rib", m))
    elif result.waffle is not None:
        for part, pl in zip(result.waffle.parts, result.waffle.placements):
            m = waffle_preview([pl], t)
            if m is not None:
                items.append(AssemblyItem(part.name, "slice", m))
    else:
        for part in result.parts:
            m = trimesh.creation.extrude_polygon(part.shape, t)
            m.apply_translation([0, 0, part.layer_index * t])
            items.append(AssemblyItem(part.name, "layer", m, np.array([0.0, 0.0, 1.0])))
    return items


def explode_offsets(items: list[AssemblyItem], factor: float) -> list[np.ndarray]:
    """Deslocamento de cada peça para a vista explodida.

    factor = 0 -> montado; 1 -> cada peça afastada ~ metade do tamanho do modelo.
    Placas saem pela normal; camadas se afastam ao longo do eixo de
    empilhamento (a `normal` delas), proporcionalmente à posição; as demais
    peças se afastam do centro do modelo.
    """
    if not items or factor == 0:
        return [np.zeros(3) for _ in items]
    lo = np.min([it.mesh.bounds[0] for it in items], axis=0)
    hi = np.max([it.mesh.bounds[1] for it in items], axis=0)
    center, size = (lo + hi) / 2, float(np.max(hi - lo))
    out = []
    layers = [it for it in items if it.kind == "layer" and it.normal is not None]
    if layers:
        n0 = layers[0].normal
        proj = [float(it.center @ n0) for it in layers]
        smin, smax = min(proj), max(proj)
    for it in items:
        if it.kind == "layer" and it.normal is not None:
            rel = (float(it.center @ n0) - smin) / max(smax - smin, 1e-9)
            out.append(n0 * rel * size * factor)
        elif it.kind == "plate" and it.normal is not None:
            out.append(it.normal * size * 0.5 * factor)
        else:
            d = it.center - center
            n = np.linalg.norm(d)
            out.append(d / n * size * 0.25 * factor if n > 1e-9 else np.zeros(3))
    return out


def assembled_mesh(result, cfg) -> trimesh.Trimesh | None:
    items = assembly_items(result, cfg)
    return trimesh.util.concatenate([it.mesh for it in items]) if items else None
