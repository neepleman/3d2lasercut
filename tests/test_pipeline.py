import math

import ezdxf
import pytest
import trimesh
from shapely.geometry import Point
from shapely.geometry import box as sbox
from shapely.ops import unary_union

from laserslicer import SliceConfig, run
from laserslicer.cli import main
from laserslicer.font import render_text
from laserslicer.nesting import nest
from laserslicer.parts import Part


def box(x, y, z):
    return trimesh.creation.box(extents=[x, y, z])


def cfg(**kw):
    base = dict(pins=False, labels=False, kerf=0.0, simplify=0.0)
    base.update(kw)
    return SliceConfig(**base)


def test_box_layers_and_area():
    r = run(box(40, 20, 30), cfg(thickness=3))
    assert len(r.layers) == 10
    for layer in r.layers:
        assert len(layer.islands) == 1
        assert layer.islands[0].area == pytest.approx(800, rel=1e-6)


@pytest.mark.parametrize("axis,expected", [("x", 3), ("y", 7), ("z", 10)])
def test_axis_selection(axis, expected):
    r = run(box(10, 20, 30), cfg(thickness=3, axis=axis))
    assert len(r.layers) == expected


def test_target_height_scales_model():
    r = run(box(10, 10, 30), cfg(thickness=3, target_height=60))
    assert r.model_height == pytest.approx(60)
    assert len(r.layers) == 20
    assert r.layers[0].islands[0].area == pytest.approx(400, rel=1e-6)


def test_torus_middle_layer_has_hole():
    t = trimesh.creation.torus(major_radius=30, minor_radius=10)
    r = run(t, cfg(thickness=2))
    mid = r.layers[len(r.layers) // 2]
    assert len(mid.islands) == 1
    assert len(mid.islands[0].interiors) == 1


def test_overlapping_bodies_are_unioned_not_xored():
    a = box(60, 40, 20)
    b = box(40, 60, 20)
    b.apply_translation([10, 10, 0])
    mesh = trimesh.util.concatenate([a, b])
    r = run(mesh, cfg(thickness=4))
    expected = unary_union([sbox(-30, -20, 30, 20), sbox(-10, -20, 30, 40)]).area
    for layer in r.layers:
        assert len(layer.islands) == 1
        assert layer.islands[0].area == pytest.approx(expected, rel=1e-6)
        assert len(layer.islands[0].interiors) == 0


def test_pins_align_and_respect_margin():
    c = cfg(thickness=3, pins=True, pin_diameter=3, pin_margin=2)
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=30)
    r = run(sphere, c)
    assert r.pins
    for pin in r.pins:
        assert pin.n_layers >= 2
        hole = Point(pin.x, pin.y).buffer(c.pin_hole_radius)
        for li in range(pin.first_layer, pin.last_layer + 1):
            layer = r.layers[li]
            # o furo está dentro do contorno da peça, com parede mínima
            outer = unary_union([p.exterior.convex_hull for p in layer.islands])
            assert hole.buffer(c.pin_margin - 1e-3).within(outer)
            # e o furo foi efetivamente aberto
            assert not any(isl.contains(Point(pin.x, pin.y)) for isl in layer.islands)
    # cada camada (exceto possivelmente as pontas finas) tem ao menos 1 pino
    pinned = {li for p in r.pins for li in range(p.first_layer, p.last_layer + 1)}
    assert len(pinned) >= len(r.layers) - 4


def test_two_pins_per_contact_region_prevent_rotation():
    r = run(box(60, 40, 30), cfg(thickness=3, pins=True))
    assert len(r.pins) == 2
    a, b = r.pins
    assert (a.first_layer, a.last_layer) == (0, len(r.layers) - 1)
    assert math.dist((a.x, a.y), (b.x, b.y)) > 15


def test_kerf_compensation():
    part = Part(name="1", layer_index=0, island_index=0,
                shape=Point(0, 0).buffer(20).difference(Point(0, 0).buffer(5)))
    cut = part.cut_shape(0.2)
    minx, _, maxx, _ = cut.bounds
    assert (maxx - minx) == pytest.approx(40.2, abs=0.01)  # contorno cresce kerf/2
    hole = cut.interiors[0]
    hx0, _, hx1, _ = hole.bounds
    assert (hx1 - hx0) == pytest.approx(9.8, abs=0.01)     # furo encolhe kerf/2


def test_nesting_no_overlap_inside_sheet():
    c = cfg(sheet_width=200, sheet_height=150, sheet_margin=5, part_spacing=2, kerf=0.15)
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=25)
    r = run(sphere, cfg(thickness=3, sheet_width=200, sheet_height=150,
                        sheet_margin=5, part_spacing=2, kerf=0.15))
    assert len(r.sheets) >= 2
    for sheet in r.sheets:
        cuts = [pp.cut for pp in sheet.placed]
        for i, a in enumerate(cuts):
            minx, miny, maxx, maxy = a.bounds
            assert minx >= c.sheet_margin - 1e-6 and miny >= c.sheet_margin - 1e-6
            assert maxx <= c.sheet_width - c.sheet_margin + 1e-6
            assert maxy <= c.sheet_height - c.sheet_margin + 1e-6
            for b in cuts[i + 1:]:
                assert a.distance(b) >= c.part_spacing - 1e-6
    assert sum(len(s.placed) for s in r.sheets) == len(r.parts)


def test_part_too_big_for_sheet_raises():
    with pytest.raises(ValueError, match="não cabe"):
        run(box(300, 300, 6), cfg(sheet_width=200, sheet_height=200))


def test_labels_inside_parts():
    r = run(box(60, 40, 15), cfg(thickness=3, labels=True, pins=True))
    for sheet in r.sheets:
        for pp in sheet.placed:
            assert pp.label is not None
            assert pp.label.within(pp.cut)


def test_render_text_centered():
    t = render_text("12-3", 5, (100, 50))
    minx, miny, maxx, maxy = t.bounds
    assert (minx + maxx) / 2 == pytest.approx(100)
    assert (miny + maxy) / 2 == pytest.approx(50)
    assert maxy - miny == pytest.approx(5 * 1.25)


def test_cli_end_to_end(tmp_path):
    model = tmp_path / "cubo.stl"
    box(50, 30, 12).export(model)
    out = tmp_path / "saida"
    assert main([str(model), "-o", str(out), "-t", "3", "--sheet", "300x200"]) == 0
    files = sorted(p.name for p in out.iterdir())
    assert files == ["cubo_chapa01.dxf", "cubo_chapa01.svg", "cubo_previa.stl",
                     "cubo_relatorio.md"]
    doc = ezdxf.readfile(out / "cubo_chapa01.dxf")
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert {"CORTE", "GRAVACAO", "CHAPA"} <= layers
    # 4 peças, cada uma com contorno + 2 furos de pino
    assert sum(1 for e in doc.modelspace() if e.dxf.layer == "CORTE") == 4 * 3
    preview = trimesh.load(out / "cubo_previa.stl")
    assert preview.bounds[1][2] == pytest.approx(12)
    report = (out / "cubo_relatorio.md").read_text(encoding="utf-8")
    assert "4 camadas" in report


def test_cli_reports_error(tmp_path, capsys):
    model = tmp_path / "grande.stl"
    box(500, 500, 6).export(model)
    assert main([str(model), "-o", str(tmp_path / "o"), "--sheet", "100x100"]) == 1
    assert "não cabe" in capsys.readouterr().err
