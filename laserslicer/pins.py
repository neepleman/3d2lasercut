"""Pinos de alinhamento entre camadas.

Estratégia: percorre as camadas de baixo para cima. Para cada par de camadas
vizinhas (i, i+1), calcula a região onde um furo cabe nas duas (interseção
erodida pelo raio do furo + parede mínima). Pinos que já atravessam a camada i
e caem nessa região continuam para cima; se faltar pino naquela região de
contato, novos pinos são criados o mais afastados possível entre si
(dois pinos distantes impedem que as camadas girem).

Resultado: cavilhas passantes de comprimento variável, que o usuário corta
no tamanho indicado no relatório.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import polylabel, unary_union

from .config import SliceConfig
from .slicer import Layer, polygons


@dataclass
class Pin:
    id: int
    x: float
    y: float
    first_layer: int
    last_layer: int

    @property
    def n_layers(self) -> int:
        return self.last_layer - self.first_layer + 1

    def length(self, thickness: float) -> float:
        return self.n_layers * thickness

    def spans(self, layer_index: int) -> bool:
        return self.first_layer <= layer_index <= self.last_layer


INSET_FRACTION = 0.35


def _candidates(region: Polygon) -> np.ndarray:
    """Pontos candidatos: o mais interno da região + um contorno recuado.

    O recuo (fração do raio inscrito) afasta os pinos da borda, então eles
    tendem a continuar válidos nas camadas seguintes -> menos pinos, mais longos.
    """
    center = polylabel(region, tolerance=0.1)
    inradius = region.exterior.distance(center)
    for poly in polygons(region.buffer(-inradius * INSET_FRACTION)):
        if poly.contains(center) or poly.distance(center) < 1e-6:
            region = poly
            break
    pts = [center.coords[0]]
    pts.extend(region.exterior.coords[:-1])
    return np.asarray(pts, dtype=float)


def _min_dist(pt: np.ndarray, others: list[np.ndarray]) -> float:
    if not others:
        return np.inf
    return float(np.min(np.linalg.norm(np.asarray(others) - pt, axis=1)))


def place_pins(layers: list[Layer], cfg: SliceConfig) -> list[Pin]:
    if not cfg.pins or len(layers) < 2:
        return []

    r = cfg.pin_hole_radius
    min_sep = 2 * r + cfg.pin_margin
    erosion = r + cfg.pin_margin
    pins: list[Pin] = []
    active: list[Pin] = []  # pinos que atravessam a camada atual

    for i in range(len(layers) - 1):
        lower, upper = layers[i], layers[i + 1]
        contact = lower.shape.intersection(upper.shape)
        regions = polygons(contact.buffer(-erosion)) if not contact.is_empty else []

        # todos os furos já existentes na camada i (contínuos ou terminando nela)
        holes_here = [np.array([p.x, p.y]) for p in active]
        continuing: list[Pin] = []

        for region in sorted(regions, key=lambda p: -p.area):
            tol_region = region.buffer(1e-6)
            in_region = [p for p in active if tol_region.contains(Point(p.x, p.y))]
            continuing.extend(in_region)
            chosen = [np.array([p.x, p.y]) for p in in_region]

            need = cfg.max_pins - len(in_region)
            if need <= 0:
                continue
            cands = _candidates(region)
            for k in range(need):
                # Referência para "o mais distante possível":
                #  - região sem pinos e só 1 pino a criar -> usa o ponto mais interno
                #  - região sem pinos e 2+ a criar -> 1º pino vai para a extremidade
                #    mais longe do centro, o 2º para a extremidade oposta
                if chosen:
                    ref = chosen
                elif need == 1:
                    ref = None
                else:
                    ref = [cands[0]]
                best, best_score = None, -1.0
                for c in cands:
                    if _min_dist(c, holes_here) < min_sep:
                        continue
                    if ref is None:
                        best = c
                        break  # cands[0] é o polylabel (ou o próximo válido)
                    score = _min_dist(c, ref)
                    if score > best_score:
                        best, best_score = c, score
                if best is None:
                    break
                pin = Pin(id=len(pins) + 1, x=float(best[0]), y=float(best[1]),
                          first_layer=i, last_layer=i + 1)
                pins.append(pin)
                continuing.append(pin)
                chosen.append(best)
                holes_here.append(best)

        for p in continuing:
            p.last_layer = i + 1
        active = continuing

    return pins


def pin_holes_for_layer(pins: list[Pin], layer_index: int, cfg: SliceConfig):
    holes = [Point(p.x, p.y).buffer(cfg.pin_hole_radius, quad_segs=16)
             for p in pins if p.spans(layer_index)]
    return unary_union(holes) if holes else None


def apply_pin_holes(layers: list[Layer], pins: list[Pin], cfg: SliceConfig) -> None:
    for layer in layers:
        holes = pin_holes_for_layer(pins, layer.index, cfg)
        if holes is None:
            continue
        new_islands = []
        for island in layer.islands:
            new_islands.extend(polygons(island.difference(holes)))
        layer.islands = sorted(new_islands, key=lambda p: -p.area)
