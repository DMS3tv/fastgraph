import json
import os
from pathlib import Path
from typing import Any


_DEFAULTS: dict[str, Any] = {
    "sweep_duration": 2.0,
    "sample_rate": 48000,
    "buffer_size": 1024,
    "f_low": 20.0,
    "f_high": 20000.0,
    "output_device": None,
    "input_device": None,
    "input_channel": 0,
    "queue_count": 5,
    "hrtf_path": None,
    "hrtf_invert": False,
    "pre_sweep_silence": 0.2,
    "post_sweep_silence": 0.5,
    "latency": "low",
    "update_check_enabled": True,
    "update_feed_url": "",
}


class SettingsManager:
    def __init__(self) -> None:
        self._path = _config_dir() / "settings.json"
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    def get(self, key: str) -> Any:
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def update(self, updates: dict[str, Any]) -> None:
        self._data.update(updates)
        self._save()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass


def _config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "DMSFastgraph"
