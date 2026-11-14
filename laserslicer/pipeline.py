"""Pipeline completo: malha -> camadas -> pinos -> peças -> chapas -> arquivos."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely.geometry import LineString, MultiLineString, box
from shapely.ops import unary_union

from .config import SliceConfig
from .defillet import SharpEdge, extend_joints, sharpen
from .assembly import assembled_mesh
from .export import sheet_to_dxf, sheet_to_svg, write_report
from .mesh_io import load_mesh, prepare_mesh
from .nesting import Sheet, nest
from .parts import Part, build_parts, place_label
from .pins import Pin, apply_pin_holes, place_pins
from .plates import Joint, Plate, apply_joints, clean_geom, find_joints, find_plates
from .ribs import build_ribs, want_ribs
from .skins import Skin, find_skins
from .slicer import Layer, clean_shape, slice_mesh
from .waffle import WaffleResult, build_waffle


@dataclass
class PlatesInfo:
    plates: list[Plate]
    joints: list[Joint]
    coverage: float
    ribs: WaffleResult | None
    ribs_reason: str
    skins: list[Skin] = field(default_factory=list)
    sharp_edges: list[SharpEdge] = field(default_factory=list)
    rib_spacing: float = 0.0
    n_tabs: int = 0


@dataclass
class Result:
    layers: list[Layer]
    pins: list[Pin]
    parts: list[Part]
    sheets: list[Sheet]
    model_height: float
    warnings: list[str] = field(default_factory=list)
    waffle: WaffleResult | None = None
    plates: PlatesInfo | None = None
    model_extents: tuple = ()
    transform: np.ndarray = field(default_factory=lambda: np.eye(4))  # original -> preparado
    files: list[str] = field(default_factory=list)


def run(mesh: trimesh.Trimesh, cfg: SliceConfig, nest_parts: bool = True) -> Result:
    """Executa o pipeline. Com nest_parts=False pula o nesting (prévia rápida);
    depois é possível chamar `nest_result`."""
    cfg.validate()
    mesh, warnings = prepare_mesh(mesh, cfg)
    if cfg.mode == "waffle":
        result = _run_waffle(mesh, cfg, warnings)
    elif cfg.mode == "plates":
        result = _run_plates(mesh, cfg, warnings)
    else:
        result = _run_stack(mesh, cfg, warnings)
    result.transform = mesh.metadata["lasercut_transform"]
    result.model_extents = tuple(float(v) for v in mesh.extents)
    if nest_parts:
        nest_result(result, cfg)
    return result


def nest_result(result: Result, cfg: SliceConfig) -> Result:
    result.sheets = nest(result.parts, cfg)
    return result


def _run_stack(mesh: trimesh.Trimesh, cfg: SliceConfig, warnings: list[str]) -> Result:
    layers = slice_mesh(mesh, cfg)

    empty = [l.index + 1 for l in layers if not l.islands]
    if empty:
        warnings.append(f"Camadas vazias (sem área útil): {empty}. "
                        "Há partes soltas/flutuantes no modelo ou detalhes finos demais.")

    pins = place_pins(layers, cfg)
    apply_pin_holes(layers, pins, cfg)
    if cfg.pins:
        unpinned = [l.index + 1 for l in layers[1:]
                    if l.islands and not any(p.spans(l.index) for p in pins)]
        if unpinned:
            warnings.append(f"Camadas sem pino de alinhamento (contato pequeno demais): "
                            f"{unpinned}. Alinhe-as visualmente.")

    parts, part_warnings = build_parts(layers, cfg)
    warnings += part_warnings
    return Result(layers=layers, pins=pins, parts=parts, sheets=[],
                  model_height=float(mesh.extents[2]), warnings=warnings)


def _run_waffle(mesh: trimesh.Trimesh, cfg: SliceConfig, warnings: list[str]) -> Result:
    wr = build_waffle(mesh, cfg)
    warnings += wr.warnings
    return Result(layers=[], pins=[], parts=wr.parts, sheets=[],
                  model_height=float(mesh.extents[2]), warnings=warnings, waffle=wr)


def _run_plates(mesh: trimesh.Trimesh, cfg: SliceConfig, warnings: list[str]) -> Result:
    plates, face_region = find_plates(mesh, cfg)
    if not plates:
        raise ValueError("O modelo não tem faces planas grandes o suficiente para virar "
                         "placas. Use o modo 'stack' ou 'waffle'.")
    joints = find_joints(mesh, plates, face_region)
    extra, sharp_edges, sharp_warn = sharpen(mesh, plates, face_region, cfg)
    if sharp_edges:
        extend_joints(joints, plates, cfg.max_fillet_radius)
    joints += extra
    warnings += sharp_warn
    warnings += apply_joints(plates, joints, cfg)

    skins: list[Skin] = []
    covered = face_region != -1      # placas + arredondamentos absorvidos
    if cfg.skins:
        skins, skin_cov, skin_warn = find_skins(mesh, face_region, plates, cfg)
        warnings += skin_warn
        covered = covered | skin_cov
    cov = float(mesh.area_faces[covered].sum() / mesh.area)

    use_ribs, reason = want_ribs(mesh, cov, cfg)
    skin_solids = [sk.solid(cfg.thickness) for sk in skins]
    ribs, spacing, n_tabs = (build_ribs(mesh, plates, cfg, warnings, skin_solids)
                             if use_ribs else (None, 0.0, 0))
    if cov < 0.98 and ribs is None:
        warnings.append(f"{100 * (1 - cov):.0f}% da superfície não é plana e ficará aberta. "
                        "Ligue as nervuras (--ribs on) para dar forma e estrutura a ela.")

    parts: list[Part] = []
    for P in plates:
        shape = P.shape
        if P.holes:
            shape = shape.difference(unary_union(P.holes))
        P.shape = shape = clean_geom(shape)
        islands = clean_shape(shape, cfg)
        multi = len(islands) > 1
        for k, island in enumerate(islands):
            name = P.name + (f"-{k + 1}" if multi else "")
            part = Part(name=name, layer_index=P.id, island_index=k, shape=island)
            if cfg.labels:
                part.label = place_label(island, name, cfg)
                if part.label is None:
                    warnings.append(f"Peça {name}: pequena demais para gravar a etiqueta.")
            parts.append(part)
    for sk in skins:
        part = Part(name=sk.name, layer_index=1000 + sk.id, island_index=0, shape=sk.shape,
                    cut_lines=sk.cut_lines)
        if cfg.labels:
            avoid = sk.cut_lines.buffer(0.8) if sk.cut_lines is not None else None
            part.label = place_label(sk.shape, sk.name, cfg, avoid=avoid)
            if part.label is None:
                # reserva uma pequena área sem cortes flexíveis para a etiqueta
                part.label = place_label(sk.shape, sk.name, cfg)
                if part.label is not None and part.cut_lines is not None:
                    keep = box(*part.label.bounds).buffer(1.5, join_style="mitre")
                    rest = part.cut_lines.difference(keep)
                    part.cut_lines = MultiLineString(
                        [g for g in getattr(rest, "geoms", [rest])
                         if isinstance(g, LineString) and g.length > 1.0]) or None
            if part.label is None:
                warnings.append(f"Peça {sk.name}: sem espaço para a etiqueta (marque à mão).")
        parts.append(part)
    if ribs is not None:
        parts += ribs.parts

    info = PlatesInfo(plates=plates, joints=joints, coverage=cov, ribs=ribs,
                      ribs_reason=reason, rib_spacing=spacing, n_tabs=n_tabs, skins=skins,
                      sharp_edges=sharp_edges)
    return Result(layers=[], pins=[], parts=parts, sheets=[],
                  model_height=float(mesh.extents[2]), warnings=warnings, plates=info)


def build_preview(result: Result, cfg: SliceConfig):
    return assembled_mesh(result, cfg)


def export_result(result: Result, cfg: SliceConfig, out_dir: str, stem: str,
                  source: str | None = None, formats: tuple[str, ...] = ("svg", "dxf"),
                  preview: bool = True) -> list[str]:
    """Grava chapas (SVG/DXF), prévia STL e relatório. Faz o nesting se faltar."""
    if not result.sheets and result.parts:
        nest_result(result, cfg)
    os.makedirs(out_dir, exist_ok=True)
    files: list[str] = []
    for sheet in result.sheets:
        base = os.path.join(out_dir, f"{stem}_chapa{sheet.index + 1:02d}")
        if "svg" in formats:
            with open(base + ".svg", "w", encoding="utf-8") as f:
                f.write(sheet_to_svg(sheet))
            files.append(base + ".svg")
        if "dxf" in formats:
            sheet_to_dxf(sheet, base + ".dxf")
            files.append(base + ".dxf")

    if preview:
        pm = build_preview(result, cfg)
        if pm is not None:
            p = os.path.join(out_dir, f"{stem}_previa.stl")
            pm.export(p)
            files.append(p)

    report = os.path.join(out_dir, f"{stem}_relatorio.md")
    write_report(report, source=source or stem, cfg=cfg, result=result)
    files.append(report)
    result.files = files
    return files


def run_file(path: str, cfg: SliceConfig, out_dir: str,
             formats: tuple[str, ...] = ("svg", "dxf"), preview: bool = True) -> Result:
    result = run(load_mesh(path), cfg)
    stem = os.path.splitext(os.path.basename(path))[0]
    export_result(result, cfg, out_dir, stem, source=path, formats=formats, preview=preview)
    return result
