from __future__ import annotations

from pathlib import Path

from dms.curator.models import PreferenceBounds
from dms.curator.parser import parse_fr_txt


def load_preference_bounds(upper_path: str | Path, lower_path: str | Path) -> PreferenceBounds:
    upper = parse_fr_txt(upper_path, label="Upper bound")
    lower = parse_fr_txt(lower_path, label="Lower bound")
    return PreferenceBounds(
        enabled=True,
        upper=upper,
        lower=lower,
        upper_path=Path(upper_path),
        lower_path=Path(lower_path),
    )
