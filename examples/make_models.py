"""Gera modelos de teste em examples/models/."""
import os

import numpy as np
import trimesh

out = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(out, exist_ok=True)

# esfera: forma orgânica clássica
trimesh.creation.icosphere(subdivisions=4, radius=40).export(os.path.join(out, "esfera.stl"))

# toro: testa furos internos e camadas com duas ilhas
trimesh.creation.torus(major_radius=45, minor_radius=15).export(os.path.join(out, "toro.stl"))

# "vaso": sólido de revolução com perfil ondulado
z = np.linspace(0, 120, 60)
r = 25 + 10 * np.sin(z / 18) + z * 0.1
profile = np.column_stack([r, z])
profile = np.vstack([[0, 0], profile, [0, 120]])
trimesh.creation.revolve(profile, sections=96).export(os.path.join(out, "vaso.stl"))

# arco: camadas de baixo têm 2 ilhas (pernas), as de cima 1 -> testa sufixos e pinos
from shapely.geometry import Point, box  # noqa: E402

perfil = box(-60, 0, 60, 60).difference(Point(0, 0).buffer(35, quad_segs=32))
arco = trimesh.creation.extrude_polygon(perfil, 30)          # perfil no plano XY
arco.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))  # Y -> Z
arco.export(os.path.join(out, "arco.stl"))

# dois corpos sobrepostos num mesmo STL (comum em exportações): não podem virar buraco
a = trimesh.creation.box(extents=[60, 40, 20])
b = trimesh.creation.box(extents=[40, 60, 20]); b.apply_translation([10, 10, 0])
trimesh.util.concatenate([a, b]).export(os.path.join(out, "sobrepostos.stl"))

print("modelos em", out)
