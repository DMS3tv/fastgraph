from pathlib import Path
from types import SimpleNamespace

import numpy as np

import dms.ui.main_window as main_window_module
from dms.export import build_filename
from dms.hrtf import HRTFCurve
from dms.session import SessionData


def _standard_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "standard.txt"
    path.write_text("100 1\n1000 2\n", encoding="utf-8")
    return HRTFCurve(str(path))


def _population_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "population.txt"
    path.write_text(
        "100 1 2 3 4 5\n1000 2 3 4 5 6\n",
        encoding="utf-8",
    )
    return HRTFCurve(str(path))


def _batch_window(make_main_window, tmp_path: Path, hrtf: HRTFCurve):
    freqs = np.array([100.0, 1000.0])
    events: list[tuple[str, tuple, dict]] = []
    triggers: list[str] = []
    statuses: list[str] = []
    window = make_main_window(session=SessionData(rig="GRAS", brand="DMS", model="Example"))
    window._kept_curves = [
        (freqs, np.array([10.0, 20.0])),
        (freqs, np.array([14.0, 24.0])),
    ]
    window._average = (freqs, np.array([12.0, 22.0]))
    window._hrtf = hrtf
    window._export_dir_input.setText(str(tmp_path))
    window._statusbar.messageChanged.connect(statuses.append)
    window._log_event = lambda *args, **kwargs: events.append(("log", args, kwargs))
    window._run_automation_trigger = triggers.append
    window._confirm_export_all_overwrite = lambda conflicts: True
    return window, events, triggers, statuses


def _data_rows(path: Path) -> np.ndarray:
    rows = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("*")
    ]
    return np.loadtxt(rows)


def test_export_all_writes_four_named_files_with_selected_inactive_hrtf(
    make_main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window, events, triggers, statuses = _batch_window(
        make_main_window, tmp_path, _standard_hrtf(tmp_path)
    )
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    window._export_all_measure_outputs()

    names = {
        "DMS Example GRAS RAW AVG.txt",
        "DMS Example GRAS COMP AVG.txt",
        "DMS Example GRAS RAW VAR.txt",
        "DMS Example GRAS COMP VAR.txt",
    }
    assert {path.name for path in tmp_path.glob("*.txt")} >= names | {"standard.txt"}
    raw_avg = _data_rows(tmp_path / "DMS Example GRAS RAW AVG.txt")
    comp_avg = _data_rows(tmp_path / "DMS Example GRAS COMP AVG.txt")
    assert np.allclose(raw_avg[:, 1], [12.0, 22.0])
    assert np.allclose(comp_avg[:, 1], [11.0, 20.0])
    for name in ("RAW AVG", "COMP AVG"):
        text = (tmp_path / f"DMS Example GRAS {name}.txt").read_text()
        assert "* Smoothing: 1/48 octave" in text
        assert "* Normalization: 1 kHz reference offset only" in text
    assert "* Compensated: No" in (tmp_path / "DMS Example GRAS RAW VAR.txt").read_text()
    assert "* Compensated: Yes" in (tmp_path / "DMS Example GRAS COMP VAR.txt").read_text()
    assert triggers == ["export_complete"]
    assert len(events) == 1
    assert statuses == [f"Exported all Measure files: {tmp_path}"]
    assert messages[0][0] == "Export All Complete"
    assert all(name in messages[0][1] for name in names)


def test_export_all_uses_population_median_and_variation_envelope(
    make_main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window, _events, _triggers, _statuses = _batch_window(
        make_main_window, tmp_path, _population_hrtf(tmp_path)
    )
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *_args: None)

    window._export_all_measure_outputs()

    comp_avg = _data_rows(tmp_path / "DMS Example GRAS COMP AVG.txt")
    assert np.allclose(comp_avg[:, 1], [9.0, 18.0])
    raw_var = _data_rows(tmp_path / "DMS Example GRAS RAW VAR.txt")
    comp_var = _data_rows(tmp_path / "DMS Example GRAS COMP VAR.txt")
    assert not np.allclose(raw_var[:, 1:], comp_var[:, 1:])
    assert np.all(comp_var[:, 1] <= comp_var[:, 3])
    assert np.all(comp_var[:, 3] <= comp_var[:, 5])


def test_export_all_generation_failure_preserves_existing_files(
    make_main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window, _events, triggers, _statuses = _batch_window(
        make_main_window, tmp_path, _standard_hrtf(tmp_path)
    )
    names = [
        "DMS Example GRAS RAW AVG.txt",
        "DMS Example GRAS COMP AVG.txt",
        "DMS Example GRAS RAW VAR.txt",
        "DMS Example GRAS COMP VAR.txt",
    ]
    for name in names:
        (tmp_path / name).write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        main_window_module,
        "export_variation",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("simulated failure")),
    )
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *_args: None)

    window._export_all_measure_outputs()

    assert all((tmp_path / name).read_text(encoding="utf-8") == "original" for name in names)
    assert triggers == []


def test_export_all_collision_cancel_writes_nothing(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    window, _events, triggers, _statuses = _batch_window(
        make_main_window, tmp_path, _standard_hrtf(tmp_path)
    )
    conflict = tmp_path / "DMS Example GRAS RAW AVG.txt"
    conflict.write_text("original", encoding="utf-8")
    window._confirm_export_all_overwrite = lambda conflicts: False
    calls: list[bool] = []
    monkeypatch.setattr(
        main_window_module,
        "export_curve",
        lambda **_kwargs: calls.append(True),
    )

    window._export_all_measure_outputs()

    assert conflict.read_text(encoding="utf-8") == "original"
    assert calls == []
    assert triggers == []


def test_measure_action_switches_between_squiglink_and_export_all(make_main_window) -> None:
    calls: list[str] = []
    window = make_main_window()
    window._theme_controller.set_brand_mode(False, persist=False)
    window.squiglink.upload = lambda: calls.append("squiglink")
    window._export_all_measure_outputs = lambda: calls.append("all")

    window._run_measure_upload_action()
    window._theme_controller.set_brand_mode(True, persist=False)
    window._run_measure_upload_action()

    assert calls == ["squiglink", "all"]


def test_brand_export_all_button_reports_missing_requirements(make_main_window) -> None:
    window = make_main_window()
    window._theme_controller.set_brand_mode(True, persist=False)
    assert window._bottom_view_mode() == "average"

    window._sync_export_button()
    assert window._upload_btn.text() == "Export All…"
    assert window._upload_btn.isEnabled() is False
    assert "average" in window._upload_btn.toolTip().lower()

    window._average = (np.array([100.0]), np.array([0.0]))
    window._kept_curves = [window._average, window._average]
    window._hrtf = object()
    window._sync_export_button()
    assert window._upload_btn.isEnabled() is True
    assert window._upload_btn.role() == "primary"


def test_export_average_equals_displayed_curve(make_main_window, tmp_path: Path) -> None:
    """Export Average writes the smoothed curve the bottom viewport draws."""
    window = make_main_window()
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 400)
    rng = np.random.default_rng(7)
    window._kept_curves = [
        (freqs, rng.normal(0.0, 3.0, freqs.size)),
        (freqs, rng.normal(0.0, 3.0, freqs.size)),
    ]
    window._recompute_average()
    displayed = window._bottom_curve_for_display()
    assert displayed is not None

    output = tmp_path / "average.txt"
    window._export_average(str(output))

    text = output.read_text(encoding="utf-8")
    assert "* Smoothing: 1/48 octave" in text
    rows = _data_rows(output)
    # The file rounds frequencies to 4 and magnitudes to 6 decimal places.
    np.testing.assert_allclose(rows[:, 0], displayed[0], atol=1e-3)
    np.testing.assert_allclose(rows[:, 1], displayed[1], atol=1e-5)


def test_export_all_comp_average_matches_export_average(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    """Export All's COMP AVG file is byte-identical to Export Average's."""

    class _FixedDatetime:
        @staticmethod
        def now():
            return SimpleNamespace(strftime=lambda _fmt: "2026-01-01 00:00:00")

    monkeypatch.setattr("dms.export.datetime", _FixedDatetime)
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *_args: None)
    window = make_main_window()
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 400)
    rng = np.random.default_rng(11)
    window._kept_curves = [
        (freqs, rng.normal(0.0, 3.0, freqs.size)),
        (freqs, rng.normal(0.0, 3.0, freqs.size)),
    ]
    window._recompute_average()
    window._hrtf = _standard_hrtf(tmp_path)
    window._is_hrtf_active = lambda: True

    single_dir = tmp_path / "single"
    all_dir = tmp_path / "all"
    single_dir.mkdir()
    all_dir.mkdir()
    window._export_average(str(single_dir / "average.txt"))
    window._export_dir_input.setText(str(all_dir))
    window._export_all_measure_outputs()

    comp_name = build_filename(window._session, compensated=True)
    assert (all_dir / comp_name).read_bytes() == (single_dir / "average.txt").read_bytes()
