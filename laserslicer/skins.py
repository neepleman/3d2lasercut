"""Superfícies curvas de curvatura simples -> chapas flexíveis (kerf bending).

Regiões do modelo que não são planas, mas cujas normais são todas
perpendiculares a um mesmo eixo d (abóbadas, cilindros, cantos arredondados),
podem ser cobertas por uma chapa plana com um padrão de cortes ("living
hinge") que a deixa flexível para dobrar na curva.

Geometria
---------
- A seção transversal da região (plano perpendicular a d) é uma curva C.
  A chapa planificada tem largura = extensão em d e comprimento = comprimento
  de arco de C, medido no lado côncavo da chapa (o lado que não estica).
- Como as placas, a chapa fica para dentro: a face externa coincide com a
  superfície do modelo.
- Nas pontas em d, se houver placas perpendiculares a d (ex.: frente e fundo
  do arco), a chapa recua t + folga e ganha abas que atravessam furos curvos
  nessas placas.
- Nas pontas do arco, a chapa é recortada onde encontraria outras placas.

Ainda não tratado: regiões de dupla curvatura (esferas, etc.), que continuam
com o contorno dado pelas nervuras.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely import make_valid
from shapely.affinity import scale as sscale
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import substring, unary_union

from .config import SliceConfig
from .plates import Plate, plate_solid, to_local
from .slicer import polygons

AXIS_TOL = math.sin(math.radians(3))


@dataclass
class Skin:
    id: int
    d: np.ndarray
    b1: np.ndarray
    b2: np.ndarray
    outer: LineString           # curva da face externa, coords (b1, b2)
    inner_left: bool            # o lado de dentro fica à esquerda da curva?
    k: float                    # escala do comprimento de arco (lado côncavo)
    x_lo: float                 # trecho de arco usado (coords da curva externa)
    x_hi: float
    s_lo: float                 # trecho em d
    s_hi: float
    shape: Polygon | None = None
    cut_lines: MultiLineString | None = None
    min_radius: float = math.inf
    end_plates: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"C{self.id + 1}"

    def band(self, x0: float | None = None, x1: float | None = None, t: float = 3.0):
        """Seção transversal da chapa (faixa de espessura t) no trecho [x0, x1]."""
        x0 = self.x_lo if x0 is None else x0
        x1 = self.x_hi if x1 is None else x1
        sub = substring(self.outer, x0, x1)
        return sub.buffer(t if self.inner_left else -t, single_sided=True,
                          cap_style="flat", join_style="round")

    def solid(self, t: float) -> trimesh.Trimesh | None:
        band = self.band(t=t)
        meshes = []
        for poly in polygons(band):
            m = trimesh.creation.extrude_polygon(poly, self.s_hi - self.s_lo)
            T = np.eye(4)
            T[:3, 0], T[:3, 1], T[:3, 2] = self.b1, self.b2, self.d
            T[:3, 3] = self.d * self.s_lo
            m.apply_transform(T)
            meshes.append(m)
        return trimesh.util.concatenate(meshes) if meshes else None

    def to_world(self, pts2: np.ndarray, s: float) -> np.ndarray:
        pts2 = np.asarray(pts2)
        return pts2[:, :1] * self.b1 + pts2[:, 1:2] * self.b2 + s * self.d


def _chain(segments: np.ndarray) -> np.ndarray | None:
    """Encadeia segmentos (n, 2, 3) numa polilinha ordenada (a maior componente)."""
    if len(segments) == 0:
        return None
    keys: dict[tuple, int] = {}
    pts: list[np.ndarray] = []

    def key(p):
        k = tuple(np.round(p, 5))
        if k not in keys:
            keys[k] = len(pts)
            pts.append(p)
        return keys[k]

    nbrs: dict[int, list[int]] = {}
    for a, b in segments:
        ia, ib = key(a), key(b)
        if ia == ib:
            continue
        nbrs.setdefault(ia, []).append(ib)
        nbrs.setdefault(ib, []).append(ia)
    seen: set[int] = set()
    best: list[int] = []
    for start in nbrs:
        if start in seen:
            continue
        comp, stack = [], [start]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.append(n)
            stack.extend(nbrs[n])
        ends = [n for n in comp if len(nbrs[n]) == 1]
        cur = ends[0] if ends else comp[0]
        order, prev = [cur], None
        while True:
            nxt = [m for m in nbrs[cur] if m != prev and (m not in order or
                   (not ends and m == order[0] and len(order) > 2))]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            order.append(cur)
            if cur == order[0]:
                break
        if len(order) > len(best):
            best = order
    return np.asarray([pts[i] for i in best]) if len(best) >= 2 else None


def _axis(normals: np.ndarray, weights: np.ndarray) -> np.ndarray:
    A = (normals * weights[:, None]).T @ normals
    w, v = np.linalg.eigh(A)
    return v[:, 0]


def _basis(d: np.ndarray):
    ref = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    b1 = np.cross(d, ref)
    b1 /= np.linalg.norm(b1)
    b2 = np.cross(d, b1)
    return b1, b2          # b1 x b2 = d


def _min_radius(curve: LineString) -> float:
    """Menor raio de curvatura, medido sobre a curva reamostrada a passo uniforme."""
    L = curve.length
    step = max(1.0, L / 200)
    if L < 3 * step:
        return math.inf
    pts = np.asarray([curve.interpolate(x).coords[0] for x in np.arange(0, L, step)])
    seg = np.diff(pts, axis=0)
    ang = np.arctan2(seg[:, 1], seg[:, 0])
    turn = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
    w = 3  # janela de 3 passos para suavizar facetas
    if len(turn) >= w:
        turn = np.convolve(turn, np.ones(w) / w, mode="valid")
    tmax = float(np.max(turn)) if len(turn) else 0.0
    return step / tmax if tmax > 1e-9 else math.inf


def _total_turning(curve: LineString) -> float:
    pts = np.asarray(curve.coords)
    seg = np.diff(pts, axis=0)
    ang = np.arctan2(seg[:, 1], seg[:, 0])
    return float(np.sum(np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)))


def hinge_slits(region: Polygon, x0: float, x1: float, y0: float, y1: float,
                cfg: SliceConfig) -> MultiLineString | None:
    """Padrão clássico de cortes paralelos alternados (living hinge).

    Colunas de cortes a cada `pitch` em x; cada coluna tem cortes de
    `slit` mm separados por pontes de `bridge` mm; colunas vizinhas são
    defasadas em meio período, o que deixa a chapa flexível em torno de y.
    """
    t = cfg.thickness
    pitch = cfg.hinge_pitch if cfg.hinge_pitch > 0 else max(1.5, 0.7 * t)
    bridge = max(2.5, t)
    slit = cfg.hinge_slit if cfg.hinge_slit > 0 else 20.0
    period = slit + bridge
    inner = region.buffer(-bridge / 2)
    lines = []
    x = x0 + pitch
    col = 0
    while x < x1 - pitch / 2:
        off = 0.0 if col % 2 == 0 else period / 2
        y = y0 - period + off
        while y < y1:
            a, b = max(y, y0 + bridge), min(y + slit, y1 - bridge)
            if b - a > 1.0:
                seg = LineString([(x, a), (x, b)]).intersection(inner)
                for g in getattr(seg, "geoms", [seg]):
                    if isinstance(g, LineString) and g.length > 1.0:
                        lines.append(g)
            y += period
        x += pitch
        col += 1
    return MultiLineString(lines) if lines else None


def find_skins(mesh: trimesh.Trimesh, face_region: np.ndarray, plates: list[Plate],
               cfg: SliceConfig) -> tuple[list[Skin], np.ndarray, list[str]]:
    """Encontra regiões cilíndricas e gera as chapas flexíveis.

    Retorna as chapas, a máscara das faces cobertas e avisos.
    Furos para as abas são adicionados em `plate.holes`.
    """
    t, cl = cfg.thickness, cfg.slot_clearance
    warnings: list[str] = []
    covered = np.zeros(len(mesh.faces), dtype=bool)
    free = np.nonzero(face_region < 0)[0]
    if len(free) == 0:
        return [], covered, warnings
    is_free = face_region < 0
    adj = mesh.face_adjacency
    edges = adj[is_free[adj[:, 0]] & is_free[adj[:, 1]]]
    comps = trimesh.graph.connected_components(edges, nodes=free, min_len=1)

    plate_solids = {P.id: plate_solid(P, cfg) for P in plates}
    skins: list[Skin] = []
    for comp in comps:
        comp = np.asarray(comp)
        area = float(mesh.area_faces[comp].sum())
        if area < cfg.plate_min_area:
            continue
        N, w = mesh.face_normals[comp], mesh.area_faces[comp]
        d = _axis(N, w)
        if np.max(np.abs(N @ d)) > AXIS_TOL:
            continue  # dupla curvatura: fica para as nervuras
        b1, b2 = _basis(d)
        verts = mesh.vertices[np.unique(mesh.faces[comp])]
        svals = verts @ d
        s0, s1 = float(svals.min()), float(svals.max())
        s_mid = (s0 + s1) / 2
        sub = mesh.submesh([comp], append=True)
        segs = trimesh.intersections.mesh_plane(sub, plane_normal=d, plane_origin=d * s_mid)
        pts3 = _chain(np.asarray(segs))
        if pts3 is None:
            continue
        pts2 = np.column_stack([pts3 @ b1, pts3 @ b2])
        closed = np.linalg.norm(pts2[0] - pts2[-1]) < 1e-6
        outer = LineString(pts2)
        if outer.length < 2 * t or _total_turning(outer) < math.radians(10):
            continue

        # lado de dentro e orientação (desenho visto de dentro, como as placas)
        mid3 = outer.interpolate(outer.length / 2)
        cent = mesh.triangles_center[comp]
        p_mid3 = mid3.x * b1 + mid3.y * b2 + s_mid * d
        n_out = N[np.argmin(np.linalg.norm(cent - p_mid3, axis=1))]
        n_in = -n_out
        a = outer.project(mid3)
        pa, pb = outer.interpolate(max(0, a - 0.5)), outer.interpolate(min(outer.length, a + 0.5))
        tan2 = np.array([pb.x - pa.x, pb.y - pa.y])
        tan2 /= np.linalg.norm(tan2)
        T3 = tan2[0] * b1 + tan2[1] * b2
        if float(np.cross(T3, d) @ n_in) < 0:
            outer = LineString(pts2[::-1])
            tan2 = -tan2
        n_in2 = np.array([n_in @ b1, n_in @ b2])
        inner_left = float(np.array([-tan2[1], tan2[0]]) @ n_in2) > 0
        inner = outer.offset_curve(t if inner_left else -t)
        L0 = outer.length
        k = min(1.0, inner.length / L0) if inner.length > 0 else 1.0

        skin = Skin(id=len(skins), d=d, b1=b1, b2=b2, outer=outer, inner_left=inner_left,
                    k=k, x_lo=0.0, x_hi=L0, s_lo=s0, s_hi=s1)
        skin.min_radius = _min_radius(outer)

        # --- pontas em d: placas perpendiculares ao eixo
        end_tabs: list[tuple[Plate, float, int]] = []  # (placa, s da face externa, lado)
        for Q in plates:
            if abs(float(Q.normal @ d)) < 1 - 1e-3:
                continue
            q = float(Q.origin @ d)
            for side, s_edge in ((-1, s0), (1, s1)):
                if abs(q - s_edge) > 0.5:
                    continue
                probe = skin.to_world(np.array([[mid3.x, mid3.y]]), q)
                if Q.base.distance(Point(to_local(Q.M, probe)[0][:2])) < 0.5:
                    end_tabs.append((Q, s_edge, side))
        for Q, s_edge, side in end_tabs:
            if side < 0:
                skin.s_lo = s_edge + t + cl
            else:
                skin.s_hi = s_edge - t - cl
            if Q.name not in cfg.no_tab_plates:
                skin.end_plates.append(Q.name)

        # --- pontas do arco: recorta onde encontraria outras placas
        others = [plate_solids[Q.id] for Q in plates
                  if plate_solids[Q.id] is not None and abs(float(Q.normal @ d)) < 1 - 1e-3]
        traces = []
        for sol in others:
            sec = sol.section(plane_origin=d * s_mid, plane_normal=d)
            if sec is None:
                continue
            for lp in sec.discrete:
                p2 = np.column_stack([lp @ b1, lp @ b2])
                if len(p2) >= 3:
                    traces.append(make_valid(Polygon(p2)))
        blocked = unary_union(traces).buffer(cl) if traces else None
        step = 0.25
        xs = np.arange(0, L0 + step / 2, step)
        ok = np.ones(len(xs), dtype=bool)
        if blocked is not None and not blocked.is_empty:
            for i, x in enumerate(xs):
                po = outer.interpolate(x)
                pi = inner.interpolate(inner.project(po))
                if LineString([(po.x, po.y), (pi.x, pi.y)]).intersects(blocked):
                    ok[i] = False
        runs, start = [], None
        for i, flag in enumerate(ok):
            if flag and start is None:
                start = i
            if (not flag or i == len(ok) - 1) and start is not None:
                end = i if flag else i - 1
                runs.append((xs[start], xs[end]))
                start = None
        runs = [r for r in runs if r[1] - r[0] >= max(4 * t, 10.0)]
        if not runs:
            continue
        skin.x_lo, skin.x_hi = max(runs, key=lambda r: r[1] - r[0])

        # --- planificação
        tri_polys = []
        for tri in mesh.triangles[comp]:
            q2 = np.column_stack([tri @ b1, tri @ b2])
            xx = np.array([outer.project(Point(p)) for p in q2])
            if closed and xx.max() - xx.min() > L0 / 2:
                xx = np.where(xx < L0 / 2, xx + L0, xx)
            poly = Polygon(np.column_stack([xx, tri @ d]))
            if poly.area > 1e-9:
                tri_polys.append(poly)
        flat = make_valid(unary_union(tri_polys)).buffer(1e-4, join_style="mitre")
        flat = flat.intersection(box(skin.x_lo, skin.s_lo, skin.x_hi, skin.s_hi))

        tab_len = cfg.tab_length_eff
        tabs = []
        span = skin.x_hi - skin.x_lo
        n = max(1, int(round(span / cfg.tab_pitch)))
        m = tab_len / 2 + 2.0
        if span > tab_len + 2 * m:
            centers = [skin.x_lo + span / 2] if n == 1 else \
                list(np.linspace(skin.x_lo + m, skin.x_hi - m, n))
            for Q, s_edge, side in end_tabs:
                if Q.name in cfg.no_tab_plates:
                    continue
                for c in centers:
                    a0, a1 = c - tab_len / 2, c + tab_len / 2
                    if side < 0:
                        tabs.append(box(a0, s_edge, a1, skin.s_lo + 0.01))
                    else:
                        tabs.append(box(a0, skin.s_hi - 0.01, a1, s_edge))
                    hole2 = skin.band(a0, a1, t=t)
                    for hp in polygons(hole2):
                        ring = np.asarray(hp.exterior.coords)
                        loc = to_local(Q.M, skin.to_world(ring, s_edge))[:, :2]
                        Q.holes.append(make_valid(Polygon(loc)).buffer(cl / 2, join_style="mitre"))
        shape = unary_union([flat, *tabs]) if tabs else flat
        shape = sscale(shape, xfact=k, yfact=1.0, origin=(0, 0))
        islands = sorted(polygons(make_valid(shape)), key=lambda p: -p.area)
        if not islands:
            continue
        skin.shape = islands[0]
        skin.cut_lines = hinge_slits(skin.shape, skin.x_lo * k, skin.x_hi * k,
                                     skin.s_lo, skin.s_hi, cfg)
        if skin.min_radius < 4 * t:
            warnings.append(f"Chapa flexível {skin.name}: raio de curvatura de "
                            f"{skin.min_radius:.0f} mm é apertado para chapa de {t:g} mm; "
                            "teste a flexão antes (ou umedeça/aqueça o MDF).")
        if not end_tabs:
            warnings.append(f"Chapa flexível {skin.name}: sem placas nas pontas para "
                            "encaixar; fixe com cola nas nervuras.")
        covered[comp] = True
        skins.append(skin)
    return skins, covered, warnings
