"""Toggle liga/desliga + grafico de frame times (offscreen)."""
import os
import struct
import sys
import tempfile
from unittest import mock

sys.argv = ["test_gui"]
sys.path.insert(0, ".")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["FRAMEPACER_TEST_GUI"] = "1"

import framepacer as f
from PySide6.QtWidgets import QSystemTrayIcon

TMP = tempfile.mkdtemp(prefix="fp-gui-")


def make_game(pid, limit, samples_us=()):
    path = os.path.join(TMP, f"{pid}.shm")
    n = len(samples_us)
    ring = [0] * f.FT_N
    for i, v in enumerate(samples_us):
        ring[i % f.FT_N] = min(v, 65535)
    with open(path, "wb") as fh:
        fh.write(struct.pack(f.FMT, f.MAGIC, 2, pid, 0, limit, 6000, 1, b"fakegame"))
        fh.write(struct.pack("<II", n % f.FT_N, min(n, f.FT_N)))
        fh.write(struct.pack("<HH", 0, 0))
        fh.write(struct.pack(f"<{f.FT_N}H", *ring))
        fh.write(b"\0" * (f.FT_SIZE - fh.tell()))
    return f.Game(path, pid)


with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
    _, w0, ns = f.run_gui()
Card, FrameGraph = ns["Card"], ns["FrameGraph"]

# tamanhos uniformes (regressão visual: mesma linha, mesma altura)
w0.show()
w0.layout().activate()
hs = (w0.def_spin.height(), w0.save_btn.height(), w0.global_power.height())
assert hs[0] == hs[1] == hs[2] > 0, hs
assert w0.copy_btn.height() > 0
print("header sizes OK")

# toggle: desliga zera, religa restaura
g = make_game(42421, 60, [16666] * 10)
card = Card(g)
assert card.toggle.isChecked(), "limite 60 -> toggle ligado"
card.show()
card.layout().activate()
assert card.spin.height() == card.toggle.height() > 0, (card.spin.height(), card.toggle.height())
print("card sizes OK")
card.refresh()
assert len(card.graph.samples) == 10, "graph deveria ler do .shm"
card.toggle.click()
assert g.limit() == 0 and not card.toggle.isChecked(), "desligar zera o limite"
card.toggle.click()
assert g.limit() == 60 and card.toggle.isChecked(), "religar restaura 60"
# mudança externa (CLI) sincroniza o toggle
g.set_limit(120)
card.refresh()
assert card.spin.value() == 120 and card.toggle.isChecked()
g.set_limit(0)
card.refresh()
assert not card.toggle.isChecked()
print("toggle OK")

# grafico: alimenta e pinta sem travar
fg = FrameGraph()
fg.set_data([16.6, 16.7, 33.0, 16.5], 16.6)
assert len(fg.samples) == 4
fg.resize(300, 76)
fg.show()
fg.repaint()
img = fg.grab().toImage()

def accent_pixels(image):
    n = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            p = image.pixelColor(x, y)
            if p.green() > 180 and p.red() < 100 and p.blue() > 100:
                n += 1
    return n

assert accent_pixels(img) > 20, "linha do gráfico deveria usar o acento"
fg2 = FrameGraph()
fg2.resize(300, 76)
fg2.show()
fg2.repaint()
assert accent_pixels(fg2.grab().toImage()) == 0, "gráfico vazio não tem acento"
assert "QPushButton" in f.DARK_QSS and "QSlider" in f.DARK_QSS and "#00e5a0" in f.DARK_QSS
print("graph OK")

# tick detecta jogo com heartbeat fresco
import time as _time

OVL = tempfile.mkdtemp(prefix="fp-ovl-")
opath = os.path.join(OVL, "42431.shm")
with open(opath, "wb") as fh:
    fh.write(struct.pack(f.FMT, f.MAGIC, 1, 42431, 0, 60, 14450, _time.monotonic_ns(), b"tickgame"))
os.environ["FRAMEPACER_DIR"] = OVL
with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
    _, w3, _ = f.run_gui()
assert w3.cards, "tick deveria detectar o jogo fake"
print("tick detect OK")
os.environ.pop("FRAMEPACER_DIR", None)

# header: ícones vetoriais + power global no lugar de aplicar-a-todos
from PySide6.QtGui import QIcon

pwr = f.power_icon()
assert not pwr.pixmap(24, 24, QIcon.Normal, QIcon.Off).isNull()
assert not pwr.pixmap(24, 24, QIcon.Normal, QIcon.On).isNull()
assert not f.floppy_icon().pixmap(24, 24).isNull()
assert not f.copy_icon().pixmap(24, 24).isNull()
print("icons OK")

HDR = tempfile.mkdtemp(prefix="fp-hdr-")
hpath = os.path.join(HDR, "42441.shm")
with open(hpath, "wb") as fh:
    fh.write(struct.pack(f.FMT, f.MAGIC, 2, 42441, 0, 60, 6000, _time.monotonic_ns(), b"hdrgame"))
    fh.write(b"\0" * (f.FT_SIZE - 64))
os.environ["FRAMEPACER_DIR"] = HDR
with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
    _, w4, _ = f.run_gui()
assert not w4.save_btn.icon().isNull(), "salvar deveria ser disquete"
assert not w4.copy_btn.icon().isNull()
assert w4.global_power.isCheckable()
w4.global_power.click()  # desliga tudo
assert list(w4.cards.values())[0].game.limit() == 0
assert not w4.global_power.isChecked()
w4.global_power.click()  # religa com o padrão
assert list(w4.cards.values())[0].game.limit() == w4.def_spin.value()
assert w4.global_power.isChecked()
list(w4.cards.values())[0].game.set_limit(0)  # mudança externa sincroniza
w4.tick()
assert not w4.global_power.isChecked()
print("global power OK")
os.environ.pop("FRAMEPACER_DIR", None)

print("GUI TESTS OK")
