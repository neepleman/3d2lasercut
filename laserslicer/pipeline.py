"""Pipeline completo: malha -> camadas -> pinos -> peças -> chapas -> arquivos."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import trimesh

from .config import SliceConfig
from .export import preview_mesh, sheet_to_dxf, sheet_to_svg, write_report
from .mesh_io import load_mesh, prepare_mesh
from .nesting import Sheet, nest
from .parts import Part, build_parts
from .pins import Pin, apply_pin_holes, place_pins
from .slicer import Layer, slice_mesh
from .waffle import WaffleResult, build_waffle, waffle_preview


@dataclass
class Result:
    layers: list[Layer]
    pins: list[Pin]
    parts: list[Part]
    sheets: list[Sheet]
    model_height: float
    warnings: list[str] = field(default_factory=list)
    waffle: WaffleResult | None = None
    files: list[str] = field(default_factory=list)


def run(mesh: trimesh.Trimesh, cfg: SliceConfig) -> Result:
    cfg.validate()
    mesh, warnings = prepare_mesh(mesh, cfg)
    if cfg.mode == "waffle":
        return _run_waffle(mesh, cfg, warnings)
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
    sheets = nest(parts, cfg)
    return Result(layers=layers, pins=pins, parts=parts, sheets=sheets,
                  model_height=float(mesh.extents[2]), warnings=warnings)


def _run_waffle(mesh: trimesh.Trimesh, cfg: SliceConfig, warnings: list[str]) -> Result:
    wr = build_waffle(mesh, cfg)
    warnings += wr.warnings
    sheets = nest(wr.parts, cfg)
    return Result(layers=[], pins=[], parts=wr.parts, sheets=sheets,
                  model_height=float(mesh.extents[2]), warnings=warnings, waffle=wr)


def build_preview(result: Result, cfg: SliceConfig):
    if result.waffle is not None:
        return waffle_preview(result.waffle.placements, cfg.thickness)
    return preview_mesh(result.layers, cfg)


def run_file(path: str, cfg: SliceConfig, out_dir: str,
             formats: tuple[str, ...] = ("svg", "dxf"), preview: bool = True) -> Result:
    result = run(load_mesh(path), cfg)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]

    for sheet in result.sheets:
        base = os.path.join(out_dir, f"{stem}_chapa{sheet.index + 1:02d}")
        if "svg" in formats:
            with open(base + ".svg", "w", encoding="utf-8") as f:
                f.write(sheet_to_svg(sheet))
            result.files.append(base + ".svg")
        if "dxf" in formats:
            sheet_to_dxf(sheet, base + ".dxf")
            result.files.append(base + ".dxf")

    if preview:
        pm = build_preview(result, cfg)
        if pm is not None:
            p = os.path.join(out_dir, f"{stem}_previa.stl")
            pm.export(p)
            result.files.append(p)

    report = os.path.join(out_dir, f"{stem}_relatorio.md")
    write_report(report, source=path, cfg=cfg, result=result)
    result.files.append(report)
    return result
