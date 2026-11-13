"""Modo grade encaixada ("waffle"): fatias verticais em X e Y com fendas de meia altura.

Geometria (modelo já preparado: base em Z=0, "para cima" = +Z):

- Fatias X ficam nos planos x = a. Perfil 2D em (u, v) = (y, z).
- Fatias Y ficam nos planos y = b. Perfil 2D em (u, v) = (x, z).
- No cruzamento (a, b), a reta vertical comum aparece como u = b no perfil X
  e u = a no perfil Y. Onde as duas peças têm material ao longo dessa reta
  (intervalo [z0, z1]) elas se cruzam, então:
    * a peça X recebe fenda **de cima** para baixo até o meio (zm),
    * a peça Y recebe fenda **de baixo** para cima até o meio.
- Quando um par de peças se cruza em vários pontos ao longo da mesma reta
  (ex.: uma fatia X que o modelo divide em duas partes), a direção é escolhida
  por par para remover o mínimo de material; veja `build_waffle`.
- Montagem: em cada encaixe, a peça com a fenda aberta para cima fica por
  baixo e a outra desce por cima.

Ao lado de cada fenda é gravado o nome da peça que encaixa ali.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from .config import SliceConfig
from .font import render_text, text_size
from .parts import Part, merge_lines, place_label
from .slicer import clean_shape, polygons, section_shapes

# (x, y, z) -> (y, z, x): plano x = a vira z' = a; perfil em (y, z)
T_X = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
# (x, y, z) -> (x, z, -y): plano y = b vira z' = -b; perfil em (x, z)
T_Y = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)


@dataclass
class Slot:
    u: float            # posição da fenda ao longo do perfil
    v_mid: float        # meio do cruzamento (fundo da fenda)
    v_end: float        # extremidade aberta (topo p/ X, base p/ Y)
    mate: str           # nome da fatia que encaixa aqui


@dataclass
class WaffleSlice:
    family: str                 # "X" ou "Y"
    index: int
    position: float             # x = a (família X) ou y = b (família Y)
    profile: object             # geometria 2D original
    slots: list[Slot] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.family}{self.index + 1}"


@dataclass
class WaffleResult:
    slices: list[WaffleSlice]
    parts: list[Part]
    placements: list[tuple[Polygon, np.ndarray, float]]  # (perfil final, T, altura) p/ prévia
    n_crossings: int
    warnings: list[str]


def grid_positions(lo: float, hi: float, spacing: float) -> list[float]:
    """Posições centradas, com as fatias das pontas a >= spacing/2 das bordas."""
    extent = hi - lo
    n = max(1, int(np.floor(extent / spacing + 1e-9)))
    center = (lo + hi) / 2
    return [center + (k - (n - 1) / 2) * spacing for k in range(n)]


def _band_segments(islands: list[Polygon], u: float, width: float,
                   v_max: float) -> list[tuple[float, float, int]]:
    """Intervalos em v onde há material na faixa [u - width/2, u + width/2].

    Considera a faixa inteira (não só a linha central) porque a chapa tem
    espessura: numa borda curva, a lateral da peça vai além da linha central.
    Retorna (v0, v1, índice da ilha).
    """
    strip = box(u - width / 2, -1.0, u + width / 2, v_max + 1.0)
    out = []
    for k, island in enumerate(islands):
        inter = island.intersection(strip)
        spans = sorted((p.bounds[1], p.bounds[3]) for p in polygons(inter) if p.area > 0)
        merged: list[list[float]] = []
        for v0, v1 in spans:
            if merged and v0 <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], v1)
            else:
                merged.append([v0, v1])
        out.extend((v0, v1, k) for v0, v1 in merged)
    return out


def _slot_rect(slot: Slot, width: float) -> Polygon:
    lo, hi = sorted((slot.v_mid, slot.v_end))
    pad = 0.5  # garante que a fenda "vaze" a borda
    if slot.v_end > slot.v_mid:
        hi += pad
    else:
        lo -= pad
    return box(slot.u - width / 2, lo, slot.u + width / 2, hi)


def _mate_label(island: Polygon, slot: Slot, cfg: SliceConfig):
    """Grava o nome da peça parceira logo além do fundo da fenda."""
    inner = island.buffer(-0.5)
    direction = -1.0 if slot.v_end > slot.v_mid else 1.0  # para longe da abertura
    h = min(cfg.label_height, 3.0)
    while h >= cfg.label_min_height - 1e-9:
        w, th = text_size(slot.mate, h)
        cy = slot.v_mid + direction * (1.0 + th / 2)
        bx = box(slot.u - w / 2, cy - th / 2, slot.u + w / 2, cy + th / 2)
        if bx.within(inner):
            return render_text(slot.mate, h, (slot.u, cy)), bx
        h *= 0.8
    return None, None


def build_waffle(mesh: trimesh.Trimesh, cfg: SliceConfig) -> WaffleResult:
    (x0, y0, _), (x1, y1, z1) = mesh.bounds
    xs = grid_positions(x0, x1, cfg.grid_x)
    ys = grid_positions(y0, y1, cfg.grid_y)
    warnings: list[str] = []

    mx = mesh.copy()
    mx.apply_transform(T_X)
    my = mesh.copy()
    my.apply_transform(T_Y)
    prof_x = section_shapes(mx, xs)
    prof_y = section_shapes(my, [-b for b in ys])

    sx = [make_slice("X", i, a, p, cfg) for i, (a, p) in enumerate(zip(xs, prof_x))]
    sy = [make_slice("Y", j, b, p, cfg) for j, (b, p) in enumerate(zip(ys, prof_y))]
    return assemble_waffle(sx, sy, z1, cfg, warnings)


def make_slice(fam: str, i: int, pos: float, prof, cfg: SliceConfig) -> WaffleSlice:
    islands = clean_shape(prof, cfg)
    return WaffleSlice(fam, i, pos, unary_union(islands) if islands else Polygon())


def assemble_waffle(sx: list[WaffleSlice], sy: list[WaffleSlice], z1: float,
                    cfg: SliceConfig, warnings: list[str] | None = None) -> WaffleResult:
    """Calcula fendas nos cruzamentos de perfis X/Y prontos e gera as peças."""
    warnings = [] if warnings is None else warnings
    # ---- cruzamentos -> fendas
    # Duas peças (ilhas) que se cruzam se movem juntas na montagem, então todos
    # os cruzamentos de um mesmo par usam a mesma direção:
    #   "A": X aberta em cima / Y aberta embaixo (Y desce sobre X)
    #   "B": X aberta embaixo / Y aberta em cima (X desce sobre Y)
    # Escolhe-se a que remove menos material (A em caso de empate).
    n_cross = 0
    for X in sx:
        if X.profile.is_empty:
            continue
        islands_x = polygons(X.profile)
        for Y in sy:
            if Y.profile.is_empty:
                continue
            islands_y = polygons(Y.profile)
            groups: dict[tuple[int, int], list] = {}
            w = cfg.slot_width
            for ax0, ax1, ix in _band_segments(islands_x, Y.position, w, z1):  # u = y = b
                for ay0, ay1, iy in _band_segments(islands_y, X.position, w, z1):  # u = x = a
                    lo, hi = max(ax0, ay0), min(ax1, ay1)
                    if hi - lo <= cfg.min_slot_overlap:
                        continue
                    groups.setdefault((ix, iy), []).append(((lo + hi) / 2, ax0, ax1, ay0, ay1))
            for crossings in groups.values():
                cost_a = sum(ax1 - ay0 for _, ax0, ax1, ay0, ay1 in crossings)
                cost_b = sum(ay1 - ax0 for _, ax0, ax1, ay0, ay1 in crossings)
                use_a = cost_a <= cost_b
                for mid, ax0, ax1, ay0, ay1 in crossings:
                    X.slots.append(Slot(u=Y.position, v_mid=mid,
                                        v_end=ax1 if use_a else ax0, mate=Y.name))
                    Y.slots.append(Slot(u=X.position, v_mid=mid,
                                        v_end=ay0 if use_a else ay1, mate=X.name))
                    n_cross += 1

    # ---- peças
    parts: list[Part] = []
    placements = []
    for fam_slices, T, sign in ((sx, T_X, 1.0), (sy, T_Y, -1.0)):
        for S in fam_slices:
            if S.profile.is_empty:
                continue
            cuts = [_slot_rect(sl, cfg.slot_width) for sl in S.slots]
            shape = S.profile.difference(unary_union(cuts)) if cuts else S.profile
            islands = clean_shape(shape, cfg)
            if not islands:
                continue
            original = len(clean_shape(S.profile, cfg))
            if len(islands) > original:
                warnings.append(f"Peça {S.name}: as fendas a dividiram em {len(islands)} "
                                "partes. Considere mudar o espaçamento da grade.")
            multi = len(islands) > 1
            for k, island in enumerate(islands):
                name = S.name + (f"-{k + 1}" if multi else "")
                part = Part(name=name, layer_index=S.index, island_index=k, shape=island)
                if cfg.labels:
                    mate_lines, boxes = [], []
                    for sl in S.slots:
                        if island.distance(_slot_rect(sl, cfg.slot_width)) > 0.01:
                            continue
                        lbl, bx = _mate_label(island, sl, cfg)
                        if lbl is not None:
                            mate_lines.append(lbl)
                            boxes.append(bx)
                    main = place_label(island, name, cfg,
                                       avoid=unary_union(boxes) if boxes else None)
                    if main is None:
                        warnings.append(f"Peça {name}: pequena demais para gravar a etiqueta.")
                    part.label = merge_lines(main, *mate_lines)
                parts.append(part)
                placements.append((island, T, sign * S.position))

    if not n_cross:
        warnings.append("Nenhum cruzamento entre fatias X e Y: a grade não vai se sustentar. "
                        "Reduza o espaçamento da grade.")
    return WaffleResult(slices=sx + sy, parts=parts, placements=placements,
                        n_crossings=n_cross, warnings=warnings)


def waffle_preview(placements, thickness: float) -> trimesh.Trimesh | None:
    meshes = []
    for poly, T, h in placements:
        m = trimesh.creation.extrude_polygon(poly, thickness)
        m.apply_translation([0, 0, h - thickness / 2])
        m.apply_transform(np.linalg.inv(T))
        meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None
