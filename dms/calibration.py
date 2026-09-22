import contextlib

from dms.file_io import atomic_write_json, load_json_with_backup
from dms.settings_manager import _config_dir


class CalibrationStore:
    """Persistent per-device SPL calibration storage."""

    def __init__(self) -> None:
        self._path = _config_dir() / "calibration.json"
        self._data: dict[str, float] = {}
        # Set when calibration.json was damaged and moved aside.
        self.load_error: str | None = None
        self._load()

    def get_sensitivity(self, device_name: str) -> float | None:
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

    def rms_to_dbspl(self, device_name: str, rms_fs: float) -> float | None:
        """Convert normalized RMS (0-1 FS) to dB SPL. Returns None if not calibrated."""
        sens = self.get_sensitivity(device_name)
        if sens is None or rms_fs <= 0:
            return None
        pa = rms_fs * sens
        return 20.0 * __import__("math").log10(pa / 20e-6)

    def _load(self) -> None:
        loaded, error = load_json_with_backup(self._path)
        self.load_error = error
        if loaded is None:
            self._data = {}
            return
        # A hand-edited file can hold non-numeric sensitivities; drop those
        # rather than crashing the first dB SPL conversion that uses them.
        clean: dict[str, float] = {}
        for name, value in loaded.items():
            try:
                clean[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
        self._data = clean

    def _save(self) -> None:
        with contextlib.suppress(Exception):
            atomic_write_json(self._path, self._data, mode=0o600)
