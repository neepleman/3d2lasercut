"""Testes da v0.3: placas com finger joints, chapas flexíveis e nervuras internas.

As verificações de interferência usam booleanas 3D (manifold3d): todas as
peças são montadas em 3D e nenhuma pode invadir outra nem sair do modelo.
"""
import itertools

import ezdxf
import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, Polygon, box as sbox
from shapely.ops import unary_union

from laserslicer import SliceConfig, run
from laserslicer.cli import main
from laserslicer.mesh_io import prepare_mesh
from laserslicer.plates import (apply_joints, find_joints, find_plates, finger_count,
                                plate_solid)
from laserslicer.waffle import waffle_preview

pytest.importorskip("manifold3d")


def cfg(**kw):
    base = dict(mode="plates", thickness=3, slot_clearance=0.1, kerf=0.0, labels=False)
    base.update(kw)
    return SliceConfig(**base)


def arch():
    perfil = sbox(-60, 0, 60, 60).difference(Point(0, 0).buffer(35, quad_segs=32))
    m = trimesh.creation.extrude_polygon(perfil, 30)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    return m


def step_l():
    a = trimesh.creation.box(extents=[80, 40, 20])
    a.apply_translation([40, 20, 10])
    b = trimesh.creation.box(extents=[30, 40, 30])
    b.apply_translation([65, 20, 35])
    return trimesh.boolean.union([a, b])


def solids(result, c):
    pi = result.plates
    out = [(P.name, plate_solid(P, c)) for P in pi.plates]
    out += [(sk.name, sk.solid(c.thickness)) for sk in pi.skins]
    if pi.ribs is not None:
        out += [(p.name, waffle_preview([pl], c.thickness))
                for p, pl in zip(pi.ribs.parts, pi.ribs.placements)]
    return [(n, s) for n, s in out if s is not None]


def assert_no_overlap(items, tol=1e-3):
    for (na, a), (nb, b) in itertools.combinations(items, 2):
        if not (np.all(a.bounds[0] <= b.bounds[1] + 1e-6)
                and np.all(b.bounds[0] <= a.bounds[1] + 1e-6)):
            continue
        v = a.intersection(b).volume
        assert v < tol, f"{na} invade {nb}: {v:.3f} mm³"


def assert_inside(items, model, tol=0.5):
    for name, s in items:
        out = s.difference(model).volume
        assert out < tol, f"{name} sai do modelo: {out:.3f} mm³"


# ------------------------------------------------------------------ placas

def test_finger_count_is_odd():
    c = cfg()
    for L in (5, 9, 18, 40, 61, 200):
        n = finger_count(L, c)
        assert n >= 1 and n % 2 == 1


def test_box_plates_fill_the_shell_exactly():
    c = cfg(slot_clearance=0.0)
    mesh, _ = prepare_mesh(trimesh.creation.box(extents=[60, 40, 30]), c)
    plates, fr = find_plates(mesh, c)
    joints = find_joints(mesh, plates, fr)
    apply_joints(plates, joints, c)
    assert len(plates) == 6 and len(joints) == 12
    assert all(j.kind == "finger_convex" and j.fingers >= 3 for j in joints)
    S = [(P.name, plate_solid(P, c)) for P in plates]
    assert_no_overlap(S)
    union = trimesh.boolean.union([s for _, s in S])
    assert union.volume == pytest.approx(60 * 40 * 30 - 54 * 34 * 24, abs=0.5)


def test_concave_corner_fingers_without_overlap():
    c = cfg(slot_clearance=0.0, ribs="none")
    model = step_l()
    r = run(model, c)
    kinds = {j.kind for j in r.plates.joints}
    assert "finger_concave" in kinds
    S = solids(r, c)
    assert_no_overlap(S)
    prepared, _ = prepare_mesh(model, c)
    assert_inside(S, prepared)


def test_slanted_faces_use_butt_joints():
    c = cfg(ribs="none")
    model = trimesh.creation.extrude_polygon(Polygon([(0, 0), (80, 0), (60, 40), (20, 40)]), 50)
    r = run(model, c)
    kinds = [j.kind for j in r.plates.joints]
    assert kinds.count("butt_convex") == 4
    S = solids(r, c)
    assert_no_overlap(S)
    prepared, _ = prepare_mesh(model, c)
    assert_inside(S, prepared)


def test_labels_engraved_on_inner_face():
    """O desenho de cada placa é visto de dentro: (u x v) aponta para dentro."""
    c = cfg()
    mesh, _ = prepare_mesh(trimesh.creation.box(extents=[60, 40, 30]), c)
    plates, _ = find_plates(mesh, c)
    for P in plates:
        e1, e2 = P.M[0, :3], P.M[1, :3]
        assert np.dot(np.cross(e1, e2), P.normal) == pytest.approx(-1)


# ------------------------------------------------------------------ nervuras

def test_big_box_gets_ribs_with_tabs_through_plates():
    c = cfg()
    model = trimesh.creation.box(extents=[200, 120, 100])
    r = run(model, c)
    pi = r.plates
    assert pi.ribs is not None and pi.n_tabs > 0
    S = solids(r, c)
    assert_no_overlap(S)
    prepared, _ = prepare_mesh(model, c)
    assert_inside(S, prepared)
    # as abas realmente entram nas placas: a nervura invade a placa *sem* furos
    holed = [P for P in pi.plates if P.holes]
    assert holed
    ribs = [s for n, s in S if n[0] in "XY"]
    for P in holed:
        full = plate_solid(P, c, shape=unary_union([P.shape, *[h.buffer(0.01) for h in P.holes]]))
        assert sum(full.intersection(rb).volume for rb in ribs) > 1.0, P.name


def test_no_tabs_option_keeps_face_clean():
    c = cfg(no_tab_plates=("P1", "P2"))
    r = run(trimesh.creation.box(extents=[200, 120, 100]), c)
    by_name = {P.name: P for P in r.plates.plates}
    assert not by_name["P1"].holes and not by_name["P2"].holes
    assert any(P.holes for P in r.plates.plates)


def test_small_flat_box_needs_no_ribs():
    r = run(trimesh.creation.box(extents=[60, 40, 30]), cfg())
    assert r.plates.ribs is None


# ------------------------------------------------------------------ chapas flexíveis

def test_arch_vault_gets_flexible_skin():
    c = cfg(rib_spacing=30, ribs="on")
    model = arch()
    r = run(model, c)
    pi = r.plates
    assert pi.coverage == pytest.approx(1.0, abs=0.01)
    assert len(pi.skins) == 1
    sk = pi.skins[0]
    assert sorted(sk.end_plates) == ["P1", "P2"]           # frente e fundo
    assert sk.cut_lines is not None and len(sk.cut_lines.geoms) > 20
    assert 25 < sk.min_radius < 40                          # abóbada de raio 35
    # comprimento planificado ~ meia-volta (pi * 35), menos os recortes nas pontas
    x0, _, x1, _ = sk.shape.bounds
    assert 95 < x1 - x0 < np.pi * 35 + 1
    S = solids(r, c)
    assert_no_overlap(S)
    prepared, _ = prepare_mesh(model, c)
    assert_inside(S, prepared)


def test_sphere_without_flat_faces_is_rejected():
    with pytest.raises(ValueError, match="faces planas"):
        run(trimesh.creation.icosphere(subdivisions=3, radius=40), cfg())


def test_cli_plates(tmp_path):
    model = tmp_path / "arco.stl"
    arch().export(model)
    out = tmp_path / "p"
    assert main([str(model), "-m", "plates", "--ribs", "30", "--no-tabs", "P1,P2",
                 "-o", str(out)]) == 0
    report = (out / "arco_relatorio.md").read_text(encoding="utf-8")
    assert "## Placas" in report and "## Chapas flexíveis" in report
    assert "abas em: nenhuma" in report
    doc = ezdxf.readfile(out / "arco_chapa01.dxf")
    open_cuts = [e for e in doc.modelspace()
                 if e.dxf.layer == "CORTE" and e.dxftype() == "LWPOLYLINE" and not e.closed]
    assert len(open_cuts) > 20  # cortes do living hinge


@pytest.mark.parametrize("label,model,n_skins", [
    ("caixa arredondada",
     lambda: trimesh.creation.extrude_polygon(
         sbox(0, 0, 160, 90).buffer(-20).buffer(20, quad_segs=16), 70), 4),
    ("cilindro", lambda: trimesh.creation.cylinder(radius=40, height=80, sections=96), 1),
])
def test_curved_skins_fit_without_collisions(label, model, n_skins):
    c = cfg()
    m = model()
    r = run(m, c)
    assert len(r.plates.skins) == n_skins
    assert r.plates.coverage == pytest.approx(1.0, abs=0.01)
    S = solids(r, c)
    assert_no_overlap(S)
    prepared, _ = prepare_mesh(m, c)
    assert_inside(S, prepared)


def test_cylinder_skin_length_uses_concave_side():
    r = run(trimesh.creation.cylinder(radius=40, height=80, sections=96), cfg())
    x0, _, x1, _ = r.plates.skins[0].shape.bounds
    # face visível convexa (r = 40) -> o lado que não estica é o de dentro (r = 37)
    assert x1 - x0 == pytest.approx(2 * np.pi * 37, rel=0.01)
