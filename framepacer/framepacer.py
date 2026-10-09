#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""FramePacer — painel gráfico (Qt6) e CLI para o limitador de FPS em tempo real.

Sem argumentos abre a GUI. Exemplos de CLI (úteis para atalhos do KDE):
    framepacer --list
    framepacer --set 60            # todos os jogos abertos
    framepacer --set 0 --pid 1234  # sem limite, só um processo
    framepacer --default 60        # limite inicial dos próximos jogos
"""
import argparse
import mmap
import os
import shutil
import struct
import sys
import time

MAGIC = 0x43415046  # "FPAC"
FMT = "<IIIIiIQ32s"  # magic, version, pid, reserved, limit, fps_x100, heartbeat_ns, name
SIZE = struct.calcsize(FMT)  # 64
FT_N = 120  # amostras no ring de frame times (protocolo v2)
FT_SIZE = 320  # 76 + 2*FT_N + padding até múltiplo de 8 (= sizeof Shared no C++)
OFF_LIMIT, OFF_FPS, OFF_HB = 16, 20, 24
API_NAMES = ((1, "Vulkan"), (2, "OpenGL (GLX)"), (4, "OpenGL (EGL)"))


def launch_option() -> str:
    """Opção de inicialização do Steam, com caminho absoluto se o PATH do Steam não achar o wrapper."""
    return f"{shutil.which('framepacer-run') or 'framepacer-run'} %command%"
PRESETS = (30, 60, 90, 120, 144, 165)

# Tema dark gamer (forçado, independe do tema do sistema).
C_BG = "#14141b"
C_CARD = "#1e1e29"
C_GRAPH_BG = "#17171f"
C_BORDER = "#2c2c3d"
C_BORDER_HI = "#3a3a4d"
C_TEXT = "#ececf1"
C_DIM = "#9a9ab0"
C_ACCENT = "#00e5a0"
C_ACCENT_HI = "#4dffc3"
C_HOT = "#ff5470"
C_TARGET = "#4caf50"
C_ON_TEXT = "#101014"

DARK_QSS = """
QWidget { color: #ececf1; }
QToolTip { background: #23232f; color: #ececf1; border: 1px solid #3a3a4d; }
QGroupBox { border: 1px solid #2c2c3d; border-radius: 10px; margin-top: 14px; font-weight: bold; color: #9a9ab0; background: #1e1e29; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget { background: transparent; }
QWidget#holder { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2c2c3d; min-height: 30px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #3a3a4d; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QPushButton { background: #2c2c3d; color: #ececf1; border: 1px solid #3a3a4d; border-radius: 8px; padding: 6px 12px; font-weight: bold; }
QPushButton:hover { background: #38384e; border-color: #00e5a0; }
QPushButton:pressed { background: #1a1a24; border-color: #00e5a0; }
QPushButton#powerBtn { font-size: 15pt; padding: 2px 12px; }
QPushButton#powerBtn:!checked { color: #9a9ab0; }
QPushButton#powerBtn:checked { background: #00e5a0; color: #101014; border-color: #00e5a0; }
QPushButton#powerBtn:checked:hover { background: #4dffc3; }
QSpinBox { background: #23232f; color: #ececf1; border: 1px solid #3a3a4d; border-radius: 8px; padding: 4px 6px; selection-background-color: #00e5a0; selection-color: #101014; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; background: transparent; border: none; }
QSlider::groove:horizontal { background: #2c2c3d; height: 6px; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #00e5a0; border-radius: 3px; }
QSlider::handle:horizontal { background: #00e5a0; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: #4dffc3; }
QFrame#card { background: #1e1e29; border: 1px solid #2c2c3d; border-radius: 12px; }
QLabel#cardTitle { font-size: 13pt; font-weight: bold; color: #ececf1; }
QLabel#apiBadge { background: #2c2c3d; color: #00e5a0; border-radius: 8px; padding: 2px 8px; font-size: 9pt; font-weight: bold; }
QLabel#fpsBig { font-size: 20pt; font-weight: bold; color: #ececf1; }
QLabel#emptyLabel { color: #9a9ab0; font-size: 11pt; }
QWidget#graph { background: #17171f; border: 1px solid #2c2c3d; border-radius: 8px; }
"""


def base_dir() -> str:
    """Onde ficam os arquivos de controle. Em ~/.local/state para continuar
    visível dentro do container dos jogos Proton (o pressure-vessel mascara
    $XDG_RUNTIME_DIR e os dirs vulkan/implicit_layer.d, mas compartilha $HOME)."""
    d = os.environ.get("FRAMEPACER_DIR")
    if not d:
        state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        d = os.path.join(state, "framepacer")
    return d


def legacy_dir() -> str:
    """Local antigo (antes do suporte a Proton); só leitura do `default` na migração."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(xdg, "framepacer") if xdg else f"/tmp/framepacer-{os.getuid()}"


def default_file() -> str:
    return os.path.join(base_dir(), "default")


def read_default() -> int:
    for d in (base_dir(), legacy_dir()):
        try:
            with open(os.path.join(d, "default")) as f:
                return max(0, int(f.read().strip() or 0))
        except (OSError, ValueError):
            continue
    return 0


def write_default(value: int) -> None:
    os.makedirs(base_dir(), mode=0o700, exist_ok=True)
    with open(default_file(), "w") as f:
        f.write(str(int(value)))


class Game:
    """Um processo com a camada ativa (um arquivo .shm mapeado em memória)."""

    def __init__(self, path: str, pid: int):
        self.path, self.pid = path, pid
        self.f = open(path, "r+b")
        try:
            st_size = os.fstat(self.f.fileno()).st_size
            self.mm = mmap.mmap(self.f.fileno(), FT_SIZE if st_size >= FT_SIZE else SIZE)
            magic, _ver, _pid, _res, _lim, _fps, _hb, name = struct.unpack_from(FMT, self.mm, 0)
            if magic != MAGIC:
                raise ValueError("magic inválido")
        except Exception:
            self.f.close()
            raise
        self.name = name.split(b"\0", 1)[0].decode(errors="replace") or f"pid {pid}"

    def api_text(self) -> str:
        mask = struct.unpack_from("<I", self.mm, 12)[0]
        return " + ".join(n for bit, n in API_NAMES if mask & bit) or "aguardando 1º frame"

    def limit(self) -> int:
        return struct.unpack_from("<i", self.mm, OFF_LIMIT)[0]

    def set_limit(self, value: int) -> None:
        struct.pack_into("<i", self.mm, OFF_LIMIT, int(value))

    def fps(self):
        """(fps, atualizado?) — não atualizado quando o jogo parou de apresentar frames."""
        x100 = struct.unpack_from("<I", self.mm, OFF_FPS)[0]
        hb = struct.unpack_from("<Q", self.mm, OFF_HB)[0]
        fresh = hb > 0 and (time.monotonic_ns() - hb) < 2_500_000_000
        return x100 / 100.0, fresh

    def frame_times_ms(self) -> list:
        """Intervalos present-a-present, do mais antigo ao mais recente, em ms.
        [] quando a lib ainda é v1 (sem ring) ou sem amostras."""
        if len(self.mm) < FT_SIZE:
            return []
        head, count = struct.unpack_from("<II", self.mm, 64)
        count = min(count, FT_N)
        if count == 0:
            return []
        raw = struct.unpack_from(f"<{FT_N}H", self.mm, 76)
        start = (head - count) % FT_N  # head = próxima posição a escrever
        return [raw[(start + i) % FT_N] / 1000.0 for i in range(count)]

    def proc_alive(self) -> bool:
        return os.path.exists(f"/proc/{self.pid}")

    def hb_fresh(self) -> bool:
        try:
            return self.fps()[1]
        except Exception:
            return False

    def alive(self) -> bool:
        # Jogo em container Proton pode ter PID invisível no host: vale o heartbeat.
        return os.path.exists(self.path) and (self.proc_alive() or self.hb_fresh())

    def close(self) -> None:
        try:
            self.mm.close()
            self.f.close()
        except (OSError, ValueError, BufferError):
            pass


def list_paths():
    d = base_dir()
    try:
        return [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".shm")]
    except FileNotFoundError:
        return []


STALE_UNLINK_AGE = 15.0  # só remove .shm sem /proc e sem heartbeat após essa carência (jogo iniciando)


def open_game(path: str):
    try:
        pid = int(os.path.basename(path)[:-4])
    except ValueError:
        return None
    try:
        game = Game(path, pid)
    except (OSError, ValueError):
        return None
    if game.proc_alive() or game.hb_fresh():
        return game
    try:
        age = time.time() - os.path.getmtime(path)  # jogo em container ainda iniciando?
    except OSError:
        game.close()
        return None
    if age > STALE_UNLINK_AGE:  # sobrou de um jogo que fechou/crashou
        game.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    return game


# --------------------------------------------------------------------------- CLI
def cli(args) -> int:
    if args.default is not None:
        write_default(args.default)
        print(f"Limite padrão para novos jogos: {args.default or 'sem limite'}")
    games = [g for g in map(open_game, list_paths()) if g]
    if args.pid is not None:
        games = [g for g in games if g.pid == args.pid]
    if args.set is not None:
        for g in games:
            g.set_limit(args.set)
            print(f"{g.name} (pid {g.pid}): limite -> {args.set or 'sem limite'}")
        if not games:
            print("Nenhum jogo com a camada ativa.", file=sys.stderr)
            return 1
    elif args.list or args.default is None:
        if not games:
            print("Nenhum jogo com a camada ativa.")
        for g in games:
            fps, fresh = g.fps()
            print(f"pid {g.pid:<7} {g.name:<16} {g.api_text():<14} limite={g.limit() or 'off':<5} fps={fps:6.1f}{'' if fresh else ' (parado)'}")
    for g in games:
        g.close()
    return 0


# --------------------------------------------------------------------------- GUI
def run_gui():
    from PySide6.QtCore import Qt, QTimer, QRectF, QSize
    from PySide6.QtGui import QIcon, QColor, QPainter, QPen, QPalette, QLinearGradient, QPainterPath
    from PySide6.QtWidgets import (QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QMenu,
                                   QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                                   QSystemTrayIcon, QVBoxLayout, QWidget)

    def make_spin() -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, 1000)
        s.setSpecialValueText("Sem limite")
        s.setSuffix(" FPS")
        return s

    class FrameGraph(QWidget):
        """Linha dos últimos intervalos present-a-present (ms), com degradê até a borda."""

        def __init__(self):
            super().__init__()
            self.setObjectName("graph")
            self.setFixedHeight(76)
            self.samples = []
            self.target_ms = 0.0

        def set_data(self, samples, target_ms: float) -> None:
            self.samples = samples
            self.target_ms = target_ms
            self.update()

        def sizeHint(self):
            return QSize(120, 76)

        def paintEvent(self, e) -> None:
            super().paintEvent(e)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            pad_l, pad_r, pad_t, pad_b = 6, 6, 16, 6
            if not self.samples:
                p.setPen(QColor(C_DIM))
                p.drawText(self.rect(), Qt.AlignCenter, "coletando frames…")
                return
            n = len(self.samples)
            plot_w = max(w - pad_l - pad_r, 1)
            plot_h = max(h - pad_t - pad_b, 1)
            ymax = max([self.target_ms * 1.5 if self.target_ms > 0 else 0.0] + self.samples + [8.0])

            def xy(i, v):
                x = pad_l + (i / (n - 1) if n > 1 else 0.5) * plot_w
                y = pad_t + plot_h - min(v / ymax, 1.0) * plot_h
                return x, y

            accent = QColor(C_ACCENT)
            line = QPainterPath()
            x0, y0 = xy(0, self.samples[0])
            line.moveTo(x0, y0)
            for i in range(1, n):
                line.lineTo(*xy(i, self.samples[i]))
            if n > 1:
                base = pad_t + plot_h
                xl, _ = xy(n - 1, 0)
                fill = QPainterPath(line)
                fill.lineTo(xl, base)
                fill.lineTo(x0, base)
                fill.closeSubpath()
                grad = QLinearGradient(0, pad_t, 0, pad_t + plot_h)
                top = QColor(C_ACCENT)
                top.setAlpha(170)
                bottom = QColor(C_ACCENT)
                bottom.setAlpha(0)
                grad.setColorAt(0, top)
                grad.setColorAt(1, bottom)
                p.fillPath(fill, grad)
            pen = QPen(accent, 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(line)
            thr = self.target_ms * 1.5 if self.target_ms > 0 else 25.0
            if self.target_ms > 0:
                y = pad_t + plot_h - min(self.target_ms / ymax, 1.0) * plot_h
                p.setPen(QPen(QColor(C_TARGET), 1, Qt.DashLine))
                p.drawLine(pad_l, int(y), w - pad_r, int(y))
            hot = QColor(C_HOT)
            for i, v in enumerate(self.samples):
                if v > thr:
                    x, y = xy(i, v)
                    p.setPen(Qt.NoPen)
                    p.setBrush(hot)
                    p.drawEllipse(int(x) - 2, int(y) - 2, 5, 5)
            s = sorted(self.samples)
            avg = sum(self.samples) / n
            p95 = s[min(int(n * 0.95), n - 1)]
            p.setPen(QColor(C_DIM))
            p.drawText(pad_l, 12, f"média {avg:.1f} ms · p95 {p95:.1f} ms")

    class Card(QFrame):
        def __init__(self, game: Game):
            super().__init__()
            self.game = game
            self.setFrameShape(QFrame.StyledPanel)
            self.setObjectName("card")

            self.name_label = QLabel(f"<b>{game.name}</b> &nbsp;<span style='color:#9a9ab0'>PID {game.pid}</span>")
            self.name_label.setObjectName("cardTitle")
            self.name_label.setTextFormat(Qt.RichText)
            self.api_badge = QLabel(game.api_text())
            self.api_badge.setObjectName("apiBadge")
            self.fps_label = QLabel("— FPS")
            self.fps_label.setObjectName("fpsBig")
            self.fps_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, 360)
            self.spin = make_spin()
            self.slider.valueChanged.connect(self._from_slider)
            self.spin.valueChanged.connect(self._from_spin)
            self.toggle = QPushButton("⏻")
            self.toggle.setObjectName("powerBtn")
            self.toggle.setToolTip("Liga/desliga o limite (desligado = sem limite)")
            self.toggle.setCheckable(True)
            self.toggle.toggled.connect(self._on_toggle)

            presets = QHBoxLayout()
            for p in PRESETS:
                b = QPushButton(str(p))
                b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, v=p: self.spin.setValue(v))
                presets.addWidget(b)
            off = QPushButton("∞")
            off.setToolTip("Sem limite")
            off.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            off.clicked.connect(lambda: self.spin.setValue(0))
            presets.addWidget(off)

            top = QHBoxLayout()
            top.addWidget(self.name_label)
            top.addWidget(self.api_badge)
            top.addStretch(1)
            top.addWidget(self.fps_label)
            mid = QHBoxLayout()
            mid.addWidget(self.slider, 1)
            mid.addWidget(self.spin)
            mid.addWidget(self.toggle)
            self.graph = FrameGraph()
            lay = QVBoxLayout(self)
            lay.addLayout(top)
            lay.addLayout(mid)
            lay.addWidget(self.graph)
            lay.addLayout(presets)

            self._last_limit = game.limit() or 60
            self._set_widgets(game.limit())
            self.refresh()

        def _set_widgets(self, v: int) -> None:
            for w, val in ((self.slider, min(v, 360)), (self.spin, v)):
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)
            self.toggle.blockSignals(True)
            self.toggle.setChecked(v != 0)
            self.toggle.blockSignals(False)

        def _apply_limit(self, v: int) -> None:
            if v > 0:
                self._last_limit = v
            self._set_widgets(v)
            self.game.set_limit(v)

        def _from_slider(self, v: int) -> None:
            self._apply_limit(v)

        def _from_spin(self, v: int) -> None:
            self._apply_limit(v)

        def _on_toggle(self, on: bool) -> None:
            self._apply_limit(self._last_limit if on else 0)

        def refresh(self) -> None:
            lim = self.game.limit()  # pode ter sido alterado pela CLI
            if lim > 0:
                self._last_limit = lim
            if lim != self.spin.value() and not (self.slider.isSliderDown() or self.spin.hasFocus()):
                self._set_widgets(lim)
            self.name_label.setText(f"<b>{self.game.name}</b> &nbsp;<span style='color:#9a9ab0'>PID {self.game.pid}</span>")
            self.api_badge.setText(self.game.api_text())
            fps, fresh = self.game.fps()
            self.fps_label.setText(f"{fps:.1f} FPS" if fresh else "— FPS")
            self.graph.set_data(self.game.frame_times_ms(), (1000.0 / lim) if lim > 0 else 0.0)

        def shutdown(self) -> None:
            self.game.close()

    class Main(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("FramePacer")
            self.resize(620, 540)
            self.cards = {}

            self.def_spin = make_spin()
            self.def_spin.setValue(read_default())
            save = QPushButton("Salvar padrão")
            save.clicked.connect(lambda: write_default(self.def_spin.value()))
            apply_all = QPushButton("Aplicar a todos os abertos")
            apply_all.clicked.connect(self.apply_all)
            box = QGroupBox("Limite inicial dos próximos jogos")
            bl = QHBoxLayout(box)
            bl.addWidget(self.def_spin)
            bl.addWidget(save)
            bl.addWidget(apply_all)

            holder = QWidget()
            holder.setObjectName("holder")
            self.list_layout = QVBoxLayout(holder)
            self.list_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(holder)

            self.empty = QLabel(
                "Nenhum jogo detectado.\n\nInicie o jogo com <b>framepacer-run</b> (Vulkan e OpenGL).\n"
                f"No Steam, em Opções de inicialização:\n<tt>{launch_option()}</tt>")
            self.empty.setObjectName("emptyLabel")
            self.empty.setAlignment(Qt.AlignCenter)
            self.empty.setTextFormat(Qt.RichText)
            self.empty.setWordWrap(True)

            copy = QPushButton("Copiar opção de inicialização do Steam")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(launch_option()))

            root = QVBoxLayout(self)
            root.addWidget(box)
            root.addWidget(self.empty)
            root.addWidget(scroll, 1)
            root.addWidget(copy)

            self._tray = None
            self._tray_menu = None
            self._toggle_act = None
            if QSystemTrayIcon.isSystemTrayAvailable():
                tray = QSystemTrayIcon(self)
                tray.setIcon(QIcon.fromTheme("applications-games"))
                tray.setToolTip("FramePacer")
                menu = QMenu()
                self._toggle_act = menu.addAction("Mostrar")
                self._toggle_act.triggered.connect(self.toggle_from_tray)
                quit_act = menu.addAction("Sair")
                quit_act.triggered.connect(QApplication.quit)
                tray.setContextMenu(menu)
                tray.activated.connect(self._on_tray_activated)
                tray.show()
                self._tray = tray
                self._tray_menu = menu

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(500)
            self.tick()

        def toggle_from_tray(self) -> None:
            if self.isVisible():
                self.hide()
                if self._toggle_act is not None:
                    self._toggle_act.setText("Mostrar")
            else:
                self.show()
                self.raise_()
                self.activateWindow()
                if self._toggle_act is not None:
                    self._toggle_act.setText("Esconder")

        def _on_tray_activated(self, reason) -> None:
            if reason == QSystemTrayIcon.Trigger:
                self.toggle_from_tray()

        def closeEvent(self, e) -> None:
            if self._tray is not None:
                e.ignore()
                self.hide()
                if self._toggle_act is not None:
                    self._toggle_act.setText("Mostrar")
            else:
                super().closeEvent(e)

        def apply_all(self) -> None:
            for c in self.cards.values():
                c.spin.setValue(self.def_spin.value())

        def tick(self) -> None:
            seen = set(list_paths())
            for path in seen - set(self.cards):
                g = open_game(path)
                if g:
                    card = Card(g)
                    self.cards[path] = card
                    self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            for path in list(self.cards):
                card = self.cards[path]
                if path not in seen or not card.game.alive():
                    self.list_layout.removeWidget(card)
                    card.shutdown()
                    card.deleteLater()
                    del self.cards[path]
                else:
                    card.refresh()
            self.empty.setVisible(not self.cards)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FramePacer")
    app.setDesktopFileName("framepacer")  # casa com o framepacer.desktop no Wayland
    app.setStyle("Fusion")
    _dark = {
        QPalette.Window: C_BG, QPalette.WindowText: C_TEXT,
        QPalette.Base: C_CARD, QPalette.AlternateBase: C_GRAPH_BG,
        QPalette.Text: C_TEXT, QPalette.Button: C_CARD,
        QPalette.ButtonText: C_TEXT, QPalette.Highlight: C_ACCENT,
        QPalette.HighlightedText: C_ON_TEXT, QPalette.ToolTipBase: C_CARD,
        QPalette.ToolTipText: C_TEXT, QPalette.Mid: C_DIM,
        QPalette.Dark: C_BG, QPalette.Light: C_BORDER_HI,
    }
    _pal = app.palette()
    for _role, _color in _dark.items():
        _pal.setColor(_role, QColor(_color))
    app.setPalette(_pal)
    app.setStyleSheet(DARK_QSS)
    w = Main()
    if os.environ.get("FRAMEPACER_TEST_GUI"):
        return app, w, {"Card": Card, "FrameGraph": FrameGraph}  # gancho de teste
    w.show()
    return app.exec()


def main() -> int:
    if len(sys.argv) == 1:
        return run_gui()
    ap = argparse.ArgumentParser(description="FramePacer — limitador de FPS em tempo real")
    ap.add_argument("--set", type=int, metavar="FPS", help="define o limite (0 = sem limite)")
    ap.add_argument("--pid", type=int, help="aplica só a este PID (padrão: todos)")
    ap.add_argument("--default", type=int, metavar="FPS", help="limite inicial dos próximos jogos")
    ap.add_argument("--list", action="store_true", help="lista jogos e FPS atual")
    ap.add_argument("--gui", action="store_true", help="abre a interface gráfica")
    args = ap.parse_args()
    if args.gui:
        return run_gui()
    return cli(args)


if __name__ == "__main__":
    sys.exit(main())
