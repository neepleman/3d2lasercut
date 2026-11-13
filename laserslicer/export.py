"""Exportação: SVG/DXF por chapa, pré-visualização 3D e relatório."""
from __future__ import annotations

import os

import ezdxf
import trimesh
from shapely.geometry import LineString, Polygon

from .config import SliceConfig
from .nesting import Sheet
from .pins import Pin
from .slicer import Layer, polygons

CUT_COLOR = "#FF0000"       # vermelho = corte (convenção comum em LightBurn/RDWorks)
ENGRAVE_COLOR = "#0000FF"   # azul = gravação/marcação


def _lines(geom) -> list[LineString]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    return [g for g in geom.geoms if isinstance(g, LineString)]


def _rings(poly: Polygon):
    """Furos primeiro, contorno externo por último (ordem certa de corte)."""
    return [*poly.interiors, poly.exterior]


# ---------------------------------------------------------------- SVG

def sheet_to_svg(sheet: Sheet) -> str:
    W, H = sheet.width, sheet.height

    def pt(x, y):
        return f"{x:.3f},{H - y:.3f}"  # SVG tem Y para baixo: espelha a coordenada, não a peça

    engrave, holes, outlines = [], [], []
    for pp in sheet.placed:
        for line in _lines(pp.label):
            engrave.append(f'<polyline points="{" ".join(pt(*c) for c in line.coords)}"/>')
        for poly in polygons(pp.cut):
            for ring in poly.interiors:
                holes.append(f'<polygon points="{" ".join(pt(*c) for c in ring.coords[:-1])}"/>')
            outlines.append(
                f'<polygon points="{" ".join(pt(*c) for c in poly.exterior.coords[:-1])}"/>'
            )

    stroke = 'fill="none" stroke-width="0.1"'
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" '
        f'viewBox="0 0 {W} {H}">',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="none" stroke="#999999" '
        f'stroke-width="0.2" stroke-dasharray="4 2" id="limite-da-chapa"/>',
        f'<g id="gravacao" {stroke} stroke="{ENGRAVE_COLOR}">', *engrave, "</g>",
        f'<g id="corte-furos" {stroke} stroke="{CUT_COLOR}">', *holes, "</g>",
        f'<g id="corte-contornos" {stroke} stroke="{CUT_COLOR}">', *outlines, "</g>",
        "</svg>",
    ])


# ---------------------------------------------------------------- DXF

def sheet_to_dxf(sheet: Sheet, path: str) -> None:
    doc = ezdxf.new("R2010", setup=False)
    doc.units = ezdxf.units.MM
    doc.layers.add("GRAVACAO", color=5)   # azul
    doc.layers.add("CORTE", color=1)      # vermelho
    doc.layers.add("CHAPA", color=8)      # cinza, só referência
    msp = doc.modelspace()

    for pp in sheet.placed:
        for line in _lines(pp.label):
            msp.add_lwpolyline(list(line.coords), dxfattribs={"layer": "GRAVACAO"})
    for pp in sheet.placed:
        for poly in polygons(pp.cut):
            for ring in _rings(poly):
                msp.add_lwpolyline(list(ring.coords)[:-1], close=True,
                                   dxfattribs={"layer": "CORTE"})
    W, H = sheet.width, sheet.height
    msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                       dxfattribs={"layer": "CHAPA"})
    doc.saveas(path)


# ---------------------------------------------------------------- Pré-visualização

def preview_mesh(layers: list[Layer], cfg: SliceConfig) -> trimesh.Trimesh | None:
    """Empilha as camadas (com furos) como sólidos, para conferir antes de cortar."""
    meshes = []
    for layer in layers:
        for island in layer.islands:
            m = trimesh.creation.extrude_polygon(island, cfg.thickness)
            m.apply_translation([0, 0, layer.index * cfg.thickness])
            meshes.append(m)
    return trimesh.util.concatenate(meshes) if meshes else None


# ---------------------------------------------------------------- Relatório

def cut_length(sheet: Sheet) -> float:
    return sum(r.length for pp in sheet.placed for p in polygons(pp.cut) for r in _rings(p))


def write_report(path: str, *, source: str, cfg: SliceConfig, layers: list[Layer],
                 sheets: list[Sheet], pins: list[Pin], warnings: list[str],
                 model_height: float) -> str:
    n_parts = sum(len(s.placed) for s in sheets)
    stack = len(layers) * cfg.thickness
    lines = [
        f"# Relatório de fatiamento — {os.path.basename(source)}",
        "",
        "## Resumo",
        f"- Material: chapa de {cfg.thickness:g} mm, kerf {cfg.kerf:g} mm",
        f"- Eixo de empilhamento: {cfg.axis.upper()}",
        f"- Altura do modelo: {model_height:.1f} mm → pilha montada: {stack:.1f} mm "
        f"({len(layers)} camadas)",
        f"- Peças: {n_parts}  |  Chapas de {cfg.sheet_width:g} x {cfg.sheet_height:g} mm: "
        f"{len(sheets)}",
        f"- Comprimento total de corte: {sum(cut_length(s) for s in sheets) / 1000:.2f} m",
        "",
        "## Chapas",
    ]
    for s in sheets:
        names = ", ".join(sorted((pp.part.name for pp in s.placed),
                                 key=lambda n: [int(x) for x in n.split("-")]))
        lines.append(f"- Chapa {s.index + 1}: {len(s.placed)} peças "
                     f"(corte {cut_length(s) / 1000:.2f} m) — {names}")

    lines += ["", "## Pinos de alinhamento"]
    if pins:
        lines.append(f"Diâmetro do pino: {cfg.pin_diameter:g} mm "
                     f"(furo de {2 * cfg.pin_hole_radius:.2f} mm). "
                     "Corte cada cavilha no comprimento indicado (ou um pouco menor).")
        lines.append("")
        lines.append("| Pino | Camadas | Comprimento |")
        lines.append("|---|---|---|")
        for p in pins:
            lines.append(f"| P{p.id} | {p.first_layer + 1}–{p.last_layer + 1} | "
                         f"{p.length(cfg.thickness):.1f} mm |")
        total = sum(p.length(cfg.thickness) for p in pins)
        lines.append("")
        lines.append(f"Total de cavilha: {total / 10:.1f} cm")
    else:
        lines.append("Nenhum pino gerado.")

    lines += [
        "",
        "## Montagem",
        "1. A peça **1** é a base; empilhe em ordem crescente.",
        "2. A etiqueta gravada (número sublinhado) fica **para cima** e legível.",
        "3. Peças com sufixo (ex.: 12-1, 12-2) estão na mesma camada.",
        "4. Use os pinos para alinhar; cole camada a camada.",
    ]
    if warnings:
        lines += ["", "## Avisos"] + [f"- {w}" for w in warnings]
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
