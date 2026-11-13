"""Distribuição das peças nas chapas (nesting).

v0.1: empacotamento em prateleiras ("shelf packing") sobre o retângulo
mínimo de cada peça. É simples e previsível; um nesting por formato real
(estilo SVGnest/Deepnest) fica para uma versão futura.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    import math
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
                raise ValueError(
                    f"A peça {part.name} ({w:.0f} x {h:.0f} mm) não cabe na chapa "
                    f"útil de {usable_w:.0f} x {usable_h:.0f} mm. Reduza a escala, "
                    f"use uma chapa maior ou outro eixo de fatiamento."
                )
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
