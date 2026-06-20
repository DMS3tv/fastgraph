from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from dms.hrtf import HRTFCurve
from uuid import uuid4

import numpy as np


CurveKind = Literal["fr", "variation"]


@dataclass(frozen=True)
class CurveData:
    kind: CurveKind
    freqs: np.ndarray
    mag_db: np.ndarray | None = None
    p10_db: np.ndarray | None = None
    p25_db: np.ndarray | None = None
    median_db: np.ndarray | None = None
    p75_db: np.ndarray | None = None
    p90_db: np.ndarray | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def shifted(self, amount_db: float) -> "CurveData":
        return replace(
            self,
            mag_db=_shift_optional(self.mag_db, amount_db),
            p10_db=_shift_optional(self.p10_db, amount_db),
            p25_db=_shift_optional(self.p25_db, amount_db),
            median_db=_shift_optional(self.median_db, amount_db),
            p75_db=_shift_optional(self.p75_db, amount_db),
            p90_db=_shift_optional(self.p90_db, amount_db),
        )


@dataclass
class LayerState:
    curve: CurveData
    source_path: Path
    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    visible: bool = True
    color: str = "#15f4ee"
    vertical_offset_db: float = 0.0
    hrtf: HRTFCurve | None = None
    is_combined: bool = False
    source_layer_ids: list[str] = field(default_factory=list)


@dataclass
class PreferenceBounds:
    enabled: bool = False
    upper: CurveData | None = None
    lower: CurveData | None = None
    upper_path: Path | None = None
    lower_path: Path | None = None


@dataclass
class ExportText:
    title: str = "Curator"
    fixture: str = ""
    hrtf_note: str = "Test Fixture"
    notes: str = ""


@dataclass
class GraphState:
    layers: list[LayerState] = field(default_factory=list)
    bounds: PreferenceBounds = field(default_factory=PreferenceBounds)
    y_min: float = -20.0
    y_max: float = 20.0
    background: str = "#101217"
    aspect_locked_25db: bool = True
    export_text: ExportText = field(default_factory=ExportText)


def _shift_optional(values: np.ndarray | None, amount_db: float) -> np.ndarray | None:
    if values is None:
        return None
    return values + float(amount_db)
