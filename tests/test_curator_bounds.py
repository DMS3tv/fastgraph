from pathlib import Path

import numpy as np
import pytest

from dms.curator.bounds import load_preference_bounds


def test_load_preference_bounds_uses_raw_fr_files(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("100 5\n1000 6\n", encoding="utf-8")
    lower.write_text("100 -5\n1000 -6\n", encoding="utf-8")

    bounds = load_preference_bounds(upper, lower)

    assert bounds.enabled is True
    assert bounds.upper_path == upper
    assert bounds.lower_path == lower
    assert np.allclose(bounds.upper.mag_db, [5.0, 6.0])
    assert np.allclose(bounds.lower.mag_db, [-5.0, -6.0])


def test_load_preference_bounds_rejects_invalid_file(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("100 5\n1000 6\n", encoding="utf-8")
    lower.write_text("not data\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_preference_bounds(upper, lower)
