"""Testes da v0.4: a sessão interativa usada pelo painel do FreeCAD."""
import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, box as sbox

from laserslicer import SliceConfig
from laserslicer.session import Session


def arch_at(offset=(100.0, -50.0, 20.0)):
    """Arco fora da origem e 'deitado' (abertura ao longo de Y), como num documento real."""
    perfil = sbox(-60, 0, 60, 60).difference(Point(0, 0).buffer(35, quad_segs=32))
    m = trimesh.creation.extrude_polygon(perfil, 30)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    m.apply_translation(offset)
    return m


def items_bounds(s):
    lo = np.min([it.mesh.bounds[0] for it in s.items], axis=0)
    hi = np.max([it.mesh.bounds[1] for it in s.items], axis=0)
    return lo, hi


@pytest.mark.parametrize("mode", ["stack", "waffle", "plates"])
def test_preview_lands_on_original_model(mode):
    model = arch_at()
    s = Session(model, SliceConfig(mode=mode, thickness=3, labels=False, grid_x=20, grid_y=10))
    assert s.compute() is not None, s.error
    lo, hi = items_bounds(s)
    # a montagem fica dentro da caixa do modelo original (camadas arredondam a altura)
    assert np.all(lo >= model.bounds[0] - 1.6)
    assert np.all(hi <= model.bounds[1] + 1.6)
    assert np.allclose(hi - lo, model.extents, atol=6)


def test_axis_and_scale_are_undone():
    model = arch_at()
    s = Session(model, SliceConfig(mode="stack", axis="y", target_height=60, labels=False))
    s.compute()
    # o modelo tem 30 mm em Y; com altura alvo 60 a prévia sai 2x maior, mas no
    # mesmo lugar e orientação do original (ancorada no canto mínimo)
    lo, hi = items_bounds(s)
    assert np.allclose(lo, model.bounds[0], atol=1.6)
    assert (hi - lo)[1] == pytest.approx(60, abs=1.6)
    # as camadas se empilham ao longo de Y (o eixo escolhido)
    layer = next(it for it in s.items if it.kind == "layer")
    assert np.allclose(np.abs(layer.normal), [0, 1, 0])


def test_explode_moves_parts_without_touching_meshes():
    s = Session(arch_at(), SliceConfig(mode="plates", thickness=3, labels=False,
                                       ribs="on", rib_spacing=30))
    s.compute()
    before = [it.mesh.bounds.copy() for it in s.items]
    assert all(np.allclose(o, 0) for o in s.offsets(0))
    offs = s.offsets(1.0)
    assert len(offs) == len(s.items)
    for it, off in zip(s.items, offs):
        if it.kind == "plate":
            assert np.dot(off, it.normal) > 0          # placas saem para fora
    assert all(np.allclose(b, it.mesh.bounds) for b, it in zip(before, s.items))


def test_options_invalidate_result_only_when_changed():
    s = Session(arch_at(), SliceConfig(mode="plates", labels=False))
    s.compute()
    assert s.result is not None
    assert s.set_options(thickness=3.0) is False and s.result is not None
    assert s.set_options(thickness=4.0) is True and s.result is None
    with pytest.raises(KeyError):
        s.set_options(espessura=3)


def test_showcase_plate_loses_its_tab_holes():
    model = trimesh.creation.box(extents=[200, 120, 100])
    s = Session(model, SliceConfig(mode="plates", labels=False))
    s.compute()
    rows = {r.name: r for r in s.plate_rows()}
    target = next(n for n, r in rows.items() if r.has_tabs)
    assert s.set_showcase(target, True)
    s.compute()
    rows = {r.name: r for r in s.plate_rows()}
    assert rows[target].showcase and not rows[target].has_tabs
    s.set_showcase(target, False)
    s.compute()
    assert {r.name: r for r in s.plate_rows()}[target].has_tabs


def test_error_is_reported_not_raised():
    s = Session(trimesh.creation.icosphere(subdivisions=3, radius=30),
                SliceConfig(mode="plates"))
    assert s.compute() is None
    assert "faces planas" in s.error
    assert s.summary().startswith("Erro")
    assert s.items == []


def test_sheets_drawn_beside_the_model():
    model = arch_at()
    s = Session(model, SliceConfig(mode="waffle", grid_x=20, grid_y=10,
                                   sheet_width=300, sheet_height=200))
    s.compute()
    sheets = s.nest()
    assert sheets
    lines = s.sheet_polylines()
    roles = {pl.role for pl in lines}
    assert roles == {"sheet", "cut", "engrave"}
    assert sum(pl.role == "sheet" for pl in lines) == len(sheets)
    xmin = min(pl.points[:, 0].min() for pl in lines)
    assert xmin > model.bounds[1][0]                     # à direita do modelo
    assert all(np.allclose(pl.points[:, 2], model.bounds[0][2], atol=1.6) for pl in lines)
    assert "Chapas de 300 x 200 mm" in s.summary()


def test_export_from_session(tmp_path):
    s = Session(arch_at(), SliceConfig(mode="plates", labels=True))
    files = s.export(str(tmp_path), "arco")
    names = sorted(p.split("/")[-1].split("\\")[-1] for p in files)
    assert "arco_chapa01.svg" in names and "arco_relatorio.md" in names
    assert (tmp_path / "arco_previa.stl").exists()
