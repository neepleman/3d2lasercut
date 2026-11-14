"""Testes da v0.7: nível de detalhe, paredes finas e junções em T no modo placas."""
import itertools

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, Polygon, box as sbox

from laserslicer import SliceConfig, run
from laserslicer.cli import main
from laserslicer.detail import apply_detail, detail_values
from laserslicer.session import Session
from laserslicer.slicer import clean_shape

pytest.importorskip("manifold3d")

T = 3.0


def cube(x0, y0, z0, x1, y1, z1):
    b = trimesh.creation.box(extents=[x1 - x0, y1 - y0, z1 - z0])
    b.apply_translation([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
    return b


def detailed_block():
    """Bloco 160x100x60 com miudezas de impressão 3D: ressalto e rebaixo rasos,
    degrau numa borda e um encaixe pequeno."""
    m = cube(0, 0, 0, 160, 100, 60)
    m = m.union(cube(40, 30, 60, 80, 60, 62))           # ressalto de 2 mm no topo
    m = m.difference(cube(100, -1, 20, 130, 2, 40))     # rebaixo de 2 mm na frente
    m = m.difference(cube(-1, 70, 50, 12, 101, 61))     # degrau de 10 mm (11x30) na quina
    m = m.difference(cube(140, 40, 57, 150, 50, 61))    # encaixe pequeno 10x10x3
    return m


def thin_wall_box(W=120, D=80, H=50, wall=2.0, divider=True):
    m = cube(0, 0, 0, W, D, H).difference(cube(wall, wall, wall, W - wall, D - wall, H + 1))
    if divider:
        m = m.union(cube(W / 2 - wall / 2, wall, wall, W / 2 + wall / 2, D - wall, H - 6))
    return m


def overlaps(s):
    out = []
    for a, b in itertools.combinations(s.items, 2):
        if np.all(a.mesh.bounds[0] <= b.mesh.bounds[1] + 1e-6) and \
                np.all(b.mesh.bounds[0] <= a.mesh.bounds[1] + 1e-6):
            v = a.mesh.intersection(b.mesh).volume
            if v > 1e-3:
                out.append((a.name, b.name, round(v, 2)))
    return out


def cfg(**kw):
    base = dict(mode="plates", thickness=T, labels=False, kerf=0.0)
    base.update(kw)
    return SliceConfig(**base)


# ------------------------------------------------------------------ limites

def test_detail_values_are_monotonic():
    lo, mid, hi = (detail_values(d, 50000, T) for d in (0, 50, 100))
    for key in ("plate_min_area", "plate_min_width", "absorb_depth", "max_fillet_radius",
                "island_min_fraction", "min_area", "min_hole_diameter", "min_feature"):
        assert lo[key] >= mid[key] >= hi[key], key
    assert hi["absorb_depth"] == 0 and hi["min_hole_diameter"] == 0
    # proporcional ao tamanho do modelo
    assert detail_values(50, 200000, T)["plate_min_area"] > mid["plate_min_area"]


def test_explicit_options_win_over_detail():
    mesh = trimesh.creation.box(extents=[100, 100, 100])
    eff = apply_detail(SliceConfig(detail=0, max_fillet_radius=5.0), mesh)
    assert eff.max_fillet_radius == 5.0                 # mudado à mão: mantido
    assert eff.absorb_depth > 0                          # não mudado: vem do detalhe
    assert apply_detail(SliceConfig(), mesh) == SliceConfig()   # sem detalhe: nada muda


def test_clean_shape_drops_slivers_and_tiny_holes():
    c = SliceConfig(island_min_fraction=0.05, min_hole_diameter=2.0, min_area=1.0,
                    simplify=0.0)
    big = sbox(0, 0, 100, 50).difference(Point(20, 20).buffer(0.5)).difference(
        Point(60, 20).buffer(5))
    sliver = sbox(200, 0, 210, 10)                       # 100 mm² = 2% da grande
    out = clean_shape(big.union(sliver), c)
    assert len(out) == 1
    assert len(out[0].interiors) == 1                    # furo de 1 mm fechado, de 10 mm fica


# ------------------------------------------------------------------ peças

def test_less_detail_means_fewer_parts_and_no_overlaps():
    model = detailed_block()
    counts = {}
    for d in (0, 25, 50, 100):
        s = Session(model, cfg(detail=d))
        s.compute()
        assert s.result is not None, s.error
        counts[d] = len(s.result.parts)
        if d <= 50:
            assert overlaps(s) == [], d
    assert counts[0] <= counts[25] <= counts[50] <= counts[100]
    assert counts[0] < counts[100]


def test_low_detail_flattens_bosses_and_recesses_into_a_plain_box():
    r = run(detailed_block(), cfg(detail=0), nest_parts=False)
    assert len(r.plates.plates) == 6
    assert any("incorporadas" in w for w in r.warnings)


def test_thin_walls_become_single_plates_without_overlap():
    s = Session(thin_wall_box(divider=False), cfg(ribs="none"))
    s.compute()
    assert s.result is not None, s.error
    assert sum(1 for it in s.items if it.kind == "plate") == 5    # base + 4 paredes
    assert any("paredes finas" in w for w in s.result.warnings)
    assert overlaps(s) == []


def test_t_junction_divider_is_trimmed():
    s = Session(thin_wall_box(divider=True), cfg(ribs="none"))
    s.compute()
    assert s.result is not None, s.error
    assert overlaps(s) == []


def test_cli_detail(tmp_path):
    model = tmp_path / "bloco.stl"
    detailed_block().export(model)
    out = tmp_path / "o"
    assert main([str(model), "-m", "plates", "--detail", "0", "-o", str(out)]) == 0
    assert "Nível de detalhe: 0" in (out / "bloco_relatorio.md").read_text(encoding="utf-8")
