"""Nível de detalhe: um único controle (0 a 100) para "quanta miudeza manter".

Com pouco detalhe, partes pequenas e não estruturais somem ou são
incorporadas a uma peça vizinha:

- faces planas pequenas deixam de virar placa;
- degraus, ressaltos e rebaixos rasos são "achatados" no plano da placa
  maior ao lado (a face pequena é incorporada à placa grande);
- lascas (ilhas pequenas que sobram de uma peça depois de furos e dedos) e
  pedacinhos de nervura são descartados;
- furos minúsculos são fechados;
- arredondamentos maiores viram cantos vivos.

Os limites são proporcionais ao tamanho do modelo (área da superfície) e à
espessura da chapa. Um valor que o usuário mudou à mão (diferente do padrão)
sempre vence o que o nível de detalhe sugere.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import trimesh
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import polylabel, unary_union

from .config import SliceConfig
from .plates import Plate, find_plates, to_local
from .slicer import polygons

DEFAULTS = SliceConfig()


def _loglerp(low_detail: float, high_detail: float, f: float) -> float:
    return math.exp(math.log(low_detail) + (math.log(high_detail) - math.log(low_detail)) * f)


def _lerp(low_detail: float, high_detail: float, f: float) -> float:
    return low_detail + (high_detail - low_detail) * f


def detail_values(level: float, surface_area: float, t: float) -> dict:
    """Limites sugeridos para um nível de detalhe (0 = pouco, 100 = máximo)."""
    f = min(max(level, 0.0), 100.0) / 100.0
    return dict(
        plate_min_area=max(50.0, surface_area * _loglerp(0.025, 0.001, f)),
        plate_min_width=t * _loglerp(8.0, 2.0, f),
        absorb_depth=t * _lerp(3.0, 0.0, f),
        max_fillet_radius=_loglerp(25.0, 3.0, f),
        island_min_fraction=_loglerp(0.25, 0.005, f),
        min_area=_loglerp(200.0, 4.0, f),
        min_hole_diameter=_lerp(3.0, 0.0, f),
        min_feature=_lerp(1.5, 0.0, f),
    )


def apply_detail(cfg: SliceConfig, mesh: trimesh.Trimesh) -> SliceConfig:
    """Configuração efetiva: valores do nível de detalhe, exceto os mudados à mão."""
    if cfg.detail is None:
        return cfg
    values = detail_values(cfg.detail, float(mesh.area), cfg.thickness)
    chosen = {k: v for k, v in values.items() if getattr(cfg, k) == getattr(DEFAULTS, k)}
    return dataclasses.replace(cfg, **chosen)


# ------------------------------------------------------------------ incorporação

def _inradius(poly: Polygon) -> float:
    try:
        return float(poly.exterior.distance(polylabel(poly, tolerance=0.1)))
    except Exception:
        return 0.0


def _big(P: Plate, cfg: SliceConfig) -> bool:
    polys = polygons(P.base)
    return (P.base.area >= cfg.plate_min_area and bool(polys)
            and max(_inradius(p) for p in polys) * 2 >= cfg.plate_min_width)


def _project(S: Plate, B: Plate):
    """Contorno de S levado ao plano (paralelo) de B."""
    Minv = np.linalg.inv(S.M)

    def move(coords):
        c = np.asarray(coords)
        w = np.column_stack([c, np.zeros(len(c)), np.ones(len(c))]) @ Minv.T
        return to_local(B.M, w[:, :3])[:, :2]

    out = []
    for p in polygons(S.base):
        out.append(make_valid(Polygon(move(p.exterior.coords),
                                      [move(r.coords) for r in p.interiors])))
    return unary_union(out) if out else Polygon()


def find_plates_absorbing(mesh: trimesh.Trimesh, cfg: SliceConfig):
    """Como `find_plates`, mas faces planas pequenas paralelas a uma placa grande,
    a até `absorb_depth` dela (degraus, ressaltos, rebaixos), são incorporadas a
    ela. Retorna (placas, face_region, quantas foram incorporadas)."""
    probe = dataclasses.replace(cfg, plate_min_area=min(cfg.plate_min_area, 20.0),
                                plate_min_width=min(cfg.plate_min_width, 2.0))
    allp, region = find_plates(mesh, probe)
    big = [P for P in allp if _big(P, cfg)]
    small = [P for P in allp if not _big(P, cfg)]
    region = region.copy()
    absorbed = 0
    gap = 0.5            # a face incorporada precisa encostar na placa (projeção)
    for S in sorted(small, key=lambda p: -p.base.area):
        best = None
        for B in big:
            if float(B.normal @ S.normal) < 0.999:
                continue
            depth = abs(float((S.origin - B.origin) @ B.normal))
            if depth > cfg.absorb_depth + 1e-6:
                continue
            proj = _project(S, B)
            if proj.is_empty or B.base.distance(proj) > gap:
                continue
            if best is None or depth < best[0]:
                best = (depth, B, proj)
        if best is None:
            region[S.faces] = -1
            continue
        _, B, proj = best
        merged = unary_union([B.base, proj])
        B.base = make_valid(merged.buffer(0.05, join_style="mitre").buffer(-0.05,
                                                                           join_style="mitre"))
        region[S.faces] = B.id
        absorbed += 1

    # paredes laterais de degraus/rebaixos achatados: perpendiculares a uma placa,
    # inteiramente dentro da faixa achatada (até absorb_depth atrás da face) e
    # encostadas nela -> somem junto
    dropped = set()
    if absorbed:
        for W in big:
            for B in big:
                if W is B or abs(float(W.normal @ B.normal)) > 0.1:
                    continue
                verts = mesh.vertices[np.unique(mesh.faces[W.faces])] if len(W.faces) else None
                if verts is None:
                    continue
                depth = (B.origin - verts) @ B.normal          # >0 = para dentro de B
                if depth.min() < -0.5 or depth.max() > cfg.absorb_depth + 0.5:
                    continue
                pts = to_local(B.M, verts)[:, :2]
                from shapely.geometry import MultiPoint
                if B.base.distance(MultiPoint([tuple(p) for p in pts])) > 0.5:
                    continue
                dropped.add(W.id)
                region[W.faces] = -1
                break
    big = [P for P in big if P.id not in dropped]

    # renumera: P1 = maior placa
    big.sort(key=lambda p: -p.base.area)
    remap = {P.id: i for i, P in enumerate(big)}
    new_region = np.full_like(region, -1)
    for old, new in remap.items():
        new_region[region == old] = new
    for P in big:
        P.id = remap[P.id]
    return big, new_region, absorbed + len(dropped)
