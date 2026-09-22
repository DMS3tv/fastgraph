from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import numpy as np

from dms import brand_brand

if TYPE_CHECKING:
    from dms.hrtf import HRTFCurve

CurveKind = Literal["fr", "variation"]

_PERCENTILE_BANDS = ("p10_db", "p25_db", "median_db", "p75_db", "p90_db")


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
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def map_bands(self, fn: Callable[[np.ndarray], np.ndarray]) -> CurveData:
        """Return a copy with ``fn`` applied to every band array that is set."""
        changes = {}
        for name in ("mag_db", *_PERCENTILE_BANDS):
            values = getattr(self, name)
            if values is not None:
                changes[name] = fn(values)
        return replace(self, **changes)

    def bands(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """p10, p25, median, p75 and p90; raises unless all five are present."""
        values = tuple(getattr(self, name) for name in _PERCENTILE_BANDS)
        if any(item is None for item in values):
            raise ValueError("This curve has no complete variation band.")
        return values

    def shifted(self, amount_db: float) -> CurveData:
        return self.map_bands(lambda values: values + float(amount_db))


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
    # Snapshot of each source layer's inputs when the combination was built,
    # so the row can show when a combined layer no longer matches its sources.
    source_signature: dict[str, tuple] = field(default_factory=dict)
    stale: bool = False


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
    brand_footer_left_1: str = brand_brand.FOOTER_LEFT_1_DEFAULT
    brand_footer_left_2: str = brand_brand.FOOTER_LEFT_2_DEFAULT
    brand_legend_bounds_label: str = brand_brand.LEGEND_BOUNDS_LABEL_DEFAULT
    brand_legend_variation_label: str = brand_brand.LEGEND_VARIATION_LABEL_DEFAULT


@dataclass
class GraphState:
    layers: list[LayerState] = field(default_factory=list)
    bounds: PreferenceBounds = field(default_factory=PreferenceBounds)
    y_min: float = -17.5
    y_max: float = 17.5
    background: str = "#101217"
    aspect_locked_25db: bool = True
    smoothing_fraction: int = 48
    show_layer_names: bool = True
    brand_clean_slate: bool = False
    export_text: ExportText = field(default_factory=ExportText)
