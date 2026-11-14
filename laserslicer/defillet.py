"""Remoção de arredondamentos (e chanfros) entre placas.

Peças feitas para impressão 3D costumam ter arestas arredondadas. No corte a
laser isso atrapalha: as paredes não chegam a se tocar (há uma faixa curva
entre elas), então não há aresta comum e nem finger joint. Aqui
"desarredondamos": as placas vizinhas são estendidas até a aresta viva que
existiria sem o arredondamento, e essa aresta vira uma junta normal.

Como funciona (sobre a malha, então serve para STL e para sólidos do FreeCAD)
-----------------------------------------------------------------------------
1. Cada face que não pertence a placa é atribuída a um par de placas (A, B)
   quando: a normal dela está "entre" as normais de A e B (combinação com
   coeficientes >= 0 e sem componente na direção da aresta) e ela está perto
   das duas placas. Arredondamentos e chanfros satisfazem isso; as "bolas"
   dos cantos (onde 3 arredondamentos se encontram) não, e só são cobertas.
2. A aresta viva é a reta de interseção dos planos de A e B, no trecho do
   arredondamento, estendida até onde encontra o plano de uma terceira placa
   (o canto vivo).
3. A e B são estendidas até essa reta (envoltória da projeção do
   arredondamento + trecho da reta). Placas nas pontas da aresta (normal ao
   longo da aresta) têm o canto do contorno preenchido (canto externo) ou
   recortado (canto interno).
4. Para cada par é criada uma Junta, tratada depois como qualquer outra.

Arredondamentos maiores que `max_fillet_radius` não são tocados: continuam
sendo chapas flexíveis ou contorno das nervuras.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
import trimesh
from shapely import make_valid
from shapely.geometry import MultiPoint, Point
from shapely.ops import unary_union

from .config import SliceConfig
from .plates import Joint, Plate, to_local

COVERED = -2          # marca em face_region: face absorvida pelo desarredondamento
NORMAL_TOL = 0.06     # |n . u| máximo (u = direção da aresta)
COEF_TOL = -0.06      # coeficientes mínimos da combinação de normais


@dataclass
class SharpEdge:
    a: int
    b: int
    p0: np.ndarray
    p1: np.ndarray
    radius: float       # raio aproximado do arredondamento removido
    faces: np.ndarray


def _plane_line(A: Plate, B: Plate):
    u = np.cross(A.normal, B.normal)
    nu = np.linalg.norm(u)
    if nu < 0.2:            # quase paralelas (< ~11°): não há canto a fazer
        return None
    u = u / nu
    # ponto na reta: resolve nA.x = dA, nB.x = dB, u.x = 0
    M = np.vstack([A.normal, B.normal, u])
    rhs = np.array([A.normal @ A.origin, B.normal @ B.origin, 0.0])
    return np.linalg.solve(M, rhs), u


def _dist_to_plate(P: Plate, pts: np.ndarray) -> np.ndarray:
    loc = to_local(P.M, pts)
    d2 = shapely.distance(P.base, shapely.points(loc[:, 0], loc[:, 1]))
    return np.sqrt(d2 ** 2 + loc[:, 2] ** 2)


def _extend(plates, pair, p_line, u, t_end, direction, rmax):
    """Ponta da aresta -> canto vivo: interseção com o plano de uma terceira placa
    próxima, logo além da ponta. Retorna (folga, t, placa) ou None."""
    best = None
    for Q in plates:
        if Q.id in pair:
            continue
        den = float(u @ Q.normal)
        if abs(den) < 0.1:
            continue
        tq = float((Q.normal @ Q.origin - Q.normal @ p_line) / den)
        gap = (tq - t_end) * direction
        if not (-1e-6 <= gap <= 2.5 * rmax):
            continue
        q = p_line + u * tq
        if _dist_to_plate(Q, q[None, :])[0] > 2.5 * rmax:
            continue
        if best is None or gap < best[0]:
            best = (gap, tq, Q)
    return best


def extend_joints(joints: list[Joint], plates: list[Plate], rmax: float) -> int:
    """Leva até o canto vivo as juntas de arestas vivas que terminavam num
    arredondamento (ex.: aresta de cima de uma caixa com cantos verticais
    arredondados). Chamar depois de `sharpen`. Retorna quantas pontas mudaram."""
    changed = 0
    for j in joints:
        d = j.p1 - j.p0
        L = float(np.linalg.norm(d))
        if L < 1e-9:
            continue
        u = d / L
        for end, direction in ((0, -1.0), (1, 1.0)):
            p = j.p0 if end == 0 else j.p1
            e = _extend(plates, (j.a, j.b), p, u, 0.0, direction, rmax)
            if e is not None and e[0] > 1e-6:
                q = p + u * e[1]
                if end == 0:
                    j.p0 = q
                else:
                    j.p1 = q
                changed += 1
    return changed


def sharpen(mesh: trimesh.Trimesh, plates: list[Plate], face_region: np.ndarray,
            cfg: SliceConfig) -> tuple[list[Joint], list[SharpEdge], list[str]]:
    """Estende as placas sobre arredondamentos/chanfros e cria as juntas.

    Altera `plate.base` e marca as faces absorvidas com COVERED em
    `face_region`. Retorna as juntas novas, as arestas vivas e avisos.
    """
    rmax = cfg.max_fillet_radius
    if rmax <= 0 or len(plates) < 2:
        return [], [], []
    free = np.nonzero(face_region == -1)[0]
    if len(free) == 0:
        return [], [], []

    N = mesh.face_normals[free]
    C = mesh.triangles_center[free]
    reach = 1.6 * rmax

    # distâncias de cada face livre a cada placa (só placas próximas importam)
    dists = np.full((len(plates), len(free)), np.inf)
    for P in plates:
        dists[P.id] = _dist_to_plate(P, C)

    best_pair = -np.ones(len(free), dtype=int)
    best_score = np.full(len(free), np.inf)
    pairs = []
    for i, A in enumerate(plates):
        near_a = dists[i] <= reach
        if not near_a.any():
            continue
        for k in range(i + 1, len(plates)):
            B = plates[k]
            cand = near_a & (dists[k] <= reach)
            if not cand.any():
                continue
            line = _plane_line(A, B)
            if line is None:
                continue
            p_line, u = line
            c = float(A.normal @ B.normal)
            n = N[cand]
            na, nb = n @ A.normal, n @ B.normal
            alpha = (na - c * nb) / (1 - c * c)
            beta = (nb - c * na) / (1 - c * c)
            ok = (np.abs(n @ u) < NORMAL_TOL) & (alpha > COEF_TOL) & (beta > COEF_TOL)
            # e perto da reta da aresta viva
            rel = C[cand] - p_line
            dline = np.linalg.norm(rel - np.outer(rel @ u, u), axis=1)
            ok &= dline <= reach
            idx = np.nonzero(cand)[0][ok]
            if len(idx) == 0:
                continue
            pid = len(pairs)
            pairs.append((i, k, p_line, u))
            score = dists[i, idx] + dists[k, idx]
            better = score < best_score[idx]
            best_pair[idx[better]] = pid
            best_score[idx[better]] = score[better]

    edges: list[SharpEdge] = []
    joints: list[Joint] = []
    adds: dict[int, list] = {P.id: [] for P in plates}
    subs: dict[int, list] = {P.id: [] for P in plates}
    warnings: list[str] = []
    corner_pts: list[np.ndarray] = []

    adj = mesh.face_adjacency
    for pid, (i, k, p_line, u) in enumerate(pairs):
        sel = np.nonzero(best_pair == pid)[0]
        if len(sel) == 0:
            continue
        cand_faces = free[sel]
        # só valem trechos que encostam nas DUAS placas: um pedaço de uma curva
        # maior (raio > máximo) encosta só numa delas e fica de fora
        in_sel = np.zeros(len(mesh.faces), dtype=bool)
        in_sel[cand_faces] = True
        inner = adj[in_sel[adj[:, 0]] & in_sel[adj[:, 1]]]
        keep = []
        for comp in trimesh.graph.connected_components(inner, nodes=cand_faces, min_len=1):
            comp = np.asarray(comp)
            mark = np.zeros(len(mesh.faces), dtype=bool)
            mark[comp] = True
            border = np.concatenate([adj[mark[adj[:, 0]], 1], adj[mark[adj[:, 1]], 0]])
            regions = set(face_region[border].tolist())
            if i in regions and k in regions:
                keep.append(comp)
        if not keep:
            continue
        faces = np.concatenate(keep)
        verts = mesh.vertices[np.unique(mesh.faces[faces])]
        rel = verts - p_line
        dline = np.linalg.norm(rel - np.outer(rel @ u, u), axis=1)
        radius = float(dline.max())
        # raio aproximado: para 90°, a distância máxima à aresta viva é ~ r
        if radius > rmax * 1.05:
            continue
        A, B = plates[i], plates[k]
        tv = rel @ u
        t0, t1 = float(tv.min()), float(tv.max())

        e0 = _extend(plates, (i, k), p_line, u, t0, -1.0, rmax)
        e1 = _extend(plates, (i, k), p_line, u, t1, 1.0, rmax)
        if e0 is not None:
            t0 = e0[1]
        if e1 is not None:
            t1 = e1[1]
        p0, p1 = p_line + u * t0, p_line + u * t1
        if np.linalg.norm(p1 - p0) < 1.0:
            continue
        edges.append(SharpEdge(i, k, p0, p1, radius, faces))
        joints.append(Joint(i, k, p0, p1))

        # placas do par: estende até a reta (envoltória da projeção + trecho da reta)
        for P in (A, B):
            pts = to_local(P.M, np.vstack([verts, p0, p1]))[:, :2]
            adds[P.id].append(MultiPoint([tuple(p) for p in pts]).convex_hull)
        # placas nas pontas: preenche (canto externo) ou recorta (canto interno)
        for end in (e0, e1):
            if end is None:
                continue
            _, tq, Q = end
            if abs(float(u @ Q.normal)) < 0.9:
                continue
            corner = p_line + u * tq
            corner_pts.append(corner)
            pts = to_local(Q.M, np.vstack([verts, corner]))[:, :2]
            region = MultiPoint([tuple(p) for p in pts]).convex_hull
            c2 = to_local(Q.M, corner[None, :])[0][:2]
            if Q.base.buffer(-1e-3).contains(Point(c2)):
                subs[Q.id].append(region)       # canto interno: tira o "recheio"
            else:
                adds[Q.id].append(region)       # canto externo: completa a quina

    if not edges:
        return [], [], []

    for P in plates:
        shape = P.base
        if adds[P.id]:
            shape = unary_union([shape, *adds[P.id]])
        if subs[P.id]:
            shape = shape.difference(unary_union(subs[P.id]))
        # solda frestas numéricas
        P.base = make_valid(shape.buffer(1e-3, join_style="mitre").buffer(-1e-3,
                                                                         join_style="mitre"))

    # faces absorvidas: as dos arredondamentos e as "bolas" dos cantos
    for e in edges:
        face_region[e.faces] = COVERED
    if corner_pts:
        rest = np.nonzero(face_region == -1)[0]
        if len(rest):
            cp = np.asarray(corner_pts)
            d = np.min(np.linalg.norm(mesh.triangles_center[rest][:, None, :] - cp[None],
                                      axis=2), axis=1)
            face_region[rest[d <= 2 * rmax]] = COVERED

    radii = sorted(e.radius for e in edges)
    warnings.append(f"{len(edges)} arredondamentos/chanfros removidos (raio até "
                    f"~{radii[-1]:.1f} mm): as placas foram estendidas até a aresta viva.")
    return joints, edges, warnings
