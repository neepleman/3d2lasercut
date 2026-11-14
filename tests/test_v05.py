"""Testes da v0.5: remoção de arredondamentos e chanfros (cantos vivos para a laser).

Critério principal: a peça arredondada tem que virar exatamente o mesmo
conjunto de placas da versão com cantos vivos (mesmo volume, mesmas juntas,
nenhuma sobreposição).
"""
import itertools

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon, box as sbox

from laserslicer import SliceConfig, run
from laserslicer.cli import main
from laserslicer.plates import plate_solid

pytest.importorskip("manifold3d")

T = 3.0


def cfg(**kw):
    base = dict(mode="plates", thickness=T, slot_clearance=0.0, kerf=0.0, labels=False,
                ribs="none")
    base.update(kw)
    return SliceConfig(**base)


def rounded_box(W, D, H, r, subdiv=2):
    """Caixa com TODAS as arestas e cantos arredondados (como em peças de impressão 3D)."""
    sphere = trimesh.creation.icosphere(subdivisions=subdiv, radius=r)
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                pts.append(sphere.vertices + [sx * (W / 2 - r), sy * (D / 2 - r), sz * (H / 2 - r)])
    return trimesh.convex.convex_hull(np.vstack(pts))


def chamfered_box(W, D, H, c):
    """Caixa com as 12 arestas chanfradas (c mm em cada face)."""
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                x, y, z = sx * W / 2, sy * D / 2, sz * H / 2
                pts += [(x, y - sy * c, z - sz * c), (x - sx * c, y, z - sz * c),
                        (x - sx * c, y - sy * c, z)]
    return trimesh.convex.convex_hull(np.array(pts))


def vertical_rounded_box(W, D, H, r):
    return trimesh.creation.extrude_polygon(
        sbox(0, 0, W, D).buffer(-r).buffer(r, quad_segs=8), H)


def l_profile(rounded: bool, r=5.0):
    """Degrau em L (perfil no plano XZ, extrudado em Y), opcionalmente arredondado."""
    prof = Polygon([(0, 0), (100, 0), (100, 60), (60, 60), (60, 25), (0, 25)])
    if rounded:  # arredonda cantos externos e o canto interno
        prof = prof.buffer(-r, join_style="round").buffer(r, join_style="round")
        prof = prof.buffer(r, join_style="round").buffer(-r, join_style="round")
    m = trimesh.creation.extrude_polygon(prof, 40)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    return m


def plate_solids(result, c):
    return [(P.name, plate_solid(P, c)) for P in result.plates.plates]


def assert_no_overlap(items, tol=1e-3):
    for (na, a), (nb, b) in itertools.combinations(items, 2):
        if not (np.all(a.bounds[0] <= b.bounds[1] + 1e-6)
                and np.all(b.bounds[0] <= a.bounds[1] + 1e-6)):
            continue
        v = a.intersection(b).volume
        assert v < tol, f"{na} invade {nb}: {v:.3f} mm³"


def union_volume(items):
    return trimesh.boolean.union([s for _, s in items]).volume


def joint_kinds(result):
    out = {}
    for j in result.plates.joints:
        out[j.kind] = out.get(j.kind, 0) + 1
    return out


SHELL = 120 * 80 * 60 - (120 - 2 * T) * (80 - 2 * T) * (60 - 2 * T)


@pytest.mark.parametrize("label,model", [
    ("arestas verticais r=3", lambda: vertical_rounded_box(120, 80, 60, 3)),
    ("arestas verticais r=8", lambda: vertical_rounded_box(120, 80, 60, 8)),
    ("tudo arredondado r=4", lambda: rounded_box(120, 80, 60, 4)),
    ("tudo arredondado r=8", lambda: rounded_box(120, 80, 60, 8)),
    ("chanfros de 5 mm", lambda: chamfered_box(120, 80, 60, 5)),
])
def test_rounded_boxes_become_sharp_finger_jointed_boxes(label, model):
    c = cfg()
    r = run(model(), c, nest_parts=False)
    assert len(r.plates.plates) == 6, label
    assert joint_kinds(r) == {"finger_convex": 12}, label
    assert r.plates.coverage == pytest.approx(1.0, abs=0.01)
    assert not r.plates.skins
    S = plate_solids(r, c)
    assert_no_overlap(S)
    # mesmo volume da casca da caixa com cantos vivos
    assert union_volume(S) == pytest.approx(SHELL, rel=2e-3), label


def test_fillet_removal_can_be_disabled():
    r = run(vertical_rounded_box(120, 80, 60, 5), cfg(max_fillet_radius=0), nest_parts=False)
    assert joint_kinds(r) == {"finger_convex": 8}       # cantos verticais ficam abertos
    assert not r.plates.sharp_edges


def test_large_curves_are_kept_as_flexible_skins():
    # raio 20 > máximo padrão (10): continua chapa flexível, não vira canto vivo
    r = run(vertical_rounded_box(160, 90, 70, 20), cfg(), nest_parts=False)
    assert len(r.plates.skins) == 4
    assert not r.plates.sharp_edges
    # com o máximo acima do raio, viram cantos vivos
    r = run(vertical_rounded_box(160, 90, 70, 20), cfg(max_fillet_radius=25), nest_parts=False)
    assert not r.plates.skins and len(r.plates.sharp_edges) == 4


def test_rounded_step_matches_sharp_step():
    c = cfg()
    sharp = run(l_profile(False), c, nest_parts=False)
    rounded = run(l_profile(True), c, nest_parts=False)
    assert len(rounded.plates.plates) == len(sharp.plates.plates)
    assert joint_kinds(rounded) == joint_kinds(sharp)
    assert "finger_concave" in joint_kinds(rounded)       # canto interno também
    S = plate_solids(rounded, c)
    assert_no_overlap(S)
    assert union_volume(S) == pytest.approx(union_volume(plate_solids(sharp, c)), rel=3e-3)


def test_cli_max_fillet_flag(tmp_path):
    model = tmp_path / "caixa.stl"
    vertical_rounded_box(120, 80, 60, 5).export(model)
    out = tmp_path / "o"
    assert main([str(model), "-m", "plates", "--max-fillet", "0", "-o", str(out)]) == 0
    assert "8 × dedos" in (out / "caixa_relatorio.md").read_text(encoding="utf-8")
    assert main([str(model), "-m", "plates", "-o", str(out)]) == 0
    report = (out / "caixa_relatorio.md").read_text(encoding="utf-8")
    assert "12 × dedos" in report and "arredondamentos" in report
