"""Workbench 3D2LaserCut para FreeCAD.

Interface (painel, prévia 3D, chapas) sobre o núcleo `laserslicer`, que fica
na raiz do repositório e continua utilizável sem o FreeCAD.
"""
import os
import sys

ICONPATH = os.path.join(os.path.dirname(__file__), "resources", "icons")
ICON = os.path.join(ICONPATH, "lasercut.svg")

# raiz do repositório (onde está o pacote `laserslicer`)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
