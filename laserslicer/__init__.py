"""3d2lasercut — transforma modelos 3D em peças empilháveis para corte a laser.

Copyright (C) 2026 Neepleman
Licenciado sob a GNU LGPL v2.1 ou posterior (veja LICENSE).
"""
__version__ = "0.2.0"

from .config import SliceConfig  # noqa: F401
from .pipeline import run, run_file  # noqa: F401
