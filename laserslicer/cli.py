"""Interface de linha de comando."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import SliceConfig
from .pipeline import run_file


def _sheet(value: str) -> tuple[float, float]:
    try:
        w, h = value.lower().replace("×", "x").split("x")
        return float(w), float(h)
    except ValueError:
        raise argparse.ArgumentTypeError("use o formato LARGURAxALTURA, ex.: 600x400")


def build_parser() -> argparse.ArgumentParser:
    d = SliceConfig()
    p = argparse.ArgumentParser(
        prog="3d2lasercut",
        description="Fatia um modelo 3D (STL/OBJ/3MF/PLY) em camadas empilháveis "
                    "para corte a laser em MDF/acrílico.",
    )
    p.add_argument("input", help="arquivo do modelo 3D")
    p.add_argument("-o", "--out", default="saida", help="pasta de saída (padrão: saida)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    g = p.add_argument_group("material e escala")
    g.add_argument("-t", "--thickness", type=float, default=d.thickness,
                   help=f"espessura da chapa em mm (padrão {d.thickness})")
    g.add_argument("-a", "--axis", choices="xyz", default=d.axis,
                   help="eixo de empilhamento do modelo (padrão z)")
    sc = g.add_mutually_exclusive_group()
    sc.add_argument("-s", "--scale", type=float, default=d.scale, help="fator de escala")
    sc.add_argument("-H", "--height", type=float, dest="target_height",
                    help="altura final desejada (mm) ao longo do eixo; ajusta a escala")
    g.add_argument("-k", "--kerf", type=float, default=d.kerf,
                   help=f"largura do corte do laser em mm (padrão {d.kerf})")

    g = p.add_argument_group("limpeza")
    g.add_argument("--min-area", type=float, default=d.min_area,
                   help=f"descarta ilhas menores que isso em mm² (padrão {d.min_area})")
    g.add_argument("--min-feature", type=float, default=d.min_feature,
                   help="remove detalhes mais finos que isso em mm (padrão: desligado)")

    g = p.add_argument_group("pinos de alinhamento")
    g.add_argument("--no-pins", action="store_true", help="não gerar furos de pino")
    g.add_argument("--pin-diameter", type=float, default=d.pin_diameter,
                   help=f"diâmetro do pino em mm (padrão {d.pin_diameter})")
    g.add_argument("--pin-clearance", type=float, default=d.pin_clearance,
                   help=f"folga do furo em mm (padrão {d.pin_clearance})")
    g.add_argument("--pin-margin", type=float, default=d.pin_margin,
                   help=f"parede mínima entre furo e borda em mm (padrão {d.pin_margin})")

    g = p.add_argument_group("etiquetas")
    g.add_argument("--no-labels", action="store_true", help="não gravar números nas peças")
    g.add_argument("--label-height", type=float, default=d.label_height,
                   help=f"altura máxima do número gravado em mm (padrão {d.label_height})")

    g = p.add_argument_group("chapa")
    g.add_argument("--sheet", type=_sheet, default=(d.sheet_width, d.sheet_height),
                   help="tamanho da chapa LARGURAxALTURA em mm (padrão 600x400)")
    g.add_argument("--margin", type=float, default=d.sheet_margin,
                   help=f"margem da chapa em mm (padrão {d.sheet_margin})")
    g.add_argument("--spacing", type=float, default=d.part_spacing,
                   help=f"espaço entre peças em mm (padrão {d.part_spacing})")
    g.add_argument("--no-rotate", action="store_true", help="não girar peças no nesting")

    g = p.add_argument_group("saída")
    g.add_argument("--format", default="svg,dxf", help="formatos: svg, dxf ou svg,dxf")
    g.add_argument("--no-preview", action="store_true", help="não gerar o STL de prévia")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = SliceConfig(
        thickness=args.thickness, axis=args.axis, scale=args.scale,
        target_height=args.target_height, kerf=args.kerf,
        min_area=args.min_area, min_feature=args.min_feature,
        pins=not args.no_pins, pin_diameter=args.pin_diameter,
        pin_clearance=args.pin_clearance, pin_margin=args.pin_margin,
        labels=not args.no_labels, label_height=args.label_height,
        sheet_width=args.sheet[0], sheet_height=args.sheet[1],
        sheet_margin=args.margin, part_spacing=args.spacing,
        allow_rotation=not args.no_rotate,
    )
    formats = tuple(f.strip().lower() for f in args.format.split(",") if f.strip())
    try:
        result = run_file(args.input, cfg, args.out, formats=formats,
                          preview=not args.no_preview)
    except (ValueError, OSError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1

    print(f"{len(result.layers)} camadas, {len(result.parts)} peças, "
          f"{len(result.sheets)} chapa(s), {len(result.pins)} pinos.")
    for w in result.warnings[:10]:
        print(f"  aviso: {w}")
    if len(result.warnings) > 10:
        print(f"  ... mais {len(result.warnings) - 10} avisos no relatório.")
    print("Arquivos gerados:")
    for f in result.files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
