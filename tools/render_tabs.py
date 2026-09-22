"""Offscreen renders of Measure / R&D / Curator in three themes.

Usage: .venv/bin/python render_tabs.py before|after
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "build" / "renders"
OUT.mkdir(parents=True, exist_ok=True)
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "after"

import numpy as np
from PyQt6.QtWidgets import QApplication

import dms.calibration as calibration_module
import dms.settings_manager as settings_module

_tmp = Path(tempfile.mkdtemp(prefix="fg_render_"))
settings_module._config_dir = lambda: _tmp / "config"
calibration_module._config_dir = lambda: _tmp / "config"

from dms.rnd.models import RnDGroup, RnDMeasurement
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.main_window import MainWindow
from dms.ui.update_check import UpdateCheck

MainWindow._refresh_devices = lambda self: None
MainWindow._start_level_monitor = lambda self: None
UpdateCheck.start = lambda self: None
MainWindow._confirm_measure_close = lambda self: True

app = QApplication.instance() or QApplication([])


def pump(seconds: float = 1.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


freqs = np.geomspace(20.0, 20000.0, 480)
base = 6.0 * np.exp(-(((np.log10(freqs) - 3.5) / 0.35) ** 2)) - 4.0 * (freqs < 60)


def curve(shift: float, wiggle: float) -> np.ndarray:
    return 90.0 + base + shift + wiggle * np.sin(np.log10(freqs) * 7.0)


manager = SettingsManager()
controller = ThemeController(app, manager)
window = MainWindow(SessionData(rig="Rig", brand="DMS", model="Demo"), manager, controller)
window._confirm_rnd_close = lambda: True
window.resize(1400, 900)
window.show()
pump(0.5)

# Measure: two kept curves, variation view.
window._kept_curves = [(freqs, curve(0.0, 1.0)), (freqs, curve(1.5, -1.2))]
window._recompute_average()
window._variation_toggle.setChecked(True)
window._update_plots()

# Curator: real REW export.
window._curator_widget.import_files(["/Users/dms/Desktop/airpods 5.txt"], show_errors=False)


# R&D: one group of two measurements.
def measurement(mid: str, name: str, mag: np.ndarray) -> RnDMeasurement:
    return RnDMeasurement(
        id=mid,
        name=name,
        freqs=freqs,
        mag_db=mag,
        metadata={"brand": "DMS", "model": "Demo", "rig": "Rig"},
        rig="Rig",
        input_device_label="Input",
        input_channel_index=0,
        input_channel_label="Channel 1",
        output_device_label="Output",
        pinned=True,
    )


rnd = window._rnd_widget
rnd.session.measurements = [
    measurement("m1", "First", curve(0.0, 0.8)),
    measurement("m2", "Second", curve(1.0, -0.9)),
]
rnd.session.groups = [
    RnDGroup(
        id="g1",
        name="Prototype A",
        visible=True,
        pinned=True,
        variation_enabled=True,
        measurement_ids=["m1", "m2"],
    )
]
rnd.session.ungrouped_order = []
rnd.replace_session(rnd.session)
rnd._select_id("g1")
pump(1.5)

tabs = {"measure": 0}
for index in range(window._tabs.count()):
    widget = window._tabs.widget(index)
    if widget is rnd:
        tabs["rnd"] = index
    elif widget is window._curator_widget:
        tabs["curator"] = index

for theme in ("dark", "fastgraph95", "dither"):
    controller.set_theme(theme, persist=False)
    pump(0.8)
    for name, index in tabs.items():
        window._tabs.setCurrentIndex(index)
        window.resize(1400, 900)
        pump(1.2)
        path = OUT / f"{name}_{theme}_{SUFFIX}.png"
        window.grab().save(str(path))
        print(path.name)

window.close()
