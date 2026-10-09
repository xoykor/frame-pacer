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
OFF_LIMIT, OFF_FPS, OFF_HB = 16, 20, 24
API_NAMES = ((1, "Vulkan"), (2, "OpenGL (GLX)"), (4, "OpenGL (EGL)"))


def launch_option() -> str:
    """Opção de inicialização do Steam, com caminho absoluto se o PATH do Steam não achar o wrapper."""
    return f"{shutil.which('framepacer-run') or 'framepacer-run'} %command%"
PRESETS = (30, 60, 90, 120, 144, 165)


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
            self.mm = mmap.mmap(self.f.fileno(), SIZE)
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
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import (QApplication, QFrame, QGroupBox, QHBoxLayout, QLabel, QMenu,
                                   QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                                   QSystemTrayIcon, QVBoxLayout, QWidget)

    def make_spin() -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, 1000)
        s.setSpecialValueText("Sem limite")
        s.setSuffix(" FPS")
        return s

    class Card(QFrame):
        def __init__(self, game: Game):
            super().__init__()
            self.game = game
            self.setFrameShape(QFrame.StyledPanel)

            self.title = title = QLabel(f"<b>{game.name}</b> &nbsp;<span style='color:gray'>PID {game.pid} · {game.api_text()}</span>")
            self.fps_label = QLabel("— FPS")
            f = QFont(self.fps_label.font())
            f.setPointSize(f.pointSize() + 6)
            f.setBold(True)
            self.fps_label.setFont(f)
            self.fps_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, 360)
            self.spin = make_spin()
            self.slider.valueChanged.connect(self._from_slider)
            self.spin.valueChanged.connect(self._from_spin)

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
            top.addWidget(title, 1)
            top.addWidget(self.fps_label)
            mid = QHBoxLayout()
            mid.addWidget(self.slider, 1)
            mid.addWidget(self.spin)
            lay = QVBoxLayout(self)
            lay.addLayout(top)
            lay.addLayout(mid)
            lay.addLayout(presets)

            self._set_widgets(game.limit())
            self.refresh()

        def _set_widgets(self, v: int) -> None:
            for w, val in ((self.slider, min(v, 360)), (self.spin, v)):
                w.blockSignals(True)
                w.setValue(val)
                w.blockSignals(False)

        def _from_slider(self, v: int) -> None:
            self._set_widgets(v)
            self.game.set_limit(v)

        def _from_spin(self, v: int) -> None:
            self._set_widgets(v)
            self.game.set_limit(v)

        def refresh(self) -> None:
            lim = self.game.limit()  # pode ter sido alterado pela CLI
            if lim != self.spin.value() and not (self.slider.isSliderDown() or self.spin.hasFocus()):
                self._set_widgets(lim)
            self.title.setText(f"<b>{self.game.name}</b> &nbsp;<span style='color:gray'>PID {self.game.pid} · {self.game.api_text()}</span>")
            fps, fresh = self.game.fps()
            self.fps_label.setText(f"{fps:.1f} FPS" if fresh else "— FPS")

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
            self.list_layout = QVBoxLayout(holder)
            self.list_layout.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(holder)

            self.empty = QLabel(
                "Nenhum jogo detectado.\n\nInicie o jogo com <b>framepacer-run</b> (Vulkan e OpenGL).\n"
                f"No Steam, em Opções de inicialização:\n<tt>{launch_option()}</tt>")
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
    w = Main()
    if os.environ.get("FRAMEPACER_TEST_GUI"):
        return app, w  # gancho de teste (sem mostrar nem entrar no loop)
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
