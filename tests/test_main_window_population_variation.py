from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dms.hrtf import HRTFCurve
from dms.ui.main_window import MainWindow


class _Toggle:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


def _variation_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "population.txt"
    path.write_text(
        "100 1 2 3 4 5\n"
        "1000 10 20 30 40 50\n",
        encoding="utf-8",
    )
    return HRTFCurve(str(path))


def test_population_compensation_forces_measure_view_to_variation(
    tmp_path: Path,
) -> None:
    fake = SimpleNamespace(
        _hrtf=_variation_hrtf(tmp_path),
        _hrtf_toggle=_Toggle(True),
        _variation_toggle=_Toggle(False),
    )
    fake._is_hrtf_active = lambda: MainWindow._is_hrtf_active(fake)

    assert MainWindow._bottom_view_mode(fake) == "variation"


def test_one_measurement_gets_population_compensation_band(
    tmp_path: Path,
) -> None:
    freqs = np.array([100.0, 1000.0])
    fake = SimpleNamespace(
        _kept_curves=[(freqs, np.array([10.0, 100.0]))],
        _average=(freqs, np.array([10.0, 100.0])),
        _hrtf=_variation_hrtf(tmp_path),
        _hrtf_toggle=_Toggle(True),
    )
    fake._is_hrtf_active = lambda: MainWindow._is_hrtf_active(fake)

    variation = MainWindow._variation_from_kept_curves(
        fake,
        hrtf=fake._hrtf,
    )

    assert variation is not None
    _, p10, p25, p75, p90, median = variation
    assert np.allclose(p10, [5.0, 50.0])
    assert np.allclose(p25, [6.0, 60.0])
    assert np.allclose(median, [7.0, 70.0])
    assert np.allclose(p75, [8.0, 80.0])
    assert np.allclose(p90, [9.0, 90.0])
