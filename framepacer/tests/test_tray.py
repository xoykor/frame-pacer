"""Fechar minimiza para a bandeja (sem display: QT_QPA_PLATFORM=offscreen)."""
import os
import sys
from unittest import mock

sys.argv = ["test_tray"]
sys.path.insert(0, ".")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["FRAMEPACER_TEST_GUI"] = "1"

import framepacer as f
from PySide6.QtWidgets import QSystemTrayIcon


def make_shown(tray_available: bool):
    with mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=tray_available):
        app, w = f.run_gui()
    w.show()
    assert w.isVisible()
    return app, w


# 1. com tray: X esconde em vez de fechar
_, w = make_shown(True)
assert w._tray is not None, "tray deveria existir"
w.close()
assert not w.isVisible(), "X deveria esconder, não fechar"
texts = {a.text() for a in w._tray_menu.actions()}
assert texts == {"Mostrar", "Sair"}, texts
w._on_tray_activated(QSystemTrayIcon.Trigger)  # clique no tray mostra de novo
assert w.isVisible(), "clique no tray deveria mostrar"
print("close-to-tray OK")

# 2. sem tray no sistema: X fecha normal (comportamento antigo)
_, w2 = make_shown(False)
assert w2._tray is None
w2.close()
assert not w2.isVisible()
print("close-without-tray OK")

print("TRAY TESTS OK")
