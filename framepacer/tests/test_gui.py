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
    return f.Game(path, pid)


with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
    _, _, ns = f.run_gui()
Card, FrameGraph = ns["Card"], ns["FrameGraph"]

# toggle: desliga zera, religa restaura
g = make_game(42421, 60, [16666] * 10)
card = Card(g)
assert card.toggle.isChecked(), "limite 60 -> toggle ligado"
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
fg.show()
fg.repaint()
assert not fg.grab().isNull()
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

print("GUI TESTS OK")
