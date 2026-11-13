"""Parâmetros de fatiamento, material e chapa."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SliceConfig:
    # --- Material / fatiamento ---
    thickness: float = 3.0          # espessura real da chapa (mm) — meça com paquímetro!
    axis: str = "z"                 # eixo de empilhamento do modelo: x, y ou z
    scale: float = 1.0              # fator de escala aplicado ao modelo
    target_height: float | None = None  # altura final ao longo do eixo (mm); sobrepõe `scale`

    # --- Limpeza de geometria ---
    min_area: float = 4.0           # descarta ilhas menores que isso (mm²)
    min_feature: float = 0.0        # remove detalhes mais finos que isso (mm); 0 = desligado
    simplify: float = 0.05          # tolerância de simplificação dos contornos (mm)

    # --- Corte a laser ---
    kerf: float = 0.15              # largura do feixe (mm); contornos são compensados em kerf/2

    # --- Pinos de alinhamento ---
    pins: bool = True
    pin_diameter: float = 3.0       # diâmetro do pino/cavilha/palito (mm)
    pin_clearance: float = 0.1      # folga no diâmetro do furo (mm)
    pin_margin: float = 2.0         # parede mínima entre furo e borda da peça (mm)
    max_pins: int = 2               # pinos por região de contato (2 impede rotação)

    # --- Etiquetas gravadas ---
    labels: bool = True
    label_height: float = 4.0       # altura máxima do texto (mm)
    label_min_height: float = 1.5   # abaixo disso a etiqueta é omitida

    # --- Chapa / nesting ---
    sheet_width: float = 600.0
    sheet_height: float = 400.0
    sheet_margin: float = 5.0
    part_spacing: float = 3.0
    allow_rotation: bool = True

    def validate(self) -> None:
        if self.thickness <= 0:
            raise ValueError("A espessura deve ser positiva.")
        if self.axis not in ("x", "y", "z"):
            raise ValueError("O eixo deve ser x, y ou z.")
        if self.scale <= 0:
            raise ValueError("A escala deve ser positiva.")
        if self.target_height is not None and self.target_height <= 0:
            raise ValueError("A altura alvo deve ser positiva.")
        if self.kerf < 0:
            raise ValueError("O kerf não pode ser negativo.")
        usable_w = self.sheet_width - 2 * self.sheet_margin
        usable_h = self.sheet_height - 2 * self.sheet_margin
        if usable_w <= 0 or usable_h <= 0:
            raise ValueError("A margem é maior que a chapa.")

    @property
    def pin_hole_radius(self) -> float:
        return (self.pin_diameter + self.pin_clearance) / 2.0
