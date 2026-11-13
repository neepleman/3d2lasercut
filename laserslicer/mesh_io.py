"""Carregamento e preparação da malha 3D."""
from __future__ import annotations

import numpy as np
import trimesh

from .config import SliceConfig

# Rotações que levam o eixo escolhido para +Z (eixo de empilhamento interno).
_AXIS_TO_Z = {
    "z": np.eye(4),
    "x": trimesh.transformations.rotation_matrix(-np.pi / 2, [0, 1, 0]),  # +X -> +Z
    "y": trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]),   # +Y -> +Z
}


def load_mesh(path: str) -> trimesh.Trimesh:
    """Carrega STL/OBJ/PLY/3MF/GLB... como uma única malha."""
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(f"Não foi possível ler uma malha válida de '{path}'.")
    return mesh


def is_closed(mesh: trimesh.Trimesh) -> bool:
    """Todos os corpos são fechados? (vários corpos fechados num STL são válidos)"""
    if mesh.is_watertight:
        return True
    bodies = mesh.split(only_watertight=False)
    return len(bodies) > 0 and all(b.is_watertight for b in bodies)


def prepare_mesh(mesh: trimesh.Trimesh, cfg: SliceConfig) -> tuple[trimesh.Trimesh, list[str]]:
    """Orienta, escala e posiciona a malha com a base em Z=0.

    Retorna a malha preparada e uma lista de avisos para o usuário.
    """
    warnings: list[str] = []
    mesh = mesh.copy()

    if not is_closed(mesh):
        mesh.merge_vertices()
        trimesh.repair.fix_normals(mesh)
        trimesh.repair.fill_holes(mesh)
        if not is_closed(mesh):
            warnings.append(
                "A malha não é fechada (watertight); algumas fatias podem sair "
                "incompletas. Considere reparar o modelo (ex.: Meshmixer, PrusaSlicer)."
            )

    mesh.apply_transform(_AXIS_TO_Z[cfg.axis])

    extent_z = float(mesh.extents[2])
    if extent_z <= 0:
        raise ValueError("O modelo não tem altura ao longo do eixo escolhido.")
    scale = cfg.target_height / extent_z if cfg.target_height else cfg.scale
    if scale != 1.0:
        mesh.apply_scale(scale)

    mesh.apply_translation(-mesh.bounds[0])
    return mesh, warnings
