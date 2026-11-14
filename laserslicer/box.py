"""Modo caixa: reconhece peças "quase caixa" e usa uma receita fixa.

Muitas peças de laser são, no fundo, caixas: base, paredes, talvez tampa,
com furos, recortes e divisórias. Modelos feitos para impressão 3D trazem
muitos detalhes internos (encaixes, degraus, canais) que, no modo placas,
viram dezenas de peças pequenas. Aqui a peça é tratada como caixa:

1. Cada corpo do arquivo é uma caixa separada, alinhada aos eixos.
2. Uma face da caixa existe se o modelo cobre boa parte dela (ou se o
   usuário mandar). Faces abertas (ex.: frente de um suporte) ficam de fora.
3. Cada face existente vira **uma placa** com o contorno da face externa do
   modelo e só os furos **passantes** (rebaixos que não atravessam são
   preenchidos). Arredondamentos nas bordas são completados até a aresta.
4. Arestas da caixa onde as duas placas chegam viram finger joints.
5. Paredes internas grandes paralelas às faces viram **divisórias**, presas
   às placas por abas (tab-and-slot) e entre si por fendas de meia altura.

Detalhes internos menores são ignorados de propósito.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely import make_valid
from shapely.geometry import LineString, Polygon, box as sbox
from shapely.ops import unary_union

from .config import SliceConfig
from .plates import Joint, Plate, find_plates, plane_frame, to_local
from .slicer import polygons, section_shapes

# ordem das faces: (eixo, sinal) — nomes como no FreeCAD (frente = -Y)
SIDES = [(0, -1), (0, 1), (1, -1), (1, 1), (2, -1), (2, 1)]
SIDE_NAMES = ["esquerda", "direita", "frente", "fundo", "base", "topo"]
AXIS_TOL = np.cos(np.radians(5))


@dataclass
class BoxFace:
    side: int                   # índice em SIDES
    coverage: float             # fração do retângulo coberta pela face externa
    exists: bool
    forced: bool = False        # decidido pelo usuário (não automático)


@dataclass
class BoxBody:
    index: int
    lo: np.ndarray
    hi: np.ndarray
    faces: list[BoxFace]
    plates: list[Plate] = field(default_factory=list)
    dividers: list[tuple[str, float]] = field(default_factory=list)  # ("X"|"Y", posição)


def axis_aligned_fraction(mesh: trimesh.Trimesh) -> float:
    n = np.abs(mesh.face_normals)
    aligned = n.max(axis=1) >= AXIS_TOL
    return float(mesh.area_faces[aligned].sum() / mesh.area) if mesh.area else 0.0


def _face_rect(side: int, lo, hi, M) -> Polygon:
    ax, sg = SIDES[side]
    others = [i for i in range(3) if i != ax]
    pts = []
    for a in (lo[others[0]], hi[others[0]]):
        for b in (lo[others[1]], hi[others[1]]):
            p = np.zeros(3)
            p[ax] = hi[ax] if sg > 0 else lo[ax]
            p[others[0]], p[others[1]] = a, b
            pts.append(p)
    loc = to_local(M, np.asarray(pts))[:, :2]
    return sbox(loc[:, 0].min(), loc[:, 1].min(), loc[:, 0].max(), loc[:, 1].max())


def _outer_face_shape(mesh, side, lo, hi, M, tol):
    """Projeção, no plano da face, das faces do modelo que estão na face externa."""
    ax, sg = SIDES[side]
    normal = np.zeros(3)
    normal[ax] = sg
    plane = hi[ax] if sg > 0 else lo[ax]
    on = (mesh.face_normals @ normal >= AXIS_TOL)
    cen = mesh.triangles_center[:, ax]
    on &= np.abs(cen - plane) <= tol
    tris = [Polygon(to_local(M, t)[:, :2]) for t in mesh.triangles[on]]
    tris = [t for t in tris if t.area > 1e-9]
    if not tris:
        return Polygon()
    return make_valid(unary_union(tris).buffer(1e-3, join_style="mitre")
                      .buffer(-1e-3, join_style="mitre"))


def _section_at_depth(mesh, side, lo, hi, M, depth):
    """Material do modelo num plano paralelo à face, `depth` mm para dentro."""
    ax, sg = SIDES[side]
    normal = np.zeros(3)
    normal[ax] = sg
    plane = (hi[ax] if sg > 0 else lo[ax]) - sg * depth
    origin = normal * plane
    segs = trimesh.intersections.mesh_plane(mesh, plane_normal=normal, plane_origin=origin)
    if len(segs) == 0:
        return Polygon()
    path = trimesh.load_path(np.asarray(segs))
    polys = []
    for loop in path.discrete:
        loc = to_local(M, np.asarray(loop))[:, :2]
        if len(loc) >= 3:
            polys.append(make_valid(Polygon(loc)))
    if not polys:
        return Polygon()
    from functools import reduce
    return make_valid(reduce(lambda a, b: a.symmetric_difference(b), polys))


def _wall_shape(mesh, side, lo, hi, M, rect, cfg) -> Polygon:
    t = cfg.thickness
    outer = _outer_face_shape(mesh, side, lo, hi, M, cfg.box_face_tol)
    if outer.is_empty:
        return outer
    # furos que não atravessam (rebaixos) são preenchidos: há material atrás deles
    behind = _section_at_depth(mesh, side, lo, hi, M, min(t, 1.5) * 0.75)
    fixed = []
    for poly in polygons(outer):
        keep = []
        for ring in poly.interiors:
            hole = Polygon(ring)
            covered = hole.intersection(behind).area if not behind.is_empty else 0.0
            if covered < 0.5 * hole.area:
                keep.append(ring)
        fixed.append(Polygon(poly.exterior, keep))
    shape = unary_union(fixed)
    # arredondamentos/chanfros nas bordas: completa a faixa junto às arestas
    return snap_to_edges(shape, rect, max(1.0, cfg.max_fillet_radius))


def snap_to_edges(shape, rect: Polygon, r: float):
    """Leva até a borda do retângulo o material que para a menos de `r` dela.

    Cada trecho perto de um lado é "empurrado" perpendicularmente até esse lado
    (sem crescer para os lados), então entalhes e recortes do contorno são
    preservados; só a faixa do arredondamento é preenchida. Duas passadas
    completam os cantos (onde dois arredondamentos se encontram).
    """
    from shapely.affinity import translate
    x0, y0, x1, y1 = rect.bounds
    sides = [  # (faixa junto ao lado, deslocamento até o lado)
        (sbox(x0, y0, x1, y0 + r + 0.5), (0.0, -(r + 0.5))),
        (sbox(x0, y1 - r - 0.5, x1, y1), (0.0, r + 0.5)),
        (sbox(x0, y0, x0 + r + 0.5, y1), (-(r + 0.5), 0.0)),
        (sbox(x1 - r - 0.5, y0, x1, y1), (r + 0.5, 0.0)),
    ]
    for _ in range(2):
        fills = []
        for strip, (dx, dy) in sides:
            for piece in polygons(shape.intersection(strip)):
                if piece.area < 1e-6:
                    continue
                moved = translate(piece, dx, dy)
                fills.append(unary_union([piece, moved]).convex_hull
                             if piece.convex_hull.area - piece.area < 0.05 * piece.area + 1e-3
                             else _sweep(piece, dx, dy))
        if fills:
            shape = make_valid(unary_union([shape, *fills]).intersection(rect))
    return shape


def _sweep(piece: Polygon, dx: float, dy: float):
    """Varredura de um polígono não convexo: une os quadriláteros de cada aresta."""
    from shapely.geometry import Polygon as P
    quads = [piece]
    coords = list(piece.exterior.coords)
    for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:]):
        q = P([(ax, ay), (bx, by), (bx + dx, by + dy), (ax + dx, ay + dy)])
        if q.is_valid and q.area > 1e-9:
            quads.append(q)
    from shapely.affinity import translate
    quads.append(translate(piece, dx, dy))
    return unary_union(quads)


def detect_bodies(mesh: trimesh.Trimesh, cfg: SliceConfig) -> list[trimesh.Trimesh]:
    bodies = [b for b in mesh.split(only_watertight=False) if b.area > 0]
    if not bodies:
        return [mesh]
    biggest = max(abs(b.volume) for b in bodies)
    return [b for b in bodies if abs(b.volume) >= 0.02 * biggest]


def analyze_box(body: trimesh.Trimesh, index: int, cfg: SliceConfig) -> BoxBody:
    lo, hi = body.bounds[0].copy(), body.bounds[1].copy()
    faces = []
    for s, (ax, sg) in enumerate(SIDES):
        normal = np.zeros(3)
        normal[ax] = sg
        origin = np.where(np.arange(3) == ax, hi if sg > 0 else lo, (lo + hi) / 2)
        M = plane_frame(origin, normal)
        rect = _face_rect(s, lo, hi, M)
        outer = _outer_face_shape(body, s, lo, hi, M, cfg.box_face_tol)
        cov = float(outer.area / rect.area) if rect.area > 0 else 0.0
        choice = cfg.box_faces[s] if s < len(cfg.box_faces) else "auto"
        if choice == "on":
            faces.append(BoxFace(s, cov, True, True))
        elif choice == "off":
            faces.append(BoxFace(s, cov, False, True))
        else:
            faces.append(BoxFace(s, cov, cov >= cfg.box_min_coverage))
    return BoxBody(index, lo, hi, faces)


def build_box_plates(body: trimesh.Trimesh, box: BoxBody, cfg: SliceConfig,
                     first_id: int) -> list[Plate]:
    plates = []
    lo, hi = box.lo, box.hi
    for f in box.faces:
        if not f.exists:
            continue
        ax, sg = SIDES[f.side]
        normal = np.zeros(3)
        normal[ax] = sg
        origin = np.where(np.arange(3) == ax, hi if sg > 0 else lo, (lo + hi) / 2)
        M = plane_frame(origin, normal)
        rect = _face_rect(f.side, lo, hi, M)
        shape = _wall_shape(body, f.side, lo, hi, M, rect, cfg)
        if f.forced and shape.area < 0.5 * rect.area:
            shape = rect          # face forçada sem material suficiente: placa cheia
        if shape.is_empty:
            continue
        plates.append(Plate(id=first_id + len(plates), normal=normal, origin=origin, M=M,
                            base=shape, faces=np.array([], dtype=int)))
        plates[-1].side = f.side   # type: ignore[attr-defined]
    box.plates = plates
    return plates


def box_joints(box: BoxBody, tol: float = 0.6) -> list[Joint]:
    """Arestas da caixa onde as duas placas chegam (pode haver vários trechos)."""
    lo, hi = box.lo, box.hi
    joints = []
    ps = box.plates
    for i in range(len(ps)):
        for k in range(i + 1, len(ps)):
            A, B = ps[i], ps[k]
            ax_a, sg_a = SIDES[A.side]
            ax_b, sg_b = SIDES[B.side]
            if ax_a == ax_b:
                continue
            ax_e = 3 - ax_a - ax_b
            p0 = np.zeros(3)
            p0[ax_a] = hi[ax_a] if sg_a > 0 else lo[ax_a]
            p0[ax_b] = hi[ax_b] if sg_b > 0 else lo[ax_b]
            p1 = p0.copy()
            p0[ax_e], p1[ax_e] = lo[ax_e], hi[ax_e]
            L = float(hi[ax_e] - lo[ax_e])

            def spans(P):
                e2 = to_local(P.M, np.vstack([p0, p1]))[:, :2]
                line = LineString(e2)
                band = line.buffer(tol, cap_style="flat")
                out = []
                for g in polygons(P.base.intersection(band)):
                    proj = [line.project(shapely_point(c)) for c in g.exterior.coords]
                    out.append((min(proj), max(proj)))
                return _merge(out)

            for a0, a1 in _intersect(spans(A), spans(B)):
                if a1 - a0 < 2.0:
                    continue
                u = (p1 - p0) / L
                joints.append(Joint(A.id, B.id, p0 + u * a0, p0 + u * a1))
    return joints


def shapely_point(c):
    from shapely.geometry import Point
    return Point(c[0], c[1])


def _merge(spans):
    spans = sorted(spans)
    out = []
    for a, b in spans:
        if out and a <= out[-1][1] + 0.5:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _intersect(sa, sb):
    out = []
    for a0, a1 in sa:
        for b0, b1 in sb:
            lo_, hi_ = max(a0, b0), min(a1, b1)
            if hi_ > lo_:
                out.append((lo_, hi_))
    return out


def find_dividers(body: trimesh.Trimesh, box: BoxBody, cfg: SliceConfig) -> list[tuple[str, float]]:
    """Paredes internas verticais (paralelas às faces X/Y) grandes o suficiente."""
    if cfg.dividers == "none":
        return []
    lo, hi = box.lo, box.hi
    probe = SliceConfig(**{**cfg.__dict__, "plate_min_area": 1.0, "plate_min_width": 1.0})
    cand, _ = find_plates(body, probe)
    out = []
    for fam, ax in (("X", 0), ("Y", 1)):
        other = 1 - ax
        full = (hi[other] - lo[other]) * (hi[2] - lo[2])
        faces = [P for P in cand if abs(P.normal[ax]) > 0.99]
        pos_faces = [P for P in faces if P.normal[ax] > 0]
        neg_faces = [P for P in faces if P.normal[ax] < 0]
        for Pp in pos_faces:
            xp = float(Pp.origin[ax])
            for Pn in neg_faces:
                xn = float(Pn.origin[ax])
                gap = xp - xn               # parede maciça: face -X antes da face +X
                if not (0.3 <= gap <= 3 * cfg.thickness + 3):
                    continue
                mid = (xp + xn) / 2
                if mid - lo[ax] < 2 * cfg.thickness or hi[ax] - mid < 2 * cfg.thickness:
                    continue        # é uma parede da caixa, não divisória
                area = min(Pp.base.area, Pn.base.area)
                if area < cfg.divider_min_fraction * full:
                    continue
                if all(abs(mid - m) > cfg.thickness for f, m in out if f == fam):
                    out.append((fam, mid))
    box.dividers = out
    return out
