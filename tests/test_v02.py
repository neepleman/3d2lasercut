"""Testes da v0.2: grade encaixada (waffle) e nesting por contorno real."""
import numpy as np
import pytest
import trimesh
from shapely.geometry import Point

from laserslicer import SliceConfig, run
from laserslicer.cli import main
from laserslicer.mesh_io import prepare_mesh
from laserslicer.nesting import nest
from laserslicer.parts import Part
from laserslicer.waffle import build_waffle, grid_positions


def cfg(**kw):
    base = dict(mode="waffle", labels=False, kerf=0.0, simplify=0.0, grid_x=20, grid_y=20)
    base.update(kw)
    return SliceConfig(**base)


def vase():
    z = np.linspace(0, 120, 60)
    r = 25 + 10 * np.sin(z / 18) + z * 0.1
    profile = np.vstack([[0, 0], np.column_stack([r, z]), [0, 120]])
    return trimesh.creation.revolve(profile, sections=96)


def waffle_of(mesh, c):
    prepared, _ = prepare_mesh(mesh, c)
    return build_waffle(prepared, c), prepared


# ------------------------------------------------------------------ waffle

def test_grid_positions_centered_with_margin():
    assert grid_positions(0, 60, 20) == pytest.approx([10, 30, 50])
    assert grid_positions(0, 10, 20) == pytest.approx([5])
    pos = grid_positions(0, 95, 25)
    assert pos[0] >= 12.5 - 1e-9 and 95 - pos[-1] >= 12.5 - 1e-9


def test_box_waffle_slots():
    c = cfg(thickness=3, slot_clearance=0.1)
    wr, _ = waffle_of(trimesh.creation.box(extents=[60, 40, 30]), c)
    names = sorted(p.name for p in wr.parts)
    assert names == ["X1", "X2", "X3", "Y1", "Y2"]
    assert wr.n_crossings == 6
    w = c.slot_width
    for p in wr.parts:
        if p.name.startswith("X"):   # perfil 40 x 30, 2 fendas de meia altura
            assert p.shape.area == pytest.approx(40 * 30 - 2 * w * 15, rel=1e-6)
            # fendas abertas em cima (z = 30), fechadas embaixo
            assert not p.shape.contains(Point(10, 25)) and p.shape.contains(Point(10, 5))
        else:                        # perfil 60 x 30, 3 fendas
            assert p.shape.area == pytest.approx(60 * 30 - 3 * w * 15, rel=1e-6)
            assert p.shape.contains(Point(10, 25)) and not p.shape.contains(Point(10, 5))


def _assert_no_interference(wr, t):
    xs = [(poly, h) for (poly, T, h), p in zip(wr.placements, wr.parts) if p.name[0] == "X"]
    ys = [(poly, -h) for (poly, T, h), p in zip(wr.placements, wr.parts) if p.name[0] == "Y"]
    zs = np.arange(0, 200, 0.25)
    crossings = 0
    for px, a in xs:
        for py, b in ys:
            for z in zs:
                x_mat = any(px.contains(Point(b + du, z)) for du in (-0.45 * t, 0, 0.45 * t))
                y_mat = any(py.contains(Point(a + du, z)) for du in (-0.45 * t, 0, 0.45 * t))
                assert not (x_mat and y_mat), f"peças se chocam em x={a}, y={b}, z={z}"
            # e elas de fato se encaixam (há material das duas na mesma coluna)
            if any(px.distance(Point(b, z)) < 0.01 for z in zs) and \
               any(py.distance(Point(a, z)) < 0.01 for z in zs):
                crossings += 1
    assert crossings > 0


@pytest.mark.parametrize("mesh_fn", [
    lambda: trimesh.creation.icosphere(subdivisions=3, radius=40),
    vase,
    lambda: trimesh.creation.torus(major_radius=40, minor_radius=12),
])
def test_waffle_pieces_do_not_collide(mesh_fn):
    c = cfg(thickness=3)
    wr, _ = waffle_of(mesh_fn(), c)
    assert wr.n_crossings > 0
    _assert_no_interference(wr, c.thickness)


def test_split_slice_gets_slots_from_the_short_side():
    """No vaso, X1 é dividida em duas partes; a peça central Y não pode ser
    rasgada de cima a baixo para encaixar a parte de cima."""
    c = cfg(thickness=3, grid_x=25, grid_y=25)
    wr, _ = waffle_of(vase(), c)
    original = {s.name: s.profile.area for s in wr.slices}
    for p in wr.parts:
        base = p.name.split("-")[0]
        if not any(q.name.startswith(base + "-") for q in wr.parts):
            assert p.shape.area > 0.7 * original[base], p.name


def test_waffle_mate_labels_engraved():
    c = cfg(thickness=3, labels=True, grid_x=25, grid_y=25)
    wr, _ = waffle_of(trimesh.creation.box(extents=[100, 80, 60]), c)
    for p in wr.parts:
        assert p.label is not None
        assert p.label.within(p.shape)
        n_slots = 3 if p.name.startswith("X") else 4
        # nome da peça + nome da parceira ao lado de cada fenda (>= 1 traço cada)
        assert len(p.label.geoms) >= n_slots + 2


def test_waffle_too_sparse_warns():
    r = run(trimesh.creation.box(extents=[30, 30, 30]), cfg(grid_x=50, grid_y=50))
    assert r.waffle.n_crossings == 1
    r = run(trimesh.creation.box(extents=[30, 30, 30]), cfg(grid_x=50, grid_y=50,
                                                             min_slot_overlap=100))
    assert any("Nenhum cruzamento" in w for w in r.warnings)


# ------------------------------------------------------------------ nesting

def _check_sheet(sheet, c):
    cuts = [pp.cut for pp in sheet.placed]
    for i, a in enumerate(cuts):
        minx, miny, maxx, maxy = a.bounds
        assert minx >= c.sheet_margin - 1e-6 and miny >= c.sheet_margin - 1e-6
        assert maxx <= c.sheet_width - c.sheet_margin + 1e-6
        assert maxy <= c.sheet_height - c.sheet_margin + 1e-6
        for b in cuts[i + 1:]:
            assert a.distance(b) >= c.part_spacing - 1e-6


def test_shape_nesting_uses_holes():
    ring = Point(0, 0).buffer(90, quad_segs=32).difference(Point(0, 0).buffer(70, quad_segs=32))
    disk = Point(0, 0).buffer(40, quad_segs=32)
    parts = [Part("1", 0, 0, ring), Part("2", 1, 0, disk)]
    common = dict(sheet_width=200, sheet_height=200, sheet_margin=5, part_spacing=3, kerf=0.1)
    shape = nest(parts, SliceConfig(nesting="shape", **common))
    shelf = nest(parts, SliceConfig(nesting="shelf", **common))
    assert len(shape) == 1 and len(shelf) == 2
    _check_sheet(shape[0], SliceConfig(**common))
    ring_placed, disk_placed = (pp.cut for pp in shape[0].placed)
    assert ring_placed.convex_hull.contains(disk_placed)


def test_shape_nesting_beats_shelf_on_round_parts():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=30)
    common = dict(thickness=3, sheet_width=200, sheet_height=150, labels=False, pins=False)
    shape = run(sphere, SliceConfig(nesting="shape", **common))
    shelf = run(sphere, SliceConfig(nesting="shelf", **common))
    assert len(shape.sheets) <= len(shelf.sheets)
    for s in shape.sheets:
        _check_sheet(s, SliceConfig(**common))


def test_shape_nesting_respects_no_rotation():
    p = Part("1", 0, 0, Point(0, 0).buffer(5).union(Point(30, 0).buffer(5)).convex_hull)
    sheets = nest([p], SliceConfig(allow_rotation=False, kerf=0))
    minx, miny, maxx, maxy = sheets[0].placed[0].cut.bounds
    assert maxx - minx == pytest.approx(40, abs=1e-6)
    assert maxy - miny == pytest.approx(10, abs=1e-6)


def test_cli_waffle(tmp_path):
    model = tmp_path / "esfera.stl"
    trimesh.creation.icosphere(subdivisions=3, radius=40).export(model)
    out = tmp_path / "w"
    assert main([str(model), "-m", "waffle", "--grid", "20x25", "-o", str(out)]) == 0
    report = (out / "esfera_relatorio.md").read_text(encoding="utf-8")
    assert "grade encaixada" in report and "a cada 20 mm" in report and "a cada 25 mm" in report
    preview = trimesh.load(out / "esfera_previa.stl")
    assert preview.extents == pytest.approx([80, 80, 80], abs=4)
