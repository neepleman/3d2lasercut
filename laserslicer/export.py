"""Exportação: SVG/DXF por chapa, pré-visualização 3D e relatório."""
from __future__ import annotations

import os
import re

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
        for line in _lines(pp.cut_lines):
            holes.append(f'<polyline points="{" ".join(pt(*c) for c in line.coords)}"/>')
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
        for line in _lines(pp.cut_lines):
            msp.add_lwpolyline(list(line.coords), dxfattribs={"layer": "CORTE"})
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
    rings = sum(r.length for pp in sheet.placed for p in polygons(pp.cut) for r in _rings(p))
    return rings + sum(ln.length for pp in sheet.placed for ln in _lines(pp.cut_lines))


MODE_NAMES = {
    "stack": "fatias empilhadas",
    "waffle": "grade encaixada (waffle)",
    "plates": "placas + chapas flexíveis + nervuras internas",
}
JOINT_NAMES = {
    "finger_convex": "dedos",
    "finger_concave": "dedos, canto interno",
    "butt_convex": "topo, ângulo ≠ 90°",
    "open_concave": "aberta, canto interno ≠ 90°",
}


def _plates_sections(cfg: SliceConfig, result) -> list[str]:
    pi = result.plates
    lines = ["", "## Placas"]
    neighbors: dict[int, list[str]] = {P.id: [] for P in pi.plates}
    for j in pi.joints:
        neighbors[j.a].append(f"{pi.plates[j.b].name} ({JOINT_NAMES[j.kind]})")
        neighbors[j.b].append(f"{pi.plates[j.a].name} ({JOINT_NAMES[j.kind]})")
    tabbed = set()
    for P in pi.plates:
        if P.holes:
            tabbed.add(P.name)
    lines += ["| Placa | Tamanho | Encaixa em | Abas aparentes |", "|---|---|---|---|"]
    for P in pi.plates:
        x0, y0, x1, y1 = P.base.bounds
        lines.append(f"| {P.name} | {x1 - x0:.0f} x {y1 - y0:.0f} mm | "
                     f"{', '.join(neighbors[P.id]) or '—'} | "
                     f"{'sim' if P.name in tabbed else 'não'} |")
    counts: dict[str, int] = {}
    for j in pi.joints:
        counts[j.kind] = counts.get(j.kind, 0) + 1
    lines.append("")
    lines.append("Juntas: " + "; ".join(f"{n} × {JOINT_NAMES[k]}" for k, n in counts.items()))
    if tabbed:
        lines.append(f"Placas com abas aparentes: {', '.join(sorted(tabbed, key=_natural_key))}. "
                     "Para esconder as abas numa face de vitrine, use `--no-tabs P1,...` "
                     "(as nervuras passam a só encostar nela e precisam de cola).")
    if pi.skins:
        lines += ["", "## Chapas flexíveis (kerf bending)"]
        for sk in pi.skins:
            x0, y0, x1, y1 = sk.shape.bounds
            ends = ", ".join(sk.end_plates) or "nenhuma (só cola)"
            lines.append(f"- {sk.name}: {x1 - x0:.0f} x {y1 - y0:.0f} mm planificada, "
                         f"raio mínimo ≈ {sk.min_radius:.0f} mm, "
                         f"{len(sk.cut_lines.geoms) if sk.cut_lines else 0} cortes flexíveis; "
                         f"abas em: {ends}")
    lines += ["", "## Nervuras internas"]
    if pi.ribs is not None:
        lines.append(f"- {len(pi.ribs.parts)} nervuras a cada {pi.rib_spacing:.0f} mm, "
                     f"{pi.ribs.n_crossings} cruzamentos, {pi.n_tabs} abas nas placas")
        lines.append(f"- Motivo: {pi.ribs_reason}")
    else:
        lines.append(f"- Sem nervuras ({pi.ribs_reason}).")
    lines += [
        "",
        "## Montagem",
        "1. As etiquetas ficam na face **de dentro** de cada placa (escondidas após montar).",
    ]
    step = 2
    if pi.ribs is not None:
        lines.append(f"{step}. Monte as nervuras como uma grade: em cada cruzamento, a peça com a "
                     "fenda aberta para cima fica por baixo. Ao lado de cada fenda está o nome "
                     "da parceira.")
        step += 1
    lines.append(f"{step}. Comece pela placa da base e vá fechando a caixa: cada placa entra "
                 "na direção perpendicular a ela, com os dedos se intercalando nas quinas"
                 + (" e as abas das nervuras atravessando os furos." if pi.ribs else "."))
    step += 1
    if pi.skins:
        lines.append(f"{step}. Chapas flexíveis: dobre devagar, com os cortes para dentro da "
                     "curva. Encaixe as abas numa placa das pontas antes de fechar a do outro "
                     "lado. As abas ficam em entalhes abertos na borda da placa, então "
                     "**cole a chapa** nas bordas e nas nervuras.")
        step += 1
    lines.append(f"{step}. Juntas de topo (ângulos ≠ 90°) deixam uma fresta em V: preencha com "
                 "cola e serragem ou massa." if any(j.kind == "butt_convex" for j in pi.joints)
                 else f"{step}. Cole as juntas com cola branca (PVA) para MDF ou cola de "
                 "acrílico, conforme o material.")
    return lines


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def write_report(path: str, *, source: str, cfg: SliceConfig, result) -> str:
    sheets: list[Sheet] = result.sheets
    n_parts = sum(len(s.placed) for s in sheets)
    lines = [
        f"# Relatório de corte — {os.path.basename(source)}",
        "",
        "## Resumo",
        f"- Modo: {MODE_NAMES[cfg.mode]}",
        f"- Material: chapa de {cfg.thickness:g} mm, kerf {cfg.kerf:g} mm",
        f"- Eixo vertical do modelo: {cfg.axis.upper()}",
    ]
    if cfg.mode == "waffle":
        wr = result.waffle
        nx = sum(1 for s in wr.slices if s.family == "X" and not s.profile.is_empty)
        ny = sum(1 for s in wr.slices if s.family == "Y" and not s.profile.is_empty)
        lines += [
            f"- Altura do modelo: {result.model_height:.1f} mm",
            f"- Grade: {nx} fatias X a cada {cfg.grid_x:g} mm, {ny} fatias Y a cada "
            f"{cfg.grid_y:g} mm, {wr.n_crossings} encaixes",
            f"- Largura das fendas: {cfg.slot_width:.2f} mm "
            f"(espessura + folga de {cfg.slot_clearance:g} mm)",
        ]
    elif cfg.mode == "plates":
        pi = result.plates
        lines += [
            f"- Medidas: {' x '.join(f'{v:.0f}' for v in result.model_extents)} mm",
            f"- Superfície coberta por placas e chapas flexíveis: {100 * pi.coverage:.0f}%",
            f"- {len(pi.plates)} placas, {len(pi.skins)} chapas flexíveis, "
            f"{len(pi.ribs.parts) if pi.ribs else 0} nervuras",
            f"- Folga dos encaixes: {cfg.slot_clearance:g} mm; dedos de "
            f"~{cfg.finger_width_eff:g} mm",
        ]
    else:
        layers: list[Layer] = result.layers
        stack = len(layers) * cfg.thickness
        lines.append(f"- Altura do modelo: {result.model_height:.1f} mm → pilha montada: "
                     f"{stack:.1f} mm ({len(layers)} camadas)")
    lines += [
        f"- Peças: {n_parts}  |  Chapas de {cfg.sheet_width:g} x {cfg.sheet_height:g} mm: "
        f"{len(sheets)} (nesting: {'contorno real' if cfg.nesting == 'shape' else 'retângulos'})",
        f"- Comprimento total de corte: {sum(cut_length(s) for s in sheets) / 1000:.2f} m",
        "",
        "## Chapas",
    ]
    usable = (cfg.sheet_width - 2 * cfg.sheet_margin) * (cfg.sheet_height - 2 * cfg.sheet_margin)
    for s in sheets:
        names = ", ".join(sorted((pp.part.name for pp in s.placed), key=_natural_key))
        area = sum(pp.cut.area for pp in s.placed)
        lines.append(f"- Chapa {s.index + 1}: {len(s.placed)} peças, "
                     f"{100 * area / usable:.0f}% de aproveitamento "
                     f"(corte {cut_length(s) / 1000:.2f} m) — {names}")

    if cfg.mode == "plates":
        lines += _plates_sections(cfg, result)
    elif cfg.mode == "waffle":
        lines += [
            "",
            "## Montagem",
            "1. Ao lado de cada fenda está gravado o nome da peça que encaixa nela.",
            "2. Coloque as peças **X** em pé, em ordem (X1, X2, …). Em cada encaixe, a peça "
            "com a fenda **aberta para cima** fica por baixo; a outra desce por cima.",
            "3. Normalmente as peças X recebem as Y por cima; peças soltas no alto do modelo "
            "(ex.: X1-2) podem ser encaixadas por último, de cima para baixo.",
            "4. Se o encaixe estiver duro, lixe levemente; se estiver frouxo, aumente "
            "o kerf ou diminua a folga das fendas na próxima vez.",
        ]
    else:
        pins: list[Pin] = result.pins
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
    if result.warnings:
        lines += ["", "## Avisos"] + [f"- {w}" for w in result.warnings]
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
