"""Peças cortáveis: geometria final, etiqueta gravada e compensação de kerf."""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import MultiLineString, Polygon, box
from shapely.ops import polylabel

from .config import SliceConfig
from .font import render_text, text_size
from .slicer import Layer, polygons


@dataclass
class Part:
    name: str
    layer_index: int
    island_index: int
    shape: Polygon                      # geometria de projeto (dimensão real da peça)
    label: MultiLineString | None = None

    def cut_shape(self, kerf: float) -> Polygon:
        """Contorno a ser seguido pelo laser: expande kerf/2 (furos encolhem kerf/2)."""
        if kerf <= 0:
            return self.shape
        return self.shape.buffer(kerf / 2.0, join_style="mitre", mitre_limit=3.0)


def merge_lines(*geoms) -> MultiLineString | None:
    lines = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        lines.extend(g.geoms if hasattr(g, "geoms") else [g])
    return MultiLineString(lines) if lines else None


def place_label(shape: Polygon, text: str, cfg: SliceConfig,
                avoid=None) -> MultiLineString | None:
    """Grava `text` no ponto mais interno da peça, o maior possível até label_height."""
    if avoid is not None and not avoid.is_empty:
        shape = shape.difference(avoid.buffer(1.0))
    inner = shape.buffer(-0.6)
    if inner.is_empty:
        return None
    h = cfg.label_height
    while h >= cfg.label_min_height - 1e-9:
        w, th = text_size(text, h)
        core = inner.buffer(-th / 2.0)
        for poly in sorted(polygons(core), key=lambda p: -p.area)[:3]:
            c = polylabel(poly, tolerance=0.1)
            if box(c.x - w / 2, c.y - th / 2, c.x + w / 2, c.y + th / 2).within(inner):
                return render_text(text, h, (c.x, c.y))
        h *= 0.8
    return None


def build_parts(layers: list[Layer], cfg: SliceConfig) -> tuple[list[Part], list[str]]:
    parts: list[Part] = []
    warnings: list[str] = []
    for layer in layers:
        multi = len(layer.islands) > 1
        for k, island in enumerate(layer.islands):
            name = f"{layer.index + 1}" + (f"-{k + 1}" if multi else "")
            part = Part(name=name, layer_index=layer.index, island_index=k, shape=island)
            if cfg.labels:
                part.label = place_label(island, name, cfg)
                if part.label is None:
                    warnings.append(f"Peça {name}: pequena demais para gravar a etiqueta.")
            parts.append(part)
    return parts, warnings
