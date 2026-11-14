"""Dependências Python do núcleo dentro do Python embutido do FreeCAD.

O FreeCAD traz o próprio Python (e o próprio numpy). Instalamos só o que
falta, com `--no-deps`, numa pasta do usuário que o FreeCAD já coloca no
sys.path (a mesma do Addon Manager). Assim nada do FreeCAD é sobrescrito,
em especial o numpy, do qual outros workbenches dependem.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

# (módulo para importar, nome no pip)
REQUIRED = [
    ("trimesh", "trimesh"),
    ("shapely", "shapely"),
    ("ezdxf", "ezdxf"),
    ("networkx", "networkx"),
    ("scipy", "scipy"),
    ("mapbox_earcut", "mapbox_earcut"),
    # dependências do ezdxf (instalamos com --no-deps)
    ("pyparsing", "pyparsing"),
    ("typing_extensions", "typing_extensions"),
    ("fontTools", "fonttools"),
]


def target_dir(user_dir: str | None = None) -> str:
    if user_dir is None:
        import FreeCAD as App
        user_dir = App.getUserAppDataDir()
    return os.path.join(user_dir, "AdditionalPythonPackages",
                        f"py{sys.version_info.major}{sys.version_info.minor}")


def ensure_paths(user_dir: str | None = None) -> None:
    d = target_dir(user_dir)
    if os.path.isdir(d) and d not in sys.path:
        sys.path.append(d)
        importlib.invalidate_caches()


def missing() -> list[str]:
    return [pip for mod, pip in REQUIRED if importlib.util.find_spec(mod) is None]


def numpy_problem() -> str | None:
    if importlib.util.find_spec("numpy") is None:
        return "O numpy não foi encontrado no Python do FreeCAD."
    import numpy
    major, minor = (int(x) for x in numpy.__version__.split(".")[:2])
    if (major, minor) < (1, 24):
        return f"O numpy do FreeCAD ({numpy.__version__}) é antigo demais (precisa de 1.24+)."
    return None


def python_executable() -> str:
    """O executável do Python embutido (no FreeCAD, sys.executable é o FreeCAD)."""
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("python"):
        return exe
    folder = os.path.dirname(exe)
    for name in ("python.exe", "python3", "python"):
        cand = os.path.join(folder, name)
        if os.path.isfile(cand):
            return cand
    return exe


def pip_command(packages: list[str], user_dir: str | None = None) -> list[str]:
    return [python_executable(), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-deps", "--upgrade", "--target", target_dir(user_dir), *packages]


def install(packages: list[str] | None = None, user_dir: str | None = None) -> tuple[bool, str]:
    packages = missing() if packages is None else packages
    if not packages:
        return True, "Nada a instalar."
    os.makedirs(target_dir(user_dir), exist_ok=True)
    cmd = pip_command(packages, user_dir)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, **kw)
    except Exception as e:  # pip ausente, timeout etc.
        return False, f"{e}\n\nComando:\n{' '.join(cmd)}"
    ensure_paths(user_dir)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and not missing(), out + f"\n\nComando:\n{' '.join(cmd)}"
