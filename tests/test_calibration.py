import json
from pathlib import Path

from helpers import corrupt_backups

from dms.calibration import CalibrationStore


def test_calibration_store_backs_up_a_corrupt_file(monkeypatch, tmp_path: Path) -> None:
    import dms.calibration as calibration_module

    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "calibration.json").write_text("]]not json[[", encoding="utf-8")

    store = CalibrationStore()

    assert store.load_error is not None
    assert len(corrupt_backups(tmp_path, "calibration.json")) == 1
    assert store.get_sensitivity("Mic") is None
    store.set_sensitivity("Mic", 0.5)
    assert CalibrationStore().get_sensitivity("Mic") == 0.5


def test_calibration_store_drops_non_numeric_entries(monkeypatch, tmp_path: Path) -> None:
    import dms.calibration as calibration_module

    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "calibration.json").write_text(
        json.dumps({"Good": 1.5, "Bad": "not a number"}), encoding="utf-8"
    )
    store = CalibrationStore()
    assert store.get_sensitivity("Good") == 1.5
    assert store.get_sensitivity("Bad") is None
    assert store.load_error is None
