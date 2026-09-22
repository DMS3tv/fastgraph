from pathlib import Path

import numpy as np

import dms.ui.measure_controller as measure_controller_module


def _write_hrtf(path: Path) -> None:
    path.write_text("100 1\n1000 2\n", encoding="utf-8")


def _window(make_main_window):
    """A real window whose plot refreshes are counted instead of drawn."""
    window = make_main_window()
    window.update_count = 0

    def _count_update(_show_pending: bool) -> None:
        window.update_count += 1

    window.measure.curves_changed.disconnect()
    window.measure.curves_changed.connect(_count_update)
    return window


def test_hrtf_dropdown_reads_fastgraph_hrtf_folder(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    _write_hrtf(hrtf_dir / "Beta.txt")
    _write_hrtf(hrtf_dir / "Alpha.txt")
    monkeypatch.setattr(measure_controller_module, "HRTF_DIR", hrtf_dir)
    window = _window(make_main_window)

    window.measure.refresh_hrtf_options()

    assert [
        window.measure_tab.hrtf_combo.itemText(i)
        for i in range(window.measure_tab.hrtf_combo.count())
    ] == [
        "None",
        "Alpha",
        "Beta",
    ]


def test_population_average_hrtf_loads_as_a_variation_compensation() -> None:
    path = Path(measure_controller_module.HRTF_DIR) / "5128 IEM Population Average.txt"
    curve = measure_controller_module.HRTFCurve(str(path))

    assert curve.is_variation
    assert curve.freqs.size == 1200
    assert curve.freqs[0] == 20.0
    assert curve.freqs[-1] == 20000.0
    variation = curve.evaluate_variation(np.array([1000.0]))
    assert variation is not None
    assert np.allclose(
        [values[0] for values in variation],
        [1.833821, 1.834518, 1.836500, 1.837123, 1.837518],
    )


def test_selecting_built_in_hrtf_loads_and_enables_compensation(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    _write_hrtf(hrtf_path)
    monkeypatch.setattr(measure_controller_module, "HRTF_DIR", hrtf_dir)
    window = _window(make_main_window)
    window.measure.refresh_hrtf_options()

    window.measure_tab.hrtf_combo.setCurrentIndex(
        window.measure_tab.hrtf_combo.findData(str(hrtf_path))
    )

    assert window.measure.hrtf is not None
    assert window.measure.hrtf.name == "Fixture A"
    assert window._settings.get("hrtf_path") == str(hrtf_path)
    assert window.measure_tab.hrtf_toggle.isEnabled()
    assert window.measure_tab.hrtf_toggle.isChecked()
    assert window.measure_tab.hrtf_label.text() == "Fixture A"
    assert window.measure_tab.hrtf_label.toolTip() == str(hrtf_path)
    # One redraw from switching the compensation toggle on, one from the load.
    assert window.update_count == 2


def test_selecting_none_clears_hrtf_and_disables_compensation(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    _write_hrtf(hrtf_path)
    monkeypatch.setattr(measure_controller_module, "HRTF_DIR", hrtf_dir)
    window = _window(make_main_window)
    window.measure.refresh_hrtf_options()
    window.measure_tab.hrtf_combo.setCurrentIndex(
        window.measure_tab.hrtf_combo.findData(str(hrtf_path))
    )

    window.measure_tab.hrtf_combo.setCurrentIndex(0)

    assert window.measure.hrtf is None
    assert window._settings.get("hrtf_path") is None
    assert not window.measure_tab.hrtf_toggle.isEnabled()
    assert not window.measure_tab.hrtf_toggle.isChecked()
    assert window.measure_tab.hrtf_label.text() == "None"
    assert window.measure_tab.hrtf_label.toolTip() == ""


def test_restore_ignores_missing_or_legacy_custom_hrtf_path(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    _write_hrtf(hrtf_dir / "Built In.txt")
    custom_path = tmp_path / "Custom.txt"
    _write_hrtf(custom_path)
    monkeypatch.setattr(measure_controller_module, "HRTF_DIR", hrtf_dir)
    window = _window(make_main_window)
    window._settings.set("hrtf_path", str(custom_path))

    window.measure.restore_hrtf_state()

    assert window.measure.hrtf is None
    assert window._settings.get("hrtf_path") is None
    assert window.measure_tab.hrtf_combo.currentText() == "None"
