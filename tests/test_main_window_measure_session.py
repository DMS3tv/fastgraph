"""Measure session save/load, dirty tracking, close prompt and recovery."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtWidgets import QMessageBox

import dms.ui.measure_controller as measure_controller_module
import dms.ui.measure_io as measure_io_module
from dms.measure_queue import QueueState
from dms.measure_session import MeasureSession
from dms.recovery import RecoveryCandidate
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair
from dms.ui.measure_dialogs import MeasureRecoveryDialog


def _curve(offset: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    freqs = np.geomspace(20.0, 20000.0, 48)
    return freqs, np.linspace(4.0, -4.0, 48) + offset


def _hrtf_name() -> str:
    options = sorted(Path(measure_controller_module.HRTF_DIR).glob("*.txt"))
    if not options:
        pytest.skip("No HRTF files are installed in this working tree.")
    return options[0].stem


def _save_to(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        measure_io_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(path), ""),
    )


def _open_from(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        measure_io_module.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(path), ""),
    )


def test_session_menu_sits_at_the_start_of_the_export_row(make_main_window) -> None:
    window = make_main_window()
    row = window._plots._footer_widget.layout()

    assert row.itemAt(0).widget() is window.measure_tab.session_menu_btn
    assert window.measure_tab.session_menu_btn.property("menuButton") is True
    assert [action.text() for action in window.measure_io._session_menu.actions()] == [
        "New Session",
        "Save Session",
        "Save Session As…",
        "Load Session…",
    ]


def test_save_then_load_restores_curves_metadata_hrtf_and_level_mode(
    tmp_path, monkeypatch, make_main_window
) -> None:
    window = make_main_window()
    hrtf_name = _hrtf_name()

    window.measure.kept_curves.extend([_curve(), _curve(1.0)])
    window.measure.kept_sweep_meta.extend(
        [
            {"timing_quality": (12.0, 11.0, 2.0, 40.0)},
            {},
        ]
    )
    window._session = SessionData(rig="Rig 2", brand="Acme", model="Widget")
    window.measure.recompute_average()
    window.measure_tab.hrtf_combo.setCurrentIndex(window.measure_tab.hrtf_combo.findText(hrtf_name))
    window.measure_tab.hrtf_toggle.setChecked(True)
    window.measure_io.mark_dirty()

    path = tmp_path / "demo.fastgraph-measure.json"
    _save_to(monkeypatch, path)
    assert window.measure_io.save_session() is True
    assert path.is_file()
    assert window.measure_io.dirty is False
    assert window.measure_io.session_path == path

    # A fresh window must rebuild the same workspace from that one file.
    other = make_main_window()
    _open_from(monkeypatch, path)
    assert other.measure_io.load_session() is True

    assert len(other.measure.kept_curves) == 2
    # Arrays serialize rounded to six decimals.
    np.testing.assert_allclose(
        other.measure.kept_curves[1][1], window.measure.kept_curves[1][1], atol=1e-5
    )
    assert other._session.brand == "Acme"
    assert other._session.model == "Widget"
    assert other._session.rig == "Rig 2"
    assert other.measure.hrtf is not None
    assert Path(other.measure.hrtf.path).stem == hrtf_name
    assert other.measure_tab.hrtf_toggle.isChecked() is True
    assert other.measure.level_mode() == "ref_1khz"
    assert other.measure.average is not None
    assert other.measure.kept_sweep_meta[0]["timing_quality"] == (12.0, 11.0, 2.0, 40.0)
    assert other.measure_io.dirty is False


def test_two_channel_pairs_survive_a_round_trip(tmp_path, monkeypatch, make_main_window) -> None:
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    window.measure.two_channel_pairs.append(
        TwoChannelCurvePair(channel_1=_curve(), channel_2=_curve(2.0))
    )
    window.measure.kept_pair_meta.append({})
    window.measure.recompute_two_channel_results()

    path = tmp_path / "pairs.fastgraph-measure.json"
    _save_to(monkeypatch, path)
    assert window.measure_io.save_session() is True

    other = make_main_window()
    _open_from(monkeypatch, path)
    assert other.measure_io.load_session() is True

    assert other.measure.two_channel_enabled is True
    assert len(other.measure.two_channel_pairs) == 1
    np.testing.assert_allclose(
        other.measure.two_channel_pairs[0].channel_2[1], _curve(2.0)[1], atol=1e-5
    )


def test_dirty_flag_and_window_title_track_the_session(
    tmp_path, monkeypatch, make_main_window
) -> None:
    window = make_main_window()
    assert window.measure_io.dirty is False
    assert "•" not in window.windowTitle()

    window.measure.kept_curves.append(_curve())
    window.measure.kept_sweep_meta.append({})
    window.measure.recompute_average()
    window.measure_io.mark_dirty()
    assert window.measure_io.dirty is True
    assert window.windowTitle().endswith("*")

    path = tmp_path / "tracked.fastgraph-measure.json"
    _save_to(monkeypatch, path)
    assert window.measure_io.save_session() is True
    assert window.measure_io.dirty is False
    assert window.windowTitle().endswith("• tracked")

    window.measure.undo_last_measurement()
    assert window.measure_io.dirty is True
    assert window.windowTitle().endswith("• tracked*")

    # New Session offers to save first; the prompt is modal, so answer it here.
    window.measure_io._confirm_discard_measure_session = lambda: True
    window.measure_io.new_session()
    assert window.measure_io.dirty is False
    assert window.measure_io.session_path is None
    assert "•" not in window.windowTitle()


def test_save_as_asks_before_replacing_another_file(
    tmp_path, monkeypatch, make_main_window
) -> None:
    window = make_main_window()
    window.measure.kept_curves.append(_curve())
    window.measure.kept_sweep_meta.append({})

    existing = tmp_path / "taken.fastgraph-measure.json"
    existing.write_text("{}", encoding="utf-8")
    _save_to(monkeypatch, existing)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    assert window.measure_io.save_session() is False
    assert existing.read_text(encoding="utf-8") == "{}"


def test_close_prompt_offers_save_only_while_dirty(tmp_path, monkeypatch, make_main_window) -> None:
    from conftest import REAL_CONFIRM_MEASURE_CLOSE

    window = make_main_window()
    confirm = lambda: REAL_CONFIRM_MEASURE_CLOSE(window.measure_io)  # noqa: E731
    assert confirm() is True

    seen: dict[str, object] = {}
    answers: list[object] = []

    class _Dialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def setStandardButtons(self, buttons) -> None:
            seen["buttons"] = buttons

        def setDefaultButton(self, button) -> None:
            seen["default"] = button

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

        def exec(self):
            return answers.pop()

    monkeypatch.setattr(
        measure_io_module,
        "QMessageBox",
        type(
            "QMessageBoxStub",
            (),
            {
                "Icon": measure_io_module.QMessageBox.Icon,
                "StandardButton": measure_io_module.QMessageBox.StandardButton,
                "__new__": lambda cls, *a, **k: _Dialog(),
            },
        ),
    )

    window.measure.kept_curves.append(_curve())
    window.measure.kept_sweep_meta.append({})
    window.measure_io.mark_dirty()

    answers.append(measure_io_module.QMessageBox.StandardButton.Cancel)
    assert confirm() is False
    assert seen["buttons"] & measure_io_module.QMessageBox.StandardButton.Save
    # No file yet, so the safe default is Cancel rather than a Save dialog.
    assert seen["default"] == measure_io_module.QMessageBox.StandardButton.Cancel

    answers.append(measure_io_module.QMessageBox.StandardButton.Discard)
    assert confirm() is True

    saved: list[bool] = []
    window.measure_io.save_session = lambda **_kwargs: saved.append(True) or True
    answers.append(measure_io_module.QMessageBox.StandardButton.Save)
    assert confirm() is True
    assert saved == [True]


def test_keeping_a_measurement_schedules_a_recovery_snapshot(make_main_window) -> None:
    window = make_main_window()
    scheduled: list[dict] = []
    window.measure_io._measure_recovery.schedule = lambda snapshot: scheduled.append(snapshot)

    window._state = QueueState.PASS_FAIL
    window._queue_target = 1
    window._queue_index = 0
    window._pending_curve = _curve()
    window.measure.on_keep()

    assert len(window.measure.kept_curves) == 1
    assert len(window.measure.kept_sweep_meta) == 1
    assert scheduled and scheduled[-1]["sweeps"]
    assert window.measure_io.dirty is True


def test_startup_recovery_restores_a_candidate(monkeypatch, make_main_window) -> None:
    window = make_main_window()
    recovered = MeasureSession(
        metadata=SessionData(rig="Rig", brand="Recovered", model="Unit"),
    )
    freqs, mag_db = _curve(3.0)
    recovered.add_sweep(freqs, mag_db)
    candidate = RecoveryCandidate(
        path=Path("current.fastgraph-measure.json"),
        kind="current",
        preserved_at=datetime.now(),
        summary="1 sweeps",
    )

    window.measure_io._measure_recovery.candidates = lambda: [candidate]
    window.measure_io._measure_recovery.restore = lambda _candidate: recovered
    enabled: list[bool] = []
    window.measure_io._measure_recovery.enable = lambda: enabled.append(True)

    class _Dialog:
        # The window compares against the module-level class, which this stub
        # replaces, so it has to carry the same action constants.
        RESTORE = MeasureRecoveryDialog.RESTORE
        DISCARD = MeasureRecoveryDialog.DISCARD
        KEEP = MeasureRecoveryDialog.KEEP
        action = MeasureRecoveryDialog.RESTORE

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self) -> int:
            return 1

        def selected_candidate(self):
            return candidate

    monkeypatch.setattr(measure_io_module, "MeasureRecoveryDialog", _Dialog)
    window.measure_io._measure_recovery.schedule = lambda _snapshot: None

    window.measure_io.initialize_recovery()

    assert len(window.measure.kept_curves) == 1
    assert window._session.brand == "Recovered"
    assert window.measure_io.dirty is True
    assert window.measure_io.session_path is None
    assert enabled == [True]


def test_console_session_commands_save_and_load(tmp_path, make_main_window) -> None:
    window = make_main_window()
    window.measure.kept_curves.append(_curve())
    window.measure.kept_sweep_meta.append({})
    path = tmp_path / "console.fastgraph-measure.json"

    window.commands._run_measure_command(["session", "save", str(path)])
    assert path.is_file()
    assert window.measure_io.dirty is False

    window.measure.kept_curves.clear()
    window.measure.kept_sweep_meta.clear()
    window.commands._run_measure_command(["session", "load", str(path)])
    assert len(window.measure.kept_curves) == 1
