"""Testes da v0.6: modo caixa.

As peças de teste imitam modelos de impressão 3D: paredes com espessura
própria, furos passantes, rebaixos que não atravessam, divisórias, detalhes
internos, arestas arredondadas e vários corpos no mesmo arquivo.
"""
import itertools

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, Polygon, box as sbox

from laserslicer import SliceConfig, run
from laserslicer.box import SIDE_NAMES, snap_to_edges
from laserslicer.cli import main
from laserslicer.session import Session

pytest.importorskip("manifold3d")

T = 3.0


def cfg(**kw):
    base = dict(mode="box", thickness=T, slot_clearance=0.1, kerf=0.0, labels=False)
    base.update(kw)
    return SliceConfig(**base)


def cube(x0, y0, z0, x1, y1, z1):
    b = trimesh.creation.box(extents=[x1 - x0, y1 - y0, z1 - z0])
    b.apply_translation([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
    return b


def open_box(W=120, D=80, H=50, wall=2.5, divider_at=None, detail=True):
    """Caixa aberta em cima, paredes de impressão 3D, com furos e um detalhe interno."""
    outer = cube(0, 0, 0, W, D, H)
    inner = cube(wall, wall, wall, W - wall, D - wall, H + 1)
    m = outer.difference(inner)
    # furo passante na parede da frente (Y = 0)
    hole = trimesh.creation.cylinder(radius=6, height=20, sections=32)
    hole.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    hole.apply_translation([W / 2, 0, H / 2])
    m = m.difference(hole)
    # rebaixo de 1 mm (não atravessa) na parede do fundo (Y = D)
    m = m.difference(cube(20, D - 1.0, 15, 40, D + 1, 35))
    if detail:  # ressalto interno pequeno (encaixe de contato): deve ser ignorado
        m = m.union(cube(wall, 30, wall, wall + 4, 40, 20))
    if divider_at is not None:
        m = m.union(cube(divider_at - 1.25, wall, wall, divider_at + 1.25, D - wall, H - 5))
    return m


def items_no_overlap(s):
    for a, b in itertools.combinations(s.items, 2):
        if not (np.all(a.mesh.bounds[0] <= b.mesh.bounds[1] + 1e-6)
                and np.all(b.mesh.bounds[0] <= a.mesh.bounds[1] + 1e-6)):
            continue
        v = a.mesh.intersection(b.mesh).volume
        assert v < 1e-3, f"{a.name} invade {b.name}: {v:.3f} mm³"


def by_side(result):
    return {SIDE_NAMES[P.side]: P for P in result.plates.plates}


def test_open_box_faces_holes_and_joints():
    r = run(open_box(), cfg(), nest_parts=False)
    pi = r.plates
    assert len(pi.boxes) == 1
    faces = {SIDE_NAMES[f.side]: f.exists for f in pi.boxes[0].faces}
    assert faces == {"esquerda": True, "direita": True, "frente": True, "fundo": True,
                     "base": True, "topo": False}
    # base + 4 paredes: 4 arestas da base + 4 cantos verticais
    assert len(pi.joints) == 8 and all(j.kind == "finger_convex" for j in pi.joints)
    P = by_side(r)
    assert len(P["frente"].shape.interiors) == 1          # furo passante mantido
    assert len(P["fundo"].shape.interiors) == 0           # rebaixo de 1 mm preenchido
    hole = Polygon(P["frente"].shape.interiors[0])
    assert hole.area == pytest.approx(np.pi * 36, rel=0.05)
    # detalhe interno ignorado: só as 5 placas
    assert len(pi.plates) == 5 and pi.ribs is None


def test_box_parts_do_not_collide_and_fill_the_box():
    s = Session(open_box(divider_at=60), cfg())
    s.compute()
    items_no_overlap(s)
    lo = np.min([it.mesh.bounds[0] for it in s.items], axis=0)
    hi = np.max([it.mesh.bounds[1] for it in s.items], axis=0)
    assert np.allclose(hi - lo, [120, 80, 50], atol=0.6)


def test_divider_detected_with_tabs():
    r = run(open_box(divider_at=60), cfg(), nest_parts=False)
    pi = r.plates
    assert pi.boxes[0].dividers and pi.boxes[0].dividers[0][0] == "X"
    assert pi.boxes[0].dividers[0][1] == pytest.approx(60, abs=0.5)
    assert pi.ribs is not None and len(pi.ribs.parts) == 1
    assert pi.n_tabs > 0
    assert any(P.holes for P in pi.plates)
    assert not any("Nenhum cruzamento" in w for w in r.warnings)
    # sem divisórias quando desligado
    r2 = run(open_box(divider_at=60), cfg(dividers="none"), nest_parts=False)
    assert r2.plates.ribs is None


def test_face_override():
    r = run(open_box(), cfg(box_faces=("auto", "auto", "off", "auto", "auto", "on")),
            nest_parts=False)
    sides = set(by_side(r))
    assert "frente" not in sides and "topo" in sides
    topo = by_side(r)["topo"]
    assert topo.base.area == pytest.approx(120 * 80, rel=0.02)    # forçada: placa cheia


def test_rounded_print_box_gets_full_length_joints():
    m = open_box(detail=False)
    # arredonda as arestas externas verticais (como impressão 3D)
    r_f = 4.0
    rounded = trimesh.creation.extrude_polygon(
        sbox(0, 0, 120, 80).buffer(-r_f).buffer(r_f, quad_segs=8), 50)
    m = m.intersection(rounded)
    r = run(m, cfg(), nest_parts=False)
    lengths = sorted(round(j.length) for j in r.plates.joints)
    # as arestas vão até os cantos vivos, apesar do arredondamento
    assert lengths == [50, 50, 50, 50, 80, 80, 120, 120]


def test_two_bodies_two_boxes_unique_names():
    a = open_box(divider_at=60)
    b = open_box(W=60, D=50, H=40, divider_at=30)
    b.apply_translation([0, 150, 0])
    r = run(trimesh.util.concatenate([a, b]), cfg(), nest_parts=False)
    pi = r.plates
    assert len(pi.boxes) == 2
    names = [p.name for p in r.parts]
    assert len(names) == len(set(names))
    assert sorted(p.name for p in pi.ribs.parts) == ["X1", "X2"]


def test_snap_keeps_notches():
    rect = sbox(0, 0, 100, 60)
    face = sbox(5, 5, 95, 55).difference(Polygon([(40, 60), (50, 40), (60, 60)]))
    out = snap_to_edges(face, rect, 6)
    assert out.area == pytest.approx(100 * 60 - 200, abs=40)    # só o entalhe falta
    assert not out.contains(Point(50, 52))


def test_cli_box(tmp_path):
    model = tmp_path / "caixa.stl"
    open_box(divider_at=60).export(model)
    out = tmp_path / "o"
    assert main([str(model), "-m", "box", "--faces", "topo=on", "-o", str(out)]) == 0
    report = (out / "caixa_relatorio.md").read_text(encoding="utf-8")
    assert "## Caixa" in report and "## Divisórias" in report and "topo (" in report
    with pytest.raises(SystemExit):          # nome de face inválido: erro de argumento
        main([str(model), "-m", "box", "--faces", "teto=on", "-o", str(out)])
