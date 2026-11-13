"""Nervuras internas: a estrutura que fica escondida atrás das placas.

São fatias verticais em X e Y (como no modo grade), mas recortadas para não
invadir as placas, que ficam na superfície. Elas se prendem às placas por
abas que atravessam furos retangulares (tab-and-slot), e se cruzam entre si
com as mesmas fendas de meia altura da grade.

Onde o modelo não tem face plana (ex.: a abóbada do arco), a nervura vai até
a superfície original, e a borda dela forma o contorno visível.
"""
from __future__ import annotations

import numpy as np
import trimesh
from shapely import make_valid
from shapely.geometry import Polygon, box
from shapely.affinity import rotate as srotate
from shapely.affinity import translate
from shapely.ops import unary_union

from .config import SliceConfig
from .plates import Plate, plate_solid, to_local
from .slicer import polygons, section_shapes
from .waffle import T_X, T_Y, WaffleResult, assemble_waffle, grid_positions, make_slice

RIB_NORMALS = {"X": np.array([1.0, 0.0, 0.0]), "Y": np.array([0.0, 1.0, 0.0])}


def auto_spacing(mesh: trimesh.Trimesh) -> float:
    return float(max(40.0, max(mesh.extents[:2]) / 4))


def want_ribs(mesh: trimesh.Trimesh, cov: float, cfg: SliceConfig) -> tuple[bool, str]:
    if cfg.ribs == "none":
        return False, "desligadas pelo usuário"
    if cfg.ribs == "on":
        return True, "ligadas pelo usuário"
    if cov < 0.98:
        return True, (f"{100 * (1 - cov):.0f}% da superfície não é plana; as nervuras "
                      "dão estrutura e formam o contorno dessas regiões")
    if max(mesh.extents) > 100:
        return True, "peça grande: as nervuras evitam que as placas empenem ou cedam"
    return False, "peça pequena e toda plana: as placas bastam"


def _transform(meshes, T):
    out = []
    for m in meshes:
        c = m.copy()
        c.apply_transform(T)
        out.append(c)
    return out


def _strip_axis(strip: Polygon):
    """Eixo longo (origem, direção unitária, comprimento) do retângulo mínimo."""
    rect = strip.minimum_rotated_rectangle
    pts = np.asarray(rect.exterior.coords)[:4]
    e01, e12 = pts[1] - pts[0], pts[2] - pts[1]
    if np.linalg.norm(e01) >= np.linalg.norm(e12):
        a, L = e01, np.linalg.norm(e01)
        origin = (pts[0] + pts[3]) / 2
    else:
        a, L = e12, np.linalg.norm(e12)
        origin = (pts[1] + pts[0]) / 2
    return origin, a / max(L, 1e-9), float(L)


def build_ribs(mesh: trimesh.Trimesh, plates: list[Plate], cfg: SliceConfig,
               warnings: list[str], extra_solids=()) -> tuple[WaffleResult, float, int]:
    t, cl = cfg.thickness, cfg.slot_clearance
    spacing = cfg.rib_spacing if cfg.rib_spacing > 0 else auto_spacing(mesh)
    (x0, y0, _), (x1, y1, z1) = mesh.bounds
    xs = grid_positions(x0, x1, spacing)
    ys = grid_positions(y0, y1, spacing)
    tab_len = cfg.tab_length_eff
    end_margin = t + 1.0

    solids = {P.id: plate_solid(P, cfg) for P in plates}
    solids = {k: v for k, v in solids.items() if v is not None}
    n_tabs = 0
    fams = {}
    for fam, T, positions, cross in (("X", T_X, xs, ys), ("Y", T_Y, ys, xs)):
        heights = positions if fam == "X" else [-p for p in positions]
        raw = section_shapes(_transform([mesh], T)[0], heights)
        all_slabs = trimesh.util.concatenate(
            _transform(list(solids.values()) + [m for m in extra_solids if m is not None], T))
        # a nervura tem espessura t: a marca das placas é tirada nas duas faces e no
        # meio dela (em superfícies inclinadas/curvas a marca muda ao longo de t)
        tr_mid = section_shapes(all_slabs, heights)
        tr_lo = section_shapes(all_slabs, [h - t / 2 for h in heights])
        tr_hi = section_shapes(all_slabs, [h + t / 2 for h in heights])
        traces = [unary_union([a, b, c]) for a, b, c in zip(tr_mid, tr_lo, tr_hi)]
        perp = [P for P in plates
                if P.id in solids and abs(float(P.normal @ RIB_NORMALS[fam])) < 0.05
                and P.name not in cfg.no_tab_plates]
        # aba só onde a placa cobre a nervura na espessura toda (meio e duas faces)
        perp_traces = {}
        for P in perp:
            sol = _transform([solids[P.id]], T)[0]
            mids = section_shapes(sol, heights)
            los = section_shapes(sol, [h - t / 2 for h in heights])
            his = section_shapes(sol, [h + t / 2 for h in heights])
            perp_traces[P.id] = [a.intersection(b).intersection(c)
                                 for a, b, c in zip(mids, los, his)]
        Tinv = np.linalg.inv(T)

        slices = []
        for i, (pos, h) in enumerate(zip(positions, heights)):
            prof = raw[i]
            if not traces[i].is_empty:
                prof = prof.difference(traces[i].buffer(cl, join_style="mitre"))
            prof = make_valid(prof)
            tabs = []
            for P in perp:
                tr = perp_traces[P.id][i]
                if tr.is_empty or prof.is_empty:
                    continue
                near = prof.buffer(cl + 0.2, join_style="mitre")
                for strip in polygons(tr.intersection(near)):
                    if strip.area < 1e-3:
                        continue
                    origin, e, L = _strip_axis(strip)
                    if L < tab_len + 2 * end_margin:
                        continue
                    n = max(1, int(round(L / cfg.tab_pitch)))
                    lo, hi = end_margin + tab_len / 2, L - end_margin - tab_len / 2
                    centers = [L / 2] if n == 1 else list(np.linspace(lo, hi, n))
                    band = tr.buffer(cl + 0.05, join_style="mitre").intersection(raw[i])
                    for cpos in centers:
                        c = origin + e * cpos
                        # longe dos cruzamentos com as nervuras da outra família
                        if any(abs(c[0] - b) < cfg.slot_width / 2 + tab_len / 2 + 1 for b in cross):
                            continue
                        win = box(-tab_len / 2, -1e4, tab_len / 2, 1e4)
                        ang = float(np.degrees(np.arctan2(e[1], e[0])))
                        win = srotate(win, ang, origin=(0, 0))
                        win = translate(win, c[0], c[1])
                        tab = [p for p in polygons(band.intersection(win))
                               if p.distance(strip) < 0.05 and p.area > 1e-3]
                        if not tab:
                            continue
                        tab_poly = unary_union(tab)
                        # furo correspondente na placa
                        pts2 = np.asarray([pt for p in polygons(tab_poly)
                                           for pt in p.exterior.coords])
                        pts3 = np.column_stack([pts2, np.full(len(pts2), h), np.ones(len(pts2))])
                        world = (pts3 @ Tinv.T)[:, :3]
                        loc = to_local(P.M, world)[:, :2]
                        d = loc[:, None, :] - loc[None, :, :]
                        k1, k2 = np.unravel_index(np.argmax((d ** 2).sum(-1)), d.shape[:2])
                        p, q = loc[k1], loc[k2]
                        ln = np.linalg.norm(q - p)
                        if ln < 1e-6:
                            continue
                        u = (q - p) / ln
                        w = np.array([-u[1], u[0]])
                        hw, ext = (t + cl) / 2, cl / 2
                        P.holes.append(Polygon([p - u * ext - w * hw, q + u * ext - w * hw,
                                                q + u * ext + w * hw, p - u * ext + w * hw]))
                        tabs.append(tab_poly)
                        n_tabs += 1
            if tabs:
                prof = unary_union([prof, *tabs])
            slices.append(make_slice(fam, i, pos, prof, cfg))
        fams[fam] = slices

    result = assemble_waffle(fams["X"], fams["Y"], z1, cfg, warnings)
    return result, spacing, n_tabs
