"""Distribuição das peças nas chapas (nesting).

Dois algoritmos:

- "shape" (padrão): nesting pelo contorno real. Cada peça é rasterizada numa
  grade fina (já dilatada por metade do espaçamento) e uma correlação via FFT
  encontra, de uma vez, todas as posições livres na chapa. Escolhe-se a mais
  baixa/à esquerda entre 4 rotações. Peças pequenas podem cair dentro de
  furos de peças grandes.
- "shelf": empacotamento em prateleiras por retângulo mínimo (rápido e previsível).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import math

import numpy as np
import shapely
from scipy.signal import fftconvolve
from shapely.affinity import rotate, translate
from shapely.geometry import MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry

from .config import SliceConfig
from .parts import Part


@dataclass
class PlacedPart:
    part: Part
    cut: Polygon                         # contorno com kerf, já posicionado na chapa
    label: MultiLineString | None        # gravação, já posicionada


@dataclass
class _Shelf:
    y: float
    height: float
    x: float = 0.0


@dataclass
class Sheet:
    index: int
    width: float
    height: float
    placed: list[PlacedPart] = field(default_factory=list)
    shelves: list[_Shelf] = field(default_factory=list)

    @property
    def used_height(self) -> float:
        return sum(s.height for s in self.shelves)


def _min_rect_angle(geom: Polygon) -> float:
    """Ângulo (graus) que alinha o retângulo mínimo da peça com os eixos."""
    rect = geom.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return 0.0
    (x0, y0), (x1, y1) = list(rect.exterior.coords)[:2]
    return -math.degrees(math.atan2(y1 - y0, x1 - x0))


def _orient(cut: Polygon, label: BaseGeometry | None, cfg: SliceConfig):
    """Gira a peça para o retângulo mínimo, deitada (largura >= altura)."""
    angle = _min_rect_angle(cut) if cfg.allow_rotation else 0.0
    minx, miny, maxx, maxy = rotate(cut, angle, origin=(0, 0)).bounds
    if cfg.allow_rotation and (maxy - miny) > (maxx - minx):
        angle += 90.0
    cut_r = rotate(cut, angle, origin=(0, 0))
    label_r = rotate(label, angle, origin=(0, 0)) if label is not None else None
    return cut_r, label_r


def nest(parts: list[Part], cfg: SliceConfig) -> list[Sheet]:
    if cfg.nesting == "shelf":
        return nest_shelf(parts, cfg)
    return nest_shape(parts, cfg)


def _too_big(part: Part, w: float, h: float, uw: float, uh: float) -> ValueError:
    return ValueError(
        f"A peça {part.name} ({w:.0f} x {h:.0f} mm) não cabe na chapa "
        f"útil de {uw:.0f} x {uh:.0f} mm. Reduza a escala, "
        f"use uma chapa maior ou outro eixo de fatiamento."
    )


def nest_shelf(parts: list[Part], cfg: SliceConfig) -> list[Sheet]:
    usable_w = cfg.sheet_width - 2 * cfg.sheet_margin
    usable_h = cfg.sheet_height - 2 * cfg.sheet_margin
    gap = cfg.part_spacing

    items = []
    for part in parts:
        cut, label = _orient(part.cut_shape(cfg.kerf), part.label, cfg)
        minx, miny, maxx, maxy = cut.bounds
        w, h = maxx - minx, maxy - miny
        if w > usable_w or h > usable_h:
            if cfg.allow_rotation and h <= usable_w and w <= usable_h:
                cut = rotate(cut, 90, origin=(0, 0))
                label = rotate(label, 90, origin=(0, 0)) if label is not None else None
                minx, miny, maxx, maxy = cut.bounds
                w, h = maxx - minx, maxy - miny
            else:
                raise _too_big(part, w, h, usable_w, usable_h)
        items.append((part, cut, label, w, h))

    # mais altas primeiro, depois mais largas
    items.sort(key=lambda it: (-round(it[4], 3), -it[3]))

    sheets: list[Sheet] = []

    def place(sheet: Sheet, shelf: _Shelf, part, cut, label, w):
        minx, miny, _, _ = cut.bounds
        dx = cfg.sheet_margin + shelf.x - minx
        dy = cfg.sheet_margin + shelf.y - miny
        sheet.placed.append(PlacedPart(
            part=part,
            cut=translate(cut, dx, dy),
            label=translate(label, dx, dy) if label is not None else None,
        ))
        shelf.x += w + gap

    for part, cut, label, w, h in items:
        done = False
        for sheet in sheets:
            # 1) prateleira existente com espaço (best fit: a mais baixa que comporta)
            fits = [s for s in sheet.shelves if s.height >= h and s.x + w <= usable_w + 1e-9]
            if fits:
                place(sheet, min(fits, key=lambda s: s.height), part, cut, label, w)
                done = True
                break
            # 2) nova prateleira nessa chapa
            top = sheet.used_height
            if top + h <= usable_h + 1e-9:
                shelf = _Shelf(y=top, height=h + gap)
                sheet.shelves.append(shelf)
                place(sheet, shelf, part, cut, label, w)
                done = True
                break
        if not done:
            sheet = Sheet(index=len(sheets), width=cfg.sheet_width, height=cfg.sheet_height)
            sheets.append(sheet)
            shelf = _Shelf(y=0.0, height=h + gap)
            sheet.shelves.append(shelf)
            place(sheet, shelf, part, cut, label, w)

    return sheets


# ---------------------------------------------------------------- nesting por contorno

@dataclass
class _Raster:
    mask: np.ndarray        # bool [linhas=y, colunas=x]
    x0: float               # canto inferior esquerdo da célula (0, 0), coords da peça
    y0: float
    angle: float


def _rasterize(geom, angle: float, res: float, pad: float) -> _Raster:
    """Células cujo centro está a <= pad + meia diagonal da peça girada.

    Conservador: todo ponto a <= pad da peça cai numa célula marcada. Se duas
    peças não dividem células, a distância entre elas é >= 2 * pad.
    """
    g = rotate(geom, angle, origin=(0, 0))
    dil = g.buffer(pad + res * math.sqrt(2) / 2, quad_segs=8)
    bx0, by0, bx1, by1 = dil.bounds
    w = max(1, math.ceil((bx1 - bx0) / res))
    h = max(1, math.ceil((by1 - by0) / res))
    xs = bx0 + (np.arange(w) + 0.5) * res
    ys = by0 + (np.arange(h) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    mask = shapely.contains_xy(dil, X, Y)
    return _Raster(mask=mask, x0=bx0, y0=by0, angle=angle)


def _angles(geom, cfg: SliceConfig) -> list[float]:
    if not cfg.allow_rotation:
        return [0.0]
    a0 = _min_rect_angle(geom)
    out = []
    for k in range(4):
        a = (a0 + 90 * k) % 360
        if all(abs(a - b) > 1e-6 for b in out):
            out.append(a)
    return out


@dataclass
class _Grid:
    occ: np.ndarray
    gx0: float
    gy0: float
    free: int


def _best_position(grid: _Grid, rasters: list[_Raster]):
    """Posição livre mais baixa (topo da peça) e depois mais à esquerda."""
    H, W = grid.occ.shape
    best = None
    occ_f = grid.occ.astype(np.float32)
    empty = not grid.occ.any()
    for r in rasters:
        h, w = r.mask.shape
        if h > H or w > W or r.mask.sum() > grid.free:
            continue
        if empty:
            row, col = 0, 0
        else:
            ov = fftconvolve(occ_f, r.mask[::-1, ::-1].astype(np.float32), mode="valid")
            feas = np.argwhere(ov < 0.5)
            if feas.size == 0:
                continue
            # mais baixo pelo topo da peça, depois mais à esquerda
            row, col = feas[0]
        key = (row + h, col)
        if best is None or key < best[0]:
            best = (key, r, int(row), int(col))
    return best


def nest_shape(parts: list[Part], cfg: SliceConfig) -> list[Sheet]:
    res = cfg.nest_resolution
    pad = cfg.part_spacing / 2
    usable_w = cfg.sheet_width - 2 * cfg.sheet_margin
    usable_h = cfg.sheet_height - 2 * cfg.sheet_margin
    # a grade cobre a área útil + pad (a dilatação pode "vazar" pad da área útil)
    gx0 = gy0 = cfg.sheet_margin - pad
    W = int(math.floor((usable_w + 2 * pad) / res + 1e-9))
    H = int(math.floor((usable_h + 2 * pad) / res + 1e-9))

    items = []
    for part in parts:
        cut = part.cut_shape(cfg.kerf)
        rasters = [_rasterize(cut, a, res, pad) for a in _angles(cut, cfg)]
        if not any(r.mask.shape[0] <= H and r.mask.shape[1] <= W for r in rasters):
            minx, miny, maxx, maxy = rotate(cut, rasters[0].angle, origin=(0, 0)).bounds
            raise _too_big(part, maxx - minx, maxy - miny, usable_w, usable_h)
        items.append((part, cut, rasters))
    items.sort(key=lambda it: -it[1].area)

    sheets: list[Sheet] = []
    grids: list[_Grid] = []

    for part, cut, rasters in items:
        placed = None
        for sheet, grid in zip(sheets, grids):
            best = _best_position(grid, rasters)
            if best is not None:
                placed = (sheet, grid, best)
                break
        if placed is None:
            sheet = Sheet(index=len(sheets), width=cfg.sheet_width, height=cfg.sheet_height)
            grid = _Grid(occ=np.zeros((H, W), dtype=bool), gx0=gx0, gy0=gy0, free=H * W)
            sheets.append(sheet)
            grids.append(grid)
            best = _best_position(grid, rasters)
            if best is None:  # não deveria acontecer (checado acima)
                raise _too_big(part, 0, 0, usable_w, usable_h)
            placed = (sheet, grid, best)

        sheet, grid, (_, r, row, col) = placed
        h, w = r.mask.shape
        grid.occ[row:row + h, col:col + w] |= r.mask
        grid.free -= int(r.mask.sum())
        dx = grid.gx0 + col * res - r.x0
        dy = grid.gy0 + row * res - r.y0
        cut_r = rotate(cut, r.angle, origin=(0, 0))
        label_r = rotate(part.label, r.angle, origin=(0, 0)) if part.label is not None else None
        sheet.placed.append(PlacedPart(
            part=part,
            cut=translate(cut_r, dx, dy),
            label=translate(label_r, dx, dy) if label_r is not None else None,
        ))
    return sheets
