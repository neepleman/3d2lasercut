"""Painel de tarefas: opções à esquerda, resultado ao vivo na vista 3D.

Toda a lógica está em `laserslicer.session.Session`; aqui só ligamos widgets
a ela e desenhamos o que ela devolve.
"""
from __future__ import annotations

import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui

try:  # o FreeCAD oferece o "PySide" como camada sobre PySide2/PySide6
    from PySide import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets

from laserslicer import SliceConfig
from laserslicer.session import KIND_COLORS, MODE_LABELS, Session

from . import ICON
from .fcbridge import Preview, object_to_trimesh, safe_name

MODES = ["stack", "waffle", "plates"]
AXES = ["z", "x", "y"]
RIBS = [("auto", "Automático"), ("on", "Sempre"), ("none", "Nunca")]
DEBOUNCE_MS = 600


def _spin(value, lo, hi, step, decimals=2, suffix=" mm", special=None):
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(decimals)
    w.setSingleStep(step)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    if special:
        w.setSpecialValueText(special)
    w.setKeyboardTracking(False)
    return w


class SelectionObserver:
    def __init__(self, panel):
        self.panel = panel

    def addSelection(self, doc, obj_name, sub, pnt):  # noqa: N802 (API do FreeCAD)
        self.panel.on_3d_selection(doc, obj_name)


class LaserCutPanel:
    def __init__(self, obj):
        self.obj = obj
        self.doc = obj.Document
        self.was_visible = bool(getattr(obj.ViewObject, "Visibility", True))
        self.preview = Preview(self.doc)
        self.session = Session(object_to_trimesh(obj), SliceConfig())
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update_preview)
        self._building = False
        self.form = self._build_form()
        self.observer = SelectionObserver(self)
        Gui.Selection.addObserver(self.observer)
        self._load_from_config(self.session.cfg)
        self.update_preview()

    # ------------------------------------------------------------ interface
    def _build_form(self):
        """Vários blocos recolhíveis (lista de widgets), como os painéis nativos.

        O FreeCAD mostra cada widget da lista como uma caixa com título que
        pode ser recolhida, e o painel de tarefas rola quando não cabe.
        Os formulários quebram o rótulo acima do campo quando o painel é
        estreito, para nunca cortar dos lados.
        """
        def page(title):
            w = QtWidgets.QWidget()
            w.setWindowTitle(title)
            w.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
            return w

        def form_layout(widget=None):
            f = QtWidgets.QFormLayout(widget) if widget is not None else QtWidgets.QFormLayout()
            f.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
            f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            return f

        def note(text):
            lbl = QtWidgets.QLabel(text)
            lbl.setWordWrap(True)
            return lbl

        # 1) modelo, modo e material
        p1 = page("3D2LaserCut")
        p1.setWindowIcon(QtGui.QIcon(ICON))
        f = form_layout(p1)
        f.addRow(note(f"<b>Objeto:</b> {self.obj.Label}"))
        self.mode = QtWidgets.QComboBox()
        for m in MODES:
            self.mode.addItem(MODE_LABELS[m], m)
        self.thickness = _spin(3.0, 0.5, 30, 0.1)
        self.kerf = _spin(0.15, 0.0, 2, 0.01, decimals=3)
        self.clearance = _spin(0.1, 0.0, 2, 0.01, decimals=3)
        self.axis = QtWidgets.QComboBox()
        for a in AXES:
            self.axis.addItem(a.upper(), a)
        self.height = _spin(0.0, 0.0, 5000, 1, decimals=1, special="original")
        f.addRow("Modo", self.mode)
        f.addRow("Espessura", self.thickness)
        f.addRow("Kerf", self.kerf)
        f.addRow("Folga", self.clearance)
        f.addRow("Eixo vertical", self.axis)
        f.addRow("Altura final", self.height)

        # 2) opções do modo
        p2 = page("Opções do modo")
        v2 = QtWidgets.QVBoxLayout(p2)
        v2.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        v2.addWidget(self.stack)
        # -- pilha
        w = QtWidgets.QWidget()
        f = form_layout(w)
        self.pins = QtWidgets.QCheckBox("Furos para pinos")
        self.pin_d = _spin(3.0, 1.0, 20, 0.5)
        f.addRow(self.pins)
        f.addRow("Diâmetro do pino", self.pin_d)
        self.stack.addWidget(w)
        # -- grade
        w = QtWidgets.QWidget()
        f = form_layout(w)
        self.grid_x = _spin(25.0, 3.0, 1000, 1, decimals=1)
        self.grid_y = _spin(25.0, 3.0, 1000, 1, decimals=1)
        f.addRow("Espaçamento X", self.grid_x)
        f.addRow("Espaçamento Y", self.grid_y)
        self.stack.addWidget(w)
        # -- placas
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        f = form_layout()
        self.ribs = QtWidgets.QComboBox()
        for key, label in RIBS:
            self.ribs.addItem(label, key)
        self.rib_spacing = _spin(0.0, 0.0, 1000, 5, decimals=0, special="auto")
        self.finger = _spin(0.0, 0.0, 200, 1, decimals=1, special="auto")
        self.max_fillet = _spin(10.0, 0.0, 200, 1, decimals=1, special="não remover")
        self.max_fillet.setToolTip("Arredondamentos e chanfros até esse raio são removidos: "
                                   "as paredes vão até a aresta viva e ganham dedos.")
        self.skins = QtWidgets.QCheckBox("Chapas flexíveis nas curvas")
        f.addRow("Nervuras", self.ribs)
        f.addRow("Espaçamento", self.rib_spacing)
        f.addRow("Largura dos dedos", self.finger)
        f.addRow("Tirar arredondamentos até", self.max_fillet)
        f.addRow(self.skins)
        v.addLayout(f)
        v.addWidget(note("Marque as placas <b>de vitrine</b> (sem abas aparentes). "
                         "Clique numa placa na vista 3D para achá-la aqui."))
        self.plate_list = QtWidgets.QListWidget()
        self.plate_list.setMinimumHeight(90)
        self.plate_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        v.addWidget(self.plate_list)
        self.stack.addWidget(w)

        # 3) prévia
        p3 = page("Prévia")
        v = QtWidgets.QVBoxLayout(p3)
        h = QtWidgets.QHBoxLayout()
        self.auto = QtWidgets.QCheckBox("Atualizar sozinho")
        self.auto.setChecked(True)
        self.refresh = QtWidgets.QPushButton("Atualizar")
        h.addWidget(self.auto)
        h.addWidget(self.refresh)
        v.addLayout(h)
        f = form_layout()
        self.explode = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.explode.setRange(0, 100)
        f.addRow("Explodir", self.explode)
        v.addLayout(f)
        self.show_original = QtWidgets.QCheckBox("Mostrar o modelo original")
        v.addWidget(self.show_original)

        # 4) chapas e exportação
        p4 = page("Chapas e exportação")
        f = form_layout(p4)
        self.sheet_w = _spin(600, 50, 5000, 10, decimals=0)
        self.sheet_h = _spin(400, 50, 5000, 10, decimals=0)
        f.addRow("Largura da chapa", self.sheet_w)
        f.addRow("Altura da chapa", self.sheet_h)
        h = QtWidgets.QHBoxLayout()
        self.btn_sheets = QtWidgets.QPushButton("Gerar chapas")
        self.btn_export = QtWidgets.QPushButton("Exportar…")
        h.addWidget(self.btn_sheets)
        h.addWidget(self.btn_export)
        f.addRow(h)

        # 5) resultado
        p5 = page("Resultado")
        v = QtWidgets.QVBoxLayout(p5)
        self.info = QtWidgets.QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMinimumHeight(90)
        self.info.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        v.addWidget(self.info)

        self.pages = [p1, p2, p3, p4, p5]
        form = self.pages
        # sinais
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for w in (self.thickness, self.kerf, self.clearance, self.height, self.pin_d,
                  self.grid_x, self.grid_y, self.rib_spacing, self.finger, self.max_fillet,
                  self.sheet_w, self.sheet_h):
            w.valueChanged.connect(self.on_option_changed)
        for w in (self.axis, self.ribs):
            w.currentIndexChanged.connect(self.on_option_changed)
        for w in (self.pins, self.skins):
            w.toggled.connect(self.on_option_changed)
        self.plate_list.itemChanged.connect(self._plate_toggled)
        self.refresh.clicked.connect(self.update_preview)
        self.explode.valueChanged.connect(self._explode_changed)
        self.show_original.toggled.connect(self._show_original)
        self.btn_sheets.clicked.connect(self.make_sheets)
        self.btn_export.clicked.connect(self.export)
        return form

    def _load_from_config(self, cfg: SliceConfig) -> None:
        self._building = True
        self.mode.setCurrentIndex(MODES.index(cfg.mode))
        self.stack.setCurrentIndex(MODES.index(cfg.mode))
        self.thickness.setValue(cfg.thickness)
        self.kerf.setValue(cfg.kerf)
        self.clearance.setValue(cfg.slot_clearance)
        self.axis.setCurrentIndex(AXES.index(cfg.axis))
        self.height.setValue(cfg.target_height or 0.0)
        self.pins.setChecked(cfg.pins)
        self.pin_d.setValue(cfg.pin_diameter)
        self.grid_x.setValue(cfg.grid_x)
        self.grid_y.setValue(cfg.grid_y)
        self.ribs.setCurrentIndex([k for k, _ in RIBS].index(cfg.ribs))
        self.rib_spacing.setValue(cfg.rib_spacing)
        self.finger.setValue(cfg.finger_width)
        self.max_fillet.setValue(cfg.max_fillet_radius)
        self.skins.setChecked(cfg.skins)
        self.sheet_w.setValue(cfg.sheet_width)
        self.sheet_h.setValue(cfg.sheet_height)
        self.show_original.setChecked(False)
        self._building = False
        self._show_original(False)

    def read_options(self) -> dict:
        ribs = self.ribs.currentData()
        return dict(
            mode=self.mode.currentData(),
            thickness=self.thickness.value(),
            kerf=self.kerf.value(),
            slot_clearance=self.clearance.value(),
            axis=self.axis.currentData(),
            target_height=self.height.value() or None,
            pins=self.pins.isChecked(),
            pin_diameter=self.pin_d.value(),
            grid_x=self.grid_x.value(),
            grid_y=self.grid_y.value(),
            ribs=ribs,
            rib_spacing=self.rib_spacing.value(),
            finger_width=self.finger.value(),
            max_fillet_radius=self.max_fillet.value(),
            skins=self.skins.isChecked(),
            sheet_width=self.sheet_w.value(),
            sheet_height=self.sheet_h.value(),
        )

    # ------------------------------------------------------------ reações
    def _mode_changed(self, idx):
        self.stack.setCurrentIndex(idx)
        self.on_option_changed()

    def on_option_changed(self, *args):
        if self._building:
            return
        try:
            changed = self.session.set_options(**self.read_options())
        except Exception as e:
            self.info.setPlainText(f"Opção inválida: {e}")
            return
        if changed and self.auto.isChecked():
            self.timer.start(DEBOUNCE_MS)
        elif changed:
            self.info.setPlainText("Opções alteradas. Clique em Atualizar.")

    def _plate_toggled(self, item):
        if self._building:
            return
        name = item.data(QtCore.Qt.UserRole)
        on = item.checkState() == QtCore.Qt.Checked
        if self.session.set_showcase(name, on) and self.auto.isChecked():
            self.timer.start(DEBOUNCE_MS)

    def _explode_changed(self, value):
        self.preview.explode(self.session.items, self.session.offsets(value / 100.0))

    def _show_original(self, on):
        try:
            self.obj.ViewObject.Visibility = bool(on)
        except Exception:
            pass

    def on_3d_selection(self, doc, obj_name):
        if doc != self.doc.Name:
            return
        obj = self.doc.getObject(obj_name)
        name = self.preview.name_of(obj) if obj is not None else None
        if name is None:
            return
        for i in range(self.plate_list.count()):
            item = self.plate_list.item(i)
            if item.data(QtCore.Qt.UserRole) == name:
                self.plate_list.setCurrentItem(item)
                break

    # ------------------------------------------------------------ ações
    def _busy(self, fn):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            return fn()
        except Exception:
            App.Console.PrintError(traceback.format_exc())
            self.info.setPlainText("Erro inesperado — veja a janela Relatório (Report view).\n\n"
                                   + traceback.format_exc(limit=3))
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def update_preview(self):
        def work():
            self.session.compute()
            self.preview.show_items(self.session.items, KIND_COLORS)
            self._explode_changed(self.explode.value())
            self._fill_plates()
            self.info.setPlainText(self.session.summary())
        self._busy(work)

    def _fill_plates(self):
        self._building = True
        self.plate_list.clear()
        for row in self.session.plate_rows():
            tabs = "com abas" if row.has_tabs else "sem abas"
            item = QtWidgets.QListWidgetItem(f"{row.name}  —  {row.size}, {tabs}")
            item.setData(QtCore.Qt.UserRole, row.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if row.showcase else QtCore.Qt.Unchecked)
            self.plate_list.addItem(item)
        self._building = False

    def make_sheets(self):
        def work():
            sheets = self.session.nest()
            self.preview.show_sheets(self.session.sheet_polylines())
            self.info.setPlainText(self.session.summary())
            try:
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass
            return sheets
        self._busy(work)

    def export(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self.pages[0], "Pasta para os arquivos de corte", os.path.expanduser("~"))
        if not folder:
            return
        stem = safe_name(self.obj.Label) or "peca"
        files = self._busy(lambda: self.session.export(folder, stem))
        if files:
            self.info.setPlainText(self.session.summary() + "\n\nArquivos gerados:\n"
                                   + "\n".join(files))

    # ------------------------------------------------------------ fechar
    def _close(self):
        self.timer.stop()
        try:
            Gui.Selection.removeObserver(self.observer)
        except Exception:
            pass
        Gui.Control.closeDialog()

    def accept(self):
        """OK: mantém a prévia (e as chapas) no documento."""
        self._close()
        return True

    def reject(self):
        """Cancelar: apaga a prévia e devolve a visibilidade do original."""
        self.preview.remove_all()
        self._show_original(self.was_visible)
        self._close()
        return True
