import json
import os
from pathlib import Path
from typing import Optional
from dms.file_io import atomic_write_json
from dms.settings_manager import _config_dir


class CalibrationStore:
    """Persistent per-device SPL calibration storage."""

    def __init__(self) -> None:
        self._path = _config_dir() / "calibration.json"
        self._data: dict[str, float] = {}
        self.load_error: str | None = None
        self._load()

    def get_sensitivity(self, device_name: str) -> Optional[float]:
        """Return Pa/FS sensitivity for device, or None if uncalibrated."""
        return self._data.get(device_name)

    def set_sensitivity(self, device_name: str, sensitivity_pa_per_fs: float) -> None:
        """Store calibrated sensitivity (Pa/FS) for device."""
        self._data[device_name] = sensitivity_pa_per_fs
        self._save()

    def is_calibrated(self, device_name: str) -> bool:
        return device_name in self._data

    def clear(self, device_name: str) -> None:
        self._data.pop(device_name, None)
        self._save()

    def rms_to_dbspl(self, device_name: str, rms_fs: float) -> Optional[float]:
        """Convert normalized RMS (0-1 FS) to dB SPL. Returns None if not calibrated."""
        sens = self.get_sensitivity(device_name)
        if sens is None or rms_fs <= 0:
            return None
        pa = rms_fs * sens
        return 20.0 * __import__("math").log10(pa / 20e-6)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path) as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}
            corrupt_path = self._path.with_name(self._path.name + ".corrupt")
            try:
                os.replace(self._path, corrupt_path)
            except OSError:
                pass
            self.load_error = (
                f"{self._path.name} was corrupt and has been reset; "
                f"backup saved as {corrupt_path.name}"
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_json(self._path, self._data)
        except Exception:
            pass
