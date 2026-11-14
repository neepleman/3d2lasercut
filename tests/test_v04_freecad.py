"""Testes do workbench do FreeCAD com Qt real (offscreen) e FreeCAD simulado.

Verificam que o painel monta, reage às opções, desenha a prévia, explode,
marca placas de vitrine, gera chapas, exporta e limpa tudo ao cancelar.
A aparência e a integração com o FreeCAD de verdade são testadas à mão.
"""
import importlib
import os
import sys

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point, box as sbox

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fake_freecad as fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def arch_mesh():
    perfil = sbox(-60, 0, 60, 60).difference(Point(0, 0).buffer(35, quad_segs=32))
    m = trimesh.creation.extrude_polygon(perfil, 30)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    m.apply_translation([200, 0, 10])
    return m


@pytest.fixture
def env(monkeypatch, qapp):
    doc = fake.Document()
    src = fake.mesh_object(doc, arch_mesh())
    App, Gui = fake.install(monkeypatch, doc, selection=[src])
    monkeypatch.syspath_prepend(ROOT)
    for name in [m for m in sys.modules if m.startswith("freecad.lasercut3d")]:
        monkeypatch.delitem(sys.modules, name)
    panel_mod = importlib.import_module("freecad.lasercut3d.panel")
    return doc, src, App, Gui, panel_mod


def preview_children(doc, group="LaserCut_Previa"):
    g = doc.getObject(group)
    return list(g.Group) if g is not None else []


def test_workbench_registers_commands(env):
    doc, src, App, Gui, _ = env
    importlib.import_module("freecad.lasercut3d.init_gui")
    importlib.import_module("freecad.lasercut3d.commands")
    assert {"LaserCut_Open", "LaserCut_InstallDeps"} <= set(Gui.commands)
    res = Gui.commands["LaserCut_Open"].GetResources()
    assert os.path.isfile(res["Pixmap"])


def test_open_command_shows_panel_for_selection(env, monkeypatch):
    doc, src, App, Gui, _ = env
    commands = importlib.import_module("freecad.lasercut3d.commands")
    monkeypatch.setattr(commands.deps, "missing", lambda: [])
    Gui.commands["LaserCut_Open"].Activated()
    assert Gui.Control.dialog is not None
    assert preview_children(doc)                    # prévia criada ao abrir
    Gui.Control.dialog.reject()


def test_panel_live_preview_cycle(env, tmp_path, monkeypatch):
    doc, src, App, Gui, panel_mod = env
    panel = panel_mod.LaserCutPanel(src)
    assert src.ViewObject.Visibility is False        # original escondido
    n_stack = len(preview_children(doc))
    assert n_stack == len(panel.session.items) > 0
    # cada peça da prévia é uma malha não vazia, no lugar do modelo original
    pts = np.vstack([o.Mesh.points for o in preview_children(doc)])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    assert np.allclose(lo, src.Mesh.points.min(axis=0), atol=1.6)

    # muda para placas: a sessão recebe as opções e a prévia é refeita
    panel.mode.setCurrentIndex(panel_mod.MODES.index("plates"))
    assert panel.session.cfg.mode == "plates"
    assert panel.timer.isActive()
    panel.update_preview()
    kinds = {it.kind for it in panel.session.items}
    assert {"plate", "skin"} <= kinds
    assert len(preview_children(doc)) == len(panel.session.items)
    assert panel.plate_list.count() == len(panel.session.plate_rows()) == 7
    assert "Placas + estrutura" in panel.info.toPlainText()

    # vista explodida: placas saem do lugar, e voltam
    obj = panel.preview.objects["P1"]
    panel.explode.setValue(80)
    assert np.linalg.norm([obj.Placement.Base.x, obj.Placement.Base.y,
                           obj.Placement.Base.z]) > 10
    panel.explode.setValue(0)
    assert obj.Placement.Base.x == obj.Placement.Base.y == obj.Placement.Base.z == 0

    # clicar numa placa na vista 3D seleciona a linha; marcar como vitrine
    panel.on_3d_selection(doc.Name, obj.Name)
    item = panel.plate_list.currentItem()
    assert item is not None and item.data(panel_mod.QtCore.Qt.UserRole) == "P1"
    item.setCheckState(panel_mod.QtCore.Qt.Checked)
    assert "P1" in panel.session.cfg.no_tab_plates

    # opção numérica
    panel.thickness.setValue(4.0)
    assert panel.session.cfg.thickness == 4.0

    # chapas e exportação
    panel.update_preview()
    panel.make_sheets()
    sheet_objs = preview_children(doc, "LaserCut_Chapas")
    assert {o.Label for o in sheet_objs} >= {"Corte", "Chapas (limite)"}
    monkeypatch.setattr(panel_mod.QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    panel.export()
    assert (tmp_path / "Arco_relatorio.md").exists()
    assert (tmp_path / "Arco_chapa01.svg").exists()

    # cancelar apaga tudo e devolve o original
    panel.reject()
    assert doc.getObject("LaserCut_Previa") is None
    assert doc.getObject("LaserCut_Chapas") is None
    assert src.ViewObject.Visibility is True
    assert Gui.Selection.observers == []


def test_error_shows_in_panel_instead_of_crashing(env, monkeypatch):
    doc, _, App, Gui, panel_mod = env
    sphere = fake.mesh_object(doc, trimesh.creation.icosphere(subdivisions=3, radius=30),
                              label="Esfera")
    panel = panel_mod.LaserCutPanel(sphere)
    panel.mode.setCurrentIndex(panel_mod.MODES.index("plates"))
    panel.update_preview()
    assert panel.info.toPlainText().startswith("Erro")
    assert preview_children(doc) == []
    panel.accept()
    assert Gui.Control.closed >= 1


def test_deps_pip_command_targets_user_folder(env):
    deps = importlib.import_module("freecad.lasercut3d.deps")
    cmd = deps.pip_command(["trimesh"], user_dir="/u/")
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert "--no-deps" in cmd and "--target" in cmd
    assert cmd[cmd.index("--target") + 1].startswith(os.path.join("/u/", "AdditionalPythonPackages"))
    assert deps.missing() == []                      # tudo instalado neste ambiente
    assert deps.numpy_problem() is None


def test_panel_fits_a_narrow_task_panel(env):
    """O painel é uma lista de blocos recolhíveis e cabe num painel estreito.

    Regressão: a v0.4 inicial era um bloco único com texto sem quebra de linha,
    que forçava uma largura mínima maior que o painel e cortava o conteúdo.
    """
    doc, src, App, Gui, panel_mod = env
    panel = panel_mod.LaserCutPanel(src)
    assert isinstance(panel.form, list) and len(panel.form) >= 4
    assert all(w.windowTitle() for w in panel.form)
    for mode in panel_mod.MODES:                       # cada modo mostra opções diferentes
        panel.stack.setCurrentIndex(panel_mod.MODES.index(mode))
        for w in panel.form:
            w.adjustSize()
            assert w.minimumSizeHint().width() <= 280, (mode, w.windowTitle(),
                                                        w.minimumSizeHint().width())
    panel.reject()


def test_panel_box_mode_and_face_override(env):
    doc, src, App, Gui, panel_mod = env
    panel = panel_mod.LaserCutPanel(src)
    panel.mode.setCurrentIndex(panel_mod.MODES.index("box"))
    assert panel.stack.currentIndex() == panel_mod.MODES.index("box")
    panel.update_preview()
    assert panel.session.result is not None, panel.info.toPlainText()
    assert "Caixa" in panel.info.toPlainText()
    n_auto = sum(1 for it in panel.session.items if it.kind == "plate")
    # força o topo aberto: uma placa a menos
    panel.face_combos[5].setCurrentIndex(2)
    assert panel.session.cfg.box_faces[5] == "off"
    panel.update_preview()
    assert sum(1 for it in panel.session.items if it.kind == "plate") == n_auto - 1
    panel.dividers.setChecked(False)
    assert panel.session.cfg.dividers == "none"
    panel.reject()
