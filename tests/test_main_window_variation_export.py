from pathlib import Path

import numpy as np

from dms.processing import VariationBand
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair


def _band() -> VariationBand:
    return VariationBand(
        np.array([100.0]),
        p10=np.array([-2.0]),
        p25=np.array([-1.0]),
        median=np.array([0.0]),
        p75=np.array([1.0]),
        p90=np.array([2.0]),
    )


def _set_variation_mode(window, checked: bool) -> None:
    """Set the view without the redraw the toggle would trigger."""
    window.measure_tab.variation_toggle.blockSignals(True)
    window.measure_tab.variation_toggle.setChecked(checked)
    window.measure_tab.variation_toggle.blockSignals(False)


def _variation_window(make_main_window, *, variation_mode: bool = False, **kwargs):
    window = make_main_window(**kwargs)
    _set_variation_mode(window, variation_mode)
    window.measure.average = (np.array([100.0]), np.array([1.0]))
    window.measure.variation = _band()
    return window


def test_sync_export_button_switches_label_and_keeps_upload_average_based(
    make_main_window,
) -> None:
    window = _variation_window(make_main_window, variation_mode=True)
    window.measure.average = (np.array([100.0]), np.array([1.0]))
    window.measure.variation = None

    window.measure_io.sync_export_button()

    assert window.measure_tab.export_btn.text() == "Export Variation…"
    assert "percentile" in window.measure_tab.export_btn.toolTip().lower()
    assert window.measure_tab.export_btn.isEnabled() is False
    assert window.measure_tab.upload_btn.isEnabled() is True

    _set_variation_mode(window, False)
    window.measure_io.sync_export_button()

    assert window.measure_tab.export_btn.text() == "Export Average…"
    assert "rew-style" in window.measure_tab.export_btn.toolTip().lower()
    assert window.measure_tab.export_btn.isEnabled() is True
    assert window.measure_tab.upload_btn.isEnabled() is True


def test_sync_export_button_disables_upload_without_average_even_with_variation(
    make_main_window,
) -> None:
    window = _variation_window(make_main_window, variation_mode=True)
    window.measure.average = None

    window.measure_io.sync_export_button()

    assert window.measure_tab.export_btn.isEnabled() is True
    assert window.measure_tab.upload_btn.isEnabled() is False


def test_export_variation_uses_current_variation_data(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    written: dict[str, object] = {}
    save_path = tmp_path / "out.txt"

    monkeypatch.setattr(
        "dms.ui.measure_io.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(save_path), ""),
    )
    monkeypatch.setattr(
        "dms.ui.measure_io.export_variation",
        lambda **kwargs: written.update(kwargs),
    )

    window = _variation_window(
        make_main_window,
        variation_mode=True,
        session=SessionData(rig="GRAS", brand="DMS", model="Example"),
    )
    window.measure.kept_curves = [(np.array([100.0]), np.array([1.0]))]

    window.measure_io.export()

    assert written["level_mode"] == "ref_1khz"
    assert np.array_equal(written["freqs"], np.array([100.0]))
    assert np.array_equal(written["p10_db"], np.array([-2.0]))
    assert np.array_equal(written["p25_db"], np.array([-1.0]))
    assert np.array_equal(written["median_db"], np.array([0.0]))
    assert np.array_equal(written["p75_db"], np.array([1.0]))
    assert np.array_equal(written["p90_db"], np.array([2.0]))
    assert written["output_path"] == save_path
    assert written["n_sweeps"] == 1
    assert written["smoothing_fraction"] == 48
    assert window._settings.get("export_directory") == str(tmp_path)
    assert window._statusbar.currentMessage().startswith("Exported variation:")


def test_export_variation_empty_state_has_variation_copy(make_main_window, monkeypatch) -> None:
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.measure_io.QMessageBox.information",
        lambda _parent, title, message: info_calls.append((title, message)),
    )

    window = _variation_window(make_main_window, variation_mode=True)
    window.measure.variation = None

    window.measure_io.export()

    assert info_calls == [("Nothing to Export", "No variation band available yet.")]


def test_two_channel_variation_exports_without_a_plot_redraw(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    written: dict[str, object] = {}
    monkeypatch.setattr(
        "dms.ui.measure_io.export_variation",
        lambda **kwargs: written.update(kwargs),
    )
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.measure_io.QMessageBox.information",
        lambda _parent, title, message: info_calls.append((title, message)),
    )
    window = make_main_window(
        settings={
            "measure_two_channel_enabled": True,
            "measure_two_channel_bottom_mode": "separate",
        }
    )
    redraws: list[bool] = []
    window.measure.curves_changed.connect(redraws.append)
    freqs = np.array([100.0, 1000.0, 10000.0])

    def flat(level: float):
        return freqs, np.full(3, level)

    window.measure.two_channel_pairs = [
        TwoChannelCurvePair(flat(1.0), flat(-2.0)),
        TwoChannelCurvePair(flat(3.0), flat(-6.0)),
    ]
    window.measure.recompute_two_channel_results()
    window.measure.on_two_channel_selection_changed("channel_2")

    window.measure_io.export_variation(str(tmp_path / "variation.txt"))

    assert info_calls == []
    assert redraws == []
    assert written["n_sweeps"] == 2
    # Channel 2 only: its two flat curves sit at -2 and -6 dB.
    np.testing.assert_allclose(written["median_db"], -4.0, atol=1e-9)
    np.testing.assert_allclose(written["p10_db"], -5.6, atol=1e-9)
    np.testing.assert_allclose(written["p90_db"], -2.4, atol=1e-9)
