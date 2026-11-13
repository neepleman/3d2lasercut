"""Fonte de traço simples (estilo display de 7 segmentos) para gravação a laser.

Usar linhas em vez de texto DXF/SVG garante que qualquer software de laser
(LightBurn, RDWorks, LaserGRBL, K40 Whisperer...) grave exatamente o que foi gerado.
Glifos definidos numa caixa de 0.6 x 1.0 (largura x altura).
"""
from __future__ import annotations

from shapely.affinity import scale, translate
from shapely.geometry import LineString, MultiLineString

W = 0.6
ADVANCE = 0.9

GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "0": [[(0, 0), (W, 0), (W, 1), (0, 1), (0, 0)]],
    "1": [[(0.15, 0.8), (0.35, 1), (0.35, 0)]],
    "2": [[(0, 1), (W, 1), (W, 0.5), (0, 0.5), (0, 0), (W, 0)]],
    "3": [[(0, 1), (W, 1), (W, 0), (0, 0)], [(0.1, 0.5), (W, 0.5)]],
    "4": [[(0, 1), (0, 0.5), (W, 0.5)], [(W, 1), (W, 0)]],
    "5": [[(W, 1), (0, 1), (0, 0.5), (W, 0.5), (W, 0), (0, 0)]],
    "6": [[(W, 1), (0, 1), (0, 0), (W, 0), (W, 0.5), (0, 0.5)]],
    "7": [[(0, 1), (W, 1), (W, 0)]],
    "8": [[(0, 0), (W, 0), (W, 1), (0, 1), (0, 0)], [(0, 0.5), (W, 0.5)]],
    "9": [[(W, 0.5), (0, 0.5), (0, 1), (W, 1), (W, 0), (0, 0)]],
    "-": [[(0.1, 0.5), (0.5, 0.5)]],
    ".": [[(0.25, 0), (0.35, 0)]],
    "C": [[(W, 1), (0, 1), (0, 0), (W, 0)]],
    "P": [[(0, 0), (0, 1), (W, 1), (W, 0.5), (0, 0.5)]],
    "X": [[(0, 0), (W, 1)], [(0, 1), (W, 0)]],
    "Y": [[(0, 1), (W / 2, 0.5), (W, 1)], [(W / 2, 0.5), (W / 2, 0)]],
}
UNDERLINE_Y = -0.25


def text_size(text: str, height: float) -> tuple[float, float]:
    """Largura e altura (incluindo o sublinhado) do texto renderizado."""
    n = len(text)
    width = ((n - 1) * ADVANCE + W) * height if n else 0.0
    return width, height * (1 - UNDERLINE_Y)


def render_text(text: str, height: float, center: tuple[float, float],
                underline: bool = True) -> MultiLineString:
    """Gera os traços do texto centralizado em `center`.

    O sublinhado diferencia 6 de 9 e indica o lado "de cima" da peça.
    """
    lines = []
    for i, ch in enumerate(text):
        for stroke in GLYPHS.get(ch, []):
            lines.append(translate(LineString(stroke), xoff=i * ADVANCE))
    total_w = (len(text) - 1) * ADVANCE + W
    if underline and text:
        lines.append(LineString([(0, UNDERLINE_Y), (total_w, UNDERLINE_Y)]))
    geom = MultiLineString(lines)
    geom = scale(geom, xfact=height, yfact=height, origin=(0, 0))
    # centraliza a caixa [0,total_w] x [UNDERLINE_Y,1]
    cx = total_w * height / 2
    cy = (1 + UNDERLINE_Y) * height / 2 if underline else height / 2
    return translate(geom, xoff=center[0] - cx, yoff=center[1] - cy)
