"""Sessão interativa: a lógica do painel do FreeCAD, sem FreeCAD nem Qt.

O painel só lê/escreve opções e desenha o que a sessão devolve. Assim, toda a
lógica (recalcular, explodir, chapas, exportar, placas "de vitrine") pode ser
testada com pytest, e outras interfaces podem reutilizá-la.

Todas as coordenadas devolvidas estão no referencial do modelo **original**
(o mesmo do documento do FreeCAD), em mm, ancoradas no canto mínimo dele. Se
houver escala (`scale`/`target_height`), a prévia mostra o tamanho real das
peças cortadas.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np
import trimesh

from .assembly import AssemblyItem, assembly_items, explode_offsets
from .config import SliceConfig
from .pipeline import Result, export_result, nest_result, run
from .slicer import polygons

MODE_LABELS = {
    "stack": "Fatias empilhadas",
    "waffle": "Grade encaixada",
    "plates": "Placas + estrutura",
}
KIND_COLORS = {                      # RGB 0..1, as mesmas cores das imagens do README
    "layer": (0.87, 0.72, 0.47),
    "slice": (0.87, 0.72, 0.47),
    "plate": (0.87, 0.72, 0.47),
    "skin": (0.78, 0.43, 0.27),
    "rib": (0.27, 0.47, 0.78),
}


@dataclass
class PlateRow:
    name: str
    size: str
    neighbors: int
    has_tabs: bool
    showcase: bool          # "de vitrine" = sem furos de aba


@dataclass
class Polyline:
    points: np.ndarray      # (n, 3)
    closed: bool
    role: str               # "sheet" | "cut" | "engrave"


@dataclass
class Session:
    mesh: trimesh.Trimesh
    cfg: SliceConfig = field(default_factory=SliceConfig)
    result: Result | None = None
    items: list[AssemblyItem] = field(default_factory=list)
    error: str | None = None

    # ------------------------------------------------------------ opções
    def set_options(self, **kw) -> bool:
        """Atualiza opções. Retorna True se algo mudou (e o resultado ficou velho)."""
        names = {f.name for f in dataclasses.fields(SliceConfig)}
        unknown = set(kw) - names
        if unknown:
            raise KeyError(f"Opções desconhecidas: {sorted(unknown)}")
        new = dataclasses.replace(self.cfg, **kw)
        if new == self.cfg:
            return False
        self.cfg = new
        self.result, self.items = None, []
        return True

    def set_showcase(self, plate: str, on: bool) -> bool:
        current = set(self.cfg.no_tab_plates)
        current.add(plate) if on else current.discard(plate)
        return self.set_options(no_tab_plates=tuple(sorted(current)))

    # ------------------------------------------------------------ cálculo
    def compute(self) -> Result | None:
        """Recalcula a montagem (sem nesting, para ser rápido)."""
        self.error = None
        try:
            self.result = run(self.mesh, self.cfg, nest_parts=False)
        except ValueError as e:
            self.result, self.items, self.error = None, [], str(e)
            return None
        # Volta para a orientação e o canto do modelo original, mas mantém a
        # escala pedida: a prévia mostra o tamanho real das peças cortadas.
        T = self.result.transform
        scale = float(np.cbrt(abs(np.linalg.det(T[:3, :3]))))
        R_inv = (T[:3, :3] / scale).T
        ext = np.asarray(self.result.model_extents)
        corners = np.array([[x, y, z] for x in (0, ext[0]) for y in (0, ext[1])
                            for z in (0, ext[2])]) @ R_inv.T
        back = np.eye(4)
        back[:3, :3] = R_inv
        back[:3, 3] = np.asarray(self.mesh.bounds[0]) - corners.min(axis=0)
        items = assembly_items(self.result, self.cfg)
        stack_dir = None
        for it in items:
            it.mesh.apply_transform(back)
            if it.normal is not None:
                n = R_inv @ it.normal
                it.normal = n / np.linalg.norm(n)
                if it.kind == "layer":
                    stack_dir = it.normal
        if stack_dir is not None:  # todas as camadas compartilham o mesmo eixo
            for it in items:
                if it.kind == "layer":
                    it.normal = stack_dir
        self.items = items
        return self.result

    def offsets(self, factor: float) -> list[np.ndarray]:
        return explode_offsets(self.items, factor)

    def nest(self):
        if self.result is None:
            self.compute()
        if self.result is None:
            return []
        nest_result(self.result, self.cfg)
        return self.result.sheets

    # ------------------------------------------------------------ placas
    def plate_rows(self) -> list[PlateRow]:
        if self.result is None or self.result.plates is None:
            return []
        pi = self.result.plates
        neighbors = {P.id: 0 for P in pi.plates}
        for j in pi.joints:
            neighbors[j.a] += 1
            neighbors[j.b] += 1
        rows = []
        for P in pi.plates:
            x0, y0, x1, y1 = P.base.bounds
            rows.append(PlateRow(name=P.name, size=f"{x1 - x0:.0f} x {y1 - y0:.0f} mm",
                                 neighbors=neighbors[P.id], has_tabs=bool(P.holes),
                                 showcase=P.name in self.cfg.no_tab_plates))
        return rows

    # ------------------------------------------------------------ chapas
    def sheet_polylines(self, gap: float = 40.0) -> list[Polyline]:
        """Chapas desenhadas ao lado do modelo, no plano da base dele."""
        if self.result is None or not self.result.sheets:
            return []
        lo = np.min([it.mesh.bounds[0] for it in self.items], axis=0) if self.items \
            else np.zeros(3)
        hi = np.max([it.mesh.bounds[1] for it in self.items], axis=0) if self.items \
            else np.zeros(3)
        x = hi[0] + gap
        y0, z0 = lo[1], lo[2]
        out: list[Polyline] = []

        def add(coords, closed, role, ox):
            c = np.asarray(coords, dtype=float)
            if len(c) < 2:
                return
            pts = np.column_stack([c[:, 0] + ox, c[:, 1] + y0, np.full(len(c), z0)])
            out.append(Polyline(pts, closed, role))

        for sheet in self.result.sheets:
            W, H = sheet.width, sheet.height
            add([(0, 0), (W, 0), (W, H), (0, H)], True, "sheet", x)
            for pp in sheet.placed:
                for poly in polygons(pp.cut):
                    for ring in [*poly.interiors, poly.exterior]:
                        add(list(ring.coords)[:-1], True, "cut", x)
                for geom, role in ((pp.cut_lines, "cut"), (pp.label, "engrave")):
                    if geom is None or geom.is_empty:
                        continue
                    for ln in getattr(geom, "geoms", [geom]):
                        add(list(ln.coords), False, role, x)
            x += W + gap
        return out

    # ------------------------------------------------------------ saída
    def export(self, folder: str, stem: str) -> list[str]:
        if self.result is None:
            self.compute()
        if self.result is None:
            raise ValueError(self.error or "Nada para exportar.")
        if not self.result.sheets:
            nest_result(self.result, self.cfg)
        return export_result(self.result, self.cfg, folder, stem, source=stem)

    def summary(self) -> str:
        if self.error:
            return f"Erro: {self.error}"
        r = self.result
        if r is None:
            return "Ainda não calculado."
        lines = [f"Modo: {MODE_LABELS[self.cfg.mode]}",
                 "Medidas: " + " x ".join(f"{v:.0f}" for v in r.model_extents) + " mm"]
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.kind] = counts.get(it.kind, 0) + 1
        names = {"layer": ("camada", "camadas"), "slice": ("fatia", "fatias"),
                 "plate": ("placa", "placas"), "skin": ("chapa flexível", "chapas flexíveis"),
                 "rib": ("nervura", "nervuras")}
        lines.append("Peças: " + ", ".join(f"{n} {names[k][0 if n == 1 else 1]}"
                                           for k, n in counts.items())
                     + f" ({len(r.parts)} no total)")
        if r.plates is not None:
            lines.append(f"Superfície coberta: {100 * r.plates.coverage:.0f}%")
            n_sharp = len(r.plates.sharp_edges)
            if n_sharp:
                lines.append(f"Arredondamentos removidos: {n_sharp} (viraram cantos vivos)")
            n_fing = sum(1 for j in r.plates.joints if j.kind.startswith("finger"))
            lines.append(f"Juntas com dedos: {n_fing} (use Explodir para ver)")
            if r.plates.ribs is None:
                lines.append(f"Nervuras: não ({r.plates.ribs_reason})")
            else:
                lines.append(f"Nervuras: {r.plates.ribs_reason}")
        if r.pins:
            lines.append(f"Pinos de alinhamento: {len(r.pins)}")
        if r.sheets:
            lines.append(f"Chapas de {self.cfg.sheet_width:g} x {self.cfg.sheet_height:g} mm: "
                         f"{len(r.sheets)}")
        if r.warnings:
            lines.append("")
            lines.append("Avisos:")
            lines += [f"• {w}" for w in r.warnings]
        return "\n".join(lines)
