from pathlib import Path
from types import MethodType, SimpleNamespace

import numpy as np

import dms.ui.main_window as main_window_module
from dms.export import build_filename
from dms.hrtf import HRTFCurve
from dms.session import SessionData
from dms.ui.main_window import AppState, MainWindow


class _Button:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""
        self.enabled = False
        self.object_name = ""
        self.role = ""

    def setText(self, value: str) -> None:
        self.text = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setEnabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def setObjectName(self, value: str) -> None:
        self.object_name = value

    def setRole(self, value: str) -> None:
        self.role = value


class _LineEdit:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def text(self) -> str:
        return self.value

    def setText(self, value: str) -> None:
        self.value = value


class _Settings:
    def __init__(self, directory: str = "") -> None:
        self.values = {"export_directory": directory}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value) -> None:
        self.values[key] = value


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


def _bind(fake, *names: str) -> None:
    for name in names:
        setattr(fake, name, MethodType(getattr(MainWindow, name), fake))


def _batch_window(tmp_path: Path, hrtf: HRTFCurve):
    freqs = np.array([100.0, 1000.0])
    kept = [
        (freqs, np.array([10.0, 20.0])),
        (freqs, np.array([14.0, 24.0])),
    ]
    events: list[tuple[str, tuple, dict]] = []
    triggers: list[str] = []
    statuses: list[str] = []
    fake = SimpleNamespace(
        _state=AppState.IDLE,
        _average=(freqs, np.array([12.0, 22.0])),
        _kept_curves=kept,
        _hrtf=hrtf,
        _session=SessionData(rig="GRAS", brand="DMS", model="Example"),
        _export_dir_input=_LineEdit(str(tmp_path)),
        _settings=_Settings(str(tmp_path)),
        _statusbar=SimpleNamespace(showMessage=statuses.append),
        _log_event=lambda *args, **kwargs: events.append(("log", args, kwargs)),
        _run_automation_trigger=triggers.append,
        _measure_export_directory=lambda: tmp_path,
        _confirm_export_all_overwrite=lambda conflicts: True,
    )
    _bind(
        fake,
        "_export_all_unavailable_reason",
        "_average_curve_with_hrtf",
        "_variation_from_kept_curves",
        "_level_mode",
        "_spl_offset_db",
    )
    return fake, events, triggers, statuses


def _data_rows(path: Path) -> np.ndarray:
    rows = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("*")
    ]
    return np.loadtxt(rows)


def test_export_all_writes_four_named_files_with_selected_inactive_hrtf(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake, events, triggers, statuses = _batch_window(tmp_path, _standard_hrtf(tmp_path))
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    MainWindow._export_all_measure_outputs(fake)

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
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake, _events, _triggers, _statuses = _batch_window(tmp_path, _population_hrtf(tmp_path))
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *_args: None)

    MainWindow._export_all_measure_outputs(fake)

    comp_avg = _data_rows(tmp_path / "DMS Example GRAS COMP AVG.txt")
    assert np.allclose(comp_avg[:, 1], [9.0, 18.0])
    raw_var = _data_rows(tmp_path / "DMS Example GRAS RAW VAR.txt")
    comp_var = _data_rows(tmp_path / "DMS Example GRAS COMP VAR.txt")
    assert not np.allclose(raw_var[:, 1:], comp_var[:, 1:])
    assert np.all(comp_var[:, 1] <= comp_var[:, 3])
    assert np.all(comp_var[:, 3] <= comp_var[:, 5])


def test_export_all_generation_failure_preserves_existing_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake, _events, triggers, _statuses = _batch_window(tmp_path, _standard_hrtf(tmp_path))
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

    MainWindow._export_all_measure_outputs(fake)

    assert all((tmp_path / name).read_text(encoding="utf-8") == "original" for name in names)
    assert triggers == []


def test_export_all_collision_cancel_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    fake, _events, triggers, _statuses = _batch_window(tmp_path, _standard_hrtf(tmp_path))
    conflict = tmp_path / "DMS Example GRAS RAW AVG.txt"
    conflict.write_text("original", encoding="utf-8")
    fake._confirm_export_all_overwrite = lambda conflicts: False
    calls: list[bool] = []
    monkeypatch.setattr(
        main_window_module,
        "export_curve",
        lambda **_kwargs: calls.append(True),
    )

    MainWindow._export_all_measure_outputs(fake)

    assert conflict.read_text(encoding="utf-8") == "original"
    assert calls == []
    assert triggers == []


def test_measure_action_switches_between_squiglink_and_export_all() -> None:
    calls: list[str] = []
    fake = SimpleNamespace(
        _theme_controller=SimpleNamespace(brand_mode=False),
        _upload_to_squiglink=lambda: calls.append("squiglink"),
        _export_all_measure_outputs=lambda: calls.append("all"),
    )
    _bind(fake, "_brand_mode_active")

    MainWindow._run_measure_upload_action(fake)
    fake._theme_controller.brand_mode = True
    MainWindow._run_measure_upload_action(fake)

    assert calls == ["squiglink", "all"]


def test_brand_export_all_button_reports_missing_requirements() -> None:
    fake = SimpleNamespace(
        _theme_controller=SimpleNamespace(brand_mode=True),
        _state=AppState.IDLE,
        _average=None,
        _variation=None,
        _kept_curves=[],
        _hrtf=None,
        _export_btn=_Button(),
        _upload_btn=_Button(),
        _bottom_view_mode=lambda: "average",
    )
    _bind(fake, "_export_all_unavailable_reason")

    MainWindow._sync_export_button(fake)
    assert fake._upload_btn.text == "Export All…"
    assert fake._upload_btn.enabled is False
    assert "average" in fake._upload_btn.tooltip.lower()

    fake._average = (np.array([100.0]), np.array([0.0]))
    fake._kept_curves = [fake._average, fake._average]
    fake._hrtf = object()
    MainWindow._sync_export_button(fake)
    assert fake._upload_btn.enabled is True
    assert fake._upload_btn.role == "primary"


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
