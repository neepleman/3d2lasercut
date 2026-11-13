"""Modo placas: faces planas do modelo viram placas unidas por finger joints.

Convenções
----------
- Cada placa fica **para dentro** do modelo: a face externa coincide com a
  superfície original e a placa ocupa a espessura t para dentro. Assim, as
  medidas externas da peça montada são as do modelo.
- Referencial local de cada placa: (u, v, w), com w = 0 na face externa e
  w = t na face interna (w aponta para dentro). O desenho 2D (u, v) é visto
  do lado de dentro, então a peça é cortada com a **face interna para cima**:
  as etiquetas gravadas ficam escondidas depois de montar.
- Em placas verticais, v aponta para cima (+Z), e as etiquetas ficam em pé.

Juntas (arestas compartilhadas entre duas placas)
-------------------------------------------------
- Ângulo reto convexo (quina de caixa): finger joint. Os dedos se alternam
  ao longo da aresta. A placa de menor número fica com os dedos das pontas,
  o que resolve os cantos onde 3 placas se encontram.
- Ângulo reto côncavo (canto interno): finger joint, com os dedos
  avançando t além da aresta para preencher o canto.
- Outros ângulos convexos: junta de topo. A placa de maior número recua o
  necessário para não invadir a outra; em cantos agudos, a de menor número
  também recua para não sair pela outra face. Sobra uma fresta em V para
  cola/massa.
- Outros ângulos côncavos: não tratados (aviso).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely import make_valid
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel, unary_union

from .config import SliceConfig
from .slicer import polygons

RIGHT_ANGLE_TOL = math.sin(math.radians(3))
EPS = 0.01


# ------------------------------------------------------------------ referenciais

def plane_frame(origin: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Matriz 4x4 mundo -> local (u, v, w), com w para dentro (= -normal)."""
    n = normal / np.linalg.norm(normal)
    n_in = -n
    up = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e2 = up - np.dot(up, n) * n
    e2 /= np.linalg.norm(e2)
    e1 = np.cross(e2, n_in)
    M = np.eye(4)
    M[:3, :3] = np.vstack([e1, e2, n_in])
    M[:3, 3] = -M[:3, :3] @ origin
    return M


def to_local(M: np.ndarray, pts) -> np.ndarray:
    pts = np.atleast_2d(np.asarray(pts, dtype=float))
    return pts @ M[:3, :3].T + M[:3, 3]


def dir_to_local(M: np.ndarray, d) -> np.ndarray:
    return M[:3, :3] @ np.asarray(d, dtype=float)


# ------------------------------------------------------------------ estruturas

@dataclass
class Plate:
    id: int
    normal: np.ndarray          # normal externa (unitária)
    origin: np.ndarray
    M: np.ndarray               # mundo -> local
    base: BaseGeometry          # face externa no plano (u, v)
    faces: np.ndarray
    shape: BaseGeometry | None = None   # após juntas
    holes: list = field(default_factory=list)  # furos para abas de nervuras (2D)

    @property
    def name(self) -> str:
        return f"P{self.id + 1}"

    @property
    def area(self) -> float:
        return float(self.base.area)


@dataclass
class Joint:
    a: int
    b: int
    p0: np.ndarray
    p1: np.ndarray
    kind: str = ""              # finger_convex | finger_concave | butt_convex | open_concave
    fingers: int = 0

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.p1 - self.p0))


# ------------------------------------------------------------------ detecção

def _inradius(poly: Polygon) -> float:
    try:
        return float(poly.exterior.distance(polylabel(poly, tolerance=0.1)))
    except Exception:
        return 0.0


def find_plates(mesh: trimesh.Trimesh, cfg: SliceConfig) -> tuple[list[Plate], np.ndarray]:
    """Agrupa faces coplanares e adjacentes em placas grandes o suficiente."""
    groups = [np.asarray(f) for f in mesh.facets]
    used = np.zeros(len(mesh.faces), dtype=bool)
    for g in groups:
        used[g] = True
    for i in np.nonzero(~used)[0]:  # triângulos isolados grandes também são planos
        if mesh.area_faces[i] >= cfg.plate_min_area:
            groups.append(np.array([i]))

    candidates = []
    for g in groups:
        area = float(mesh.area_faces[g].sum())
        if area < cfg.plate_min_area:
            continue
        n = (mesh.face_normals[g] * mesh.area_faces[g][:, None]).sum(axis=0)
        n /= np.linalg.norm(n)
        origin = mesh.triangles[g[0]][0]
        M = plane_frame(origin, n)
        tris = [Polygon(to_local(M, tri)[:, :2]) for tri in mesh.triangles[g]]
        base = make_valid(unary_union([t for t in tris if t.area > 1e-9]))
        # solda frestas numéricas entre triângulos
        base = make_valid(base.buffer(1e-4, join_style="mitre").buffer(-1e-4, join_style="mitre"))
        polys = polygons(base)
        if not polys or max(_inradius(p) for p in polys) < cfg.plate_min_width / 2:
            continue
        candidates.append((area, n, origin, M, base, g))

    candidates.sort(key=lambda c: -c[0])  # P1 = maior placa
    plates = []
    face_region = -np.ones(len(mesh.faces), dtype=int)
    for i, (area, n, origin, M, base, g) in enumerate(candidates):
        plates.append(Plate(id=i, normal=n, origin=origin, M=M, base=base, faces=g))
        face_region[g] = i
    return plates, face_region


def find_joints(mesh: trimesh.Trimesh, plates: list[Plate],
                face_region: np.ndarray) -> list[Joint]:
    """Arestas compartilhadas por duas placas, fundidas em segmentos contínuos."""
    pairs: dict[tuple[int, int], list] = {}
    for (f1, f2), (v1, v2) in zip(mesh.face_adjacency, mesh.face_adjacency_edges):
        r1, r2 = face_region[f1], face_region[f2]
        if r1 < 0 or r2 < 0 or r1 == r2:
            continue
        key = (min(r1, r2), max(r1, r2))
        pairs.setdefault(key, []).append((mesh.vertices[v1], mesh.vertices[v2]))

    joints = []
    for (a, b), edges in sorted(pairs.items()):
        d = np.cross(plates[a].normal, plates[b].normal)
        if np.linalg.norm(d) < 1e-6:
            continue  # coplanares (não deveria ocorrer)
        d /= np.linalg.norm(d)
        spans = []
        for p, q in edges:
            tp, tq = float(p @ d), float(q @ d)
            spans.append((tp, p, tq, q) if tp <= tq else (tq, q, tp, p))
        spans.sort(key=lambda s: s[0])
        cur = list(spans[0])
        for s in spans[1:]:
            if s[0] <= cur[2] + 1e-6:
                if s[2] > cur[2]:
                    cur[2], cur[3] = s[2], s[3]
            else:
                joints.append(Joint(a, b, np.array(cur[1]), np.array(cur[3])))
                cur = list(s)
        joints.append(Joint(a, b, np.array(cur[1]), np.array(cur[3])))
    return [j for j in joints if j.length > 0.5]


# ------------------------------------------------------------------ juntas

def _into(plate: Plate, p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
    """Direção 3D, no plano da placa, que sai da aresta para dentro da placa."""
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    cand = np.cross(plate.normal, d)
    mid = (p0 + p1) / 2
    best, best_dist = cand, np.inf
    for sgn in (1.0, -1.0):
        pt = to_local(plate.M, mid + sgn * cand * 0.5)[0][:2]
        dist = plate.base.distance(Point(pt))
        if dist < best_dist:
            best, best_dist = sgn * cand, dist
    return best


def _rect(s0, e, m, a0, a1, d0, d1) -> Polygon:
    """Retângulo no plano da placa: [a0, a1] ao longo da aresta, [d0, d1] para dentro."""
    return Polygon([s0 + e * a0 + m * d0, s0 + e * a1 + m * d0,
                    s0 + e * a1 + m * d1, s0 + e * a0 + m * d1])


def _end_trim(plates: list[Plate], j: Joint, end: np.ndarray, t: float) -> float:
    """Quanto encurtar, nesta ponta, os dedos que avançam além de um canto interno.

    Se uma terceira placa encosta na ponta da aresta (ex.: parede lateral), os
    primeiros t mm ao longo da aresta já são dela.
    """
    d = (j.p1 - j.p0) / np.linalg.norm(j.p1 - j.p0)
    trim = 0.0
    for Q in plates:
        if Q.id in (j.a, j.b):
            continue
        dn = abs(float(d @ Q.normal))
        if dn < 0.2 or abs(float((end - Q.origin) @ Q.normal)) > 1e-3:
            continue
        if Q.base.distance(Point(to_local(Q.M, end)[0][:2])) < 1e-3:
            trim = max(trim, t / dn)
    return trim


def finger_count(length: float, cfg: SliceConfig) -> int:
    n = int(length // cfg.finger_width_eff)
    n = max(1, n)
    return n if n % 2 == 1 else n - 1


def apply_joints(plates: list[Plate], joints: list[Joint],
                 cfg: SliceConfig) -> list[str]:
    t, cl = cfg.thickness, cfg.slot_clearance
    warnings: list[str] = []
    adds: dict[int, list] = {p.id: [] for p in plates}
    cuts: dict[int, list] = {p.id: [] for p in plates}

    for j in joints:
        A, B = plates[j.a], plates[j.b]
        uA, uB = _into(A, j.p0, j.p1), _into(B, j.p0, j.p1)
        c = float(A.normal @ B.normal)
        convex = float(uA @ B.normal) < 0
        right = abs(c) < RIGHT_ANGLE_TOL

        if right:
            j.kind = "finger_convex" if convex else "finger_concave"
            j.fingers = finger_count(j.length, cfg)
        elif convex:
            j.kind = "butt_convex"
        else:
            j.kind = "open_concave"
            warnings.append(f"Junta {A.name}/{B.name}: canto interno fora de 90° — as "
                            "placas não se tocam ali; use cola/massa ou ajuste o modelo.")
            continue

        for P, uP, other, uO in ((A, uA, B, uB), (B, uB, A, uA)):
            s = to_local(P.M, [j.p0, j.p1])[:, :2]
            s0, L = s[0], float(np.linalg.norm(s[1] - s[0]))
            e = (s[1] - s[0]) / L
            m = dir_to_local(P.M, uP)[:2]
            m /= np.linalg.norm(m)
            lower = P.id == min(j.a, j.b)

            if j.kind == "butt_convex":
                if lower:
                    # canto agudo: a placa que vai até a aresta teria o canto interno
                    # saindo pela outra face; recua t·cot(φ) (sobra uma fresta em V)
                    cos_phi = float(uP @ uO)
                    if cos_phi > 1e-6:
                        cut = t * cos_phi / math.sqrt(max(1e-9, 1 - cos_phi ** 2))
                        cuts[P.id].append(_rect(s0, e, m, -EPS, L + EPS, -EPS, cut))
                    continue
                a = -float(uP @ other.normal)
                cc = float(P.normal @ other.normal)
                setback = t * max(1.0, 1.0 - cc) / max(a, 1e-3)
                cuts[P.id].append(_rect(s0, e, m, -EPS, L + EPS, -EPS, setback))
                continue

            n = j.fingers
            bounds = [L * i / n for i in range(n + 1)]
            if j.kind == "finger_concave":
                # pontas encostadas em terceiras placas (na ordem p0 -> p1 desta placa)
                bounds[0] += _end_trim(plates, j, j.p0, t)
                bounds[-1] -= _end_trim(plates, j, j.p1, t)
            for i in range(n):
                owned = lower == (i % 2 == 0)
                first, last = i == 0, i == n - 1
                if j.kind == "finger_convex" and not owned:
                    lo = -EPS if first else bounds[i] - cl / 2
                    hi = L + EPS if last else bounds[i + 1] + cl / 2
                    cuts[P.id].append(_rect(s0, e, m, lo, hi, -EPS, t))
                elif j.kind == "finger_concave" and owned:
                    lo = bounds[i] if first else bounds[i] + cl / 2
                    hi = bounds[i + 1] if last else bounds[i + 1] - cl / 2
                    adds[P.id].append(_rect(s0, e, m, lo, hi, -t, EPS))

    for P in plates:
        shape = P.base
        if adds[P.id]:
            shape = unary_union([shape, *adds[P.id]])
        if cuts[P.id]:
            shape = shape.difference(unary_union(cuts[P.id]))
        P.shape = make_valid(shape)
    return warnings


# ------------------------------------------------------------------ 3D

def plate_solid(P: Plate, cfg: SliceConfig, shape: BaseGeometry | None = None):
    """Sólido 3D da placa (para seções e prévia)."""
    shape = P.shape if shape is None else shape
    meshes = []
    for poly in polygons(shape):
        m = trimesh.creation.extrude_polygon(poly, cfg.thickness)  # w de 0 a t
        m.apply_transform(np.linalg.inv(P.M))
        meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None


def coverage(mesh: trimesh.Trimesh, plates: list[Plate]) -> float:
    return float(sum(p.area for p in plates) / mesh.area) if mesh.area else 0.0
