"""Pure, serializable state for a Measure workspace session.

The Measure tab keeps its work in plain Python state on the main window:
``_kept_curves`` (single-channel sweeps), ``_two_channel_pairs`` (paired L/R
captures), the :class:`~dms.session.SessionData` describing the device under
test, the level mode and the selected HRTF. This module is that state's
on-disk shape, so a Measure session can be saved, reopened and recovered after
a crash exactly the way an R&D session can.

Nothing here touches Qt, the filesystem or the audio engine:
:mod:`dms.measure_persistence` owns file I/O and :mod:`dms.measure_recovery`
owns the crash-recovery generations. Keeping this module pure is what lets the
whole format be tested without a window.

Design notes:

- Arrays serialize as lists of floats rounded to six decimals. A kept sweep is
  600 points, so the rounding keeps the JSON roughly a third smaller than full
  repr precision while staying far below the resolution of any measurement.
- ``schema_version`` follows the R&D convention: an older file is read with
  this version's defaults and upgraded in memory, a newer file raises
  :class:`UnsupportedMeasureSessionVersion` so the caller can say "update
  Fastgraph" instead of "this file is damaged".
- ``MeasurementDiagnostics`` and ``HarmonicAnalysis`` are reduced to plain
  JSON here rather than stored whole: diagnostics become the dataclass's own
  dictionary, and a harmonic analysis becomes the three-number distortion
  summary the Measure UI actually shows.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from dms.session import SessionData
from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
)
from dms.version import __version__ as APP_VERSION

MEASURE_SESSION_SCHEMA_VERSION = 1
MEASURE_SESSION_EXTENSION = ".fastgraph-measure.json"

#: Decimals kept when an array is serialized. Six is well below the noise
#: floor of any real measurement and keeps the JSON small.
ARRAY_DECIMALS = 6

#: Point count used when paired captures are collapsed into one curve, the
#: same grid the Measure plots use.
DEFAULT_COMBINE_POINTS = 1200

LEVEL_MODES = ("ref_1khz", "dbspl")
BOTTOM_MODES = ("combined", "separate")

Curve = tuple[np.ndarray, np.ndarray]
TimingQuality = tuple[float, float, float, float]


__all__ = [
    "APP_VERSION",
    "ARRAY_DECIMALS",
    "BOTTOM_MODES",
    "LEVEL_MODES",
    "MEASURE_SESSION_EXTENSION",
    "MEASURE_SESSION_SCHEMA_VERSION",
    "KeptPair",
    "KeptSweep",
    "MeasureSession",
    "UnsupportedMeasureSessionVersion",
    "diagnostics_to_dict",
    "distortion_summary",
    "session_data_from_dict",
    "session_data_to_dict",
]


class UnsupportedMeasureSessionVersion(ValueError):
    """A Measure session written by a newer Fastgraph than this one.

    A distinct type so callers can tell "this file is from the future" (fixed
    by updating Fastgraph) apart from "this file is damaged" (not fixable).
    """


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _array_to_list(values: Any) -> list[float]:
    array = np.asarray(values, dtype=float).ravel()
    if array.size == 0:
        return []
    return [round(float(item), ARRAY_DECIMALS) for item in array]


def _array_from_list(values: Any) -> np.ndarray:
    if values is None:
        return np.array([], dtype=float)
    return np.asarray(values, dtype=float).ravel()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _json_safe(value: Any) -> Any:
    """Reduce a diagnostics value to something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    return str(value)


def diagnostics_to_dict(value: Any) -> dict[str, Any] | None:
    """Return alignment diagnostics as a plain JSON-safe dictionary.

    Accepts a :class:`~dms.measurement_alignment.MeasurementDiagnostics` (or
    any dataclass), a dictionary that is passed through, or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {str(key): _json_safe(item) for key, item in dataclasses.asdict(value).items()}
    raise TypeError(
        f"Diagnostics must be a dataclass, a dictionary or None, not {type(value).__name__}."
    )


def distortion_summary(value: Any) -> dict[str, float] | None:
    """Reduce a harmonic analysis to the three numbers the Measure tab shows.

    Accepts a :class:`~dms.processing.HarmonicAnalysis` (anything carrying
    ``freqs`` and ``thd_percent``), an already-summarized dictionary, or
    ``None``. Frequencies whose THD is NaN are ignored; an analysis with no
    finite THD at all summarizes to ``None``.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        summary = {
            key: float(value[key])
            for key in ("thd_percent_median", "thd_percent_max", "thd_max_hz")
            if value.get(key) is not None
        }
        return summary or None

    freqs = np.asarray(getattr(value, "freqs", []), dtype=float).ravel()
    thd = np.asarray(getattr(value, "thd_percent", []), dtype=float).ravel()
    if thd.size == 0 or freqs.size != thd.size:
        return None
    finite = np.isfinite(thd)
    if not bool(finite.any()):
        return None
    valid_thd = thd[finite]
    valid_freqs = freqs[finite]
    peak = int(np.argmax(valid_thd))
    return {
        "thd_percent_median": round(float(np.median(valid_thd)), ARRAY_DECIMALS),
        "thd_percent_max": round(float(valid_thd[peak]), ARRAY_DECIMALS),
        "thd_max_hz": round(float(valid_freqs[peak]), ARRAY_DECIMALS),
    }


def session_data_to_dict(session: SessionData) -> dict[str, Any]:
    """Serialize :class:`~dms.session.SessionData`.

    ``SessionData`` already offers ``to_dict``; the matching reader lives here
    because ``dms/session.py`` has no ``from_dict``.
    """
    return session.to_dict()


def session_data_from_dict(data: Any) -> SessionData:
    """Rebuild :class:`~dms.session.SessionData` from a saved dictionary.

    Unknown keys are ignored and missing keys fall back to the dataclass's own
    defaults, so a metadata block written by an older Fastgraph still loads.
    """
    payload = dict(data or {})
    kwargs: dict[str, Any] = {}
    for spec in dataclasses.fields(SessionData):
        if spec.name not in payload:
            continue
        value = payload[spec.name]
        if isinstance(spec.default, bool):
            kwargs[spec.name] = bool(value)
        else:
            kwargs[spec.name] = "" if value is None else str(value)
    kwargs.setdefault("rig", "")
    kwargs.setdefault("brand", "")
    kwargs.setdefault("model", "")
    return SessionData(**kwargs)


def _timing_quality(value: Any) -> TimingQuality | None:
    if value is None:
        return None
    items = [float(item) for item in value]
    if len(items) != 4:
        raise ValueError("timing_quality must be (start_conf, end_conf, drift_ms, snr_db).")
    return (items[0], items[1], items[2], items[3])


@dataclass
class KeptSweep:
    """One kept Measure sweep and everything recorded about it."""

    freqs: np.ndarray
    mag_db: np.ndarray
    kept_at: str = field(default_factory=_now)
    diagnostics: dict[str, Any] | None = None
    timing_quality: TimingQuality | None = None
    distortion_summary: dict[str, float] | None = None
    note: str = ""

    def __post_init__(self) -> None:
        self.freqs = np.asarray(self.freqs, dtype=float).ravel()
        self.mag_db = np.asarray(self.mag_db, dtype=float).ravel()
        self.diagnostics = diagnostics_to_dict(self.diagnostics)
        self.timing_quality = _timing_quality(self.timing_quality)
        self.distortion_summary = distortion_summary(self.distortion_summary)
        self.note = str(self.note or "")

    @property
    def curve(self) -> Curve:
        return (self.freqs, self.mag_db)

    def to_dict(self) -> dict[str, Any]:
        return {
            "freqs": _array_to_list(self.freqs),
            "mag_db": _array_to_list(self.mag_db),
            "kept_at": self.kept_at,
            "diagnostics": self.diagnostics,
            "timing_quality": (
                None
                if self.timing_quality is None
                else [round(float(item), ARRAY_DECIMALS) for item in self.timing_quality]
            ),
            "distortion_summary": self.distortion_summary,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Any) -> KeptSweep:
        payload = dict(data or {})
        return cls(
            freqs=_array_from_list(payload.get("freqs")),
            mag_db=_array_from_list(payload.get("mag_db")),
            kept_at=str(payload.get("kept_at") or _now()),
            diagnostics=payload.get("diagnostics") or None,
            timing_quality=payload.get("timing_quality"),
            distortion_summary=payload.get("distortion_summary") or None,
            note=str(payload.get("note") or ""),
        )


@dataclass
class KeptPair:
    """One kept two-channel capture: the L and R sweeps kept together."""

    channel_1: KeptSweep
    channel_2: KeptSweep
    kept_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_1": self.channel_1.to_dict(),
            "channel_2": self.channel_2.to_dict(),
            "kept_at": self.kept_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> KeptPair:
        payload = dict(data or {})
        return cls(
            channel_1=KeptSweep.from_dict(payload.get("channel_1")),
            channel_2=KeptSweep.from_dict(payload.get("channel_2")),
            kept_at=str(payload.get("kept_at") or _now()),
        )

    def to_curve_pair(self) -> TwoChannelCurvePair:
        """Rebuild the runtime pair the Measure plots consume."""
        return TwoChannelCurvePair(
            channel_1=self.channel_1.curve,
            channel_2=self.channel_2.curve,
            channel_1_diagnostics=self.channel_1.diagnostics,
            channel_2_diagnostics=self.channel_2.diagnostics,
        )


def _as_kept_sweep(
    value: Any,
    *,
    diagnostics: Any = None,
    timing_quality: Any = None,
    distortion: Any = None,
    kept_at: str | None = None,
) -> KeptSweep:
    """Coerce a curve tuple or an existing sweep into a :class:`KeptSweep`."""
    if isinstance(value, KeptSweep):
        return value
    freqs, mag_db = value
    return KeptSweep(
        freqs=freqs,
        mag_db=mag_db,
        kept_at=kept_at or _now(),
        diagnostics=diagnostics,
        timing_quality=timing_quality,
        distortion_summary=distortion,
    )


@dataclass
class MeasureSession:
    """A whole Measure workspace: the device, the settings and the captures."""

    metadata: SessionData
    two_channel: bool = False
    bottom_mode: str = "combined"
    level_mode: str = "ref_1khz"
    hrtf_path: str | None = None
    hrtf_name: str | None = None
    hrtf_enabled: bool = False
    sweeps: list[KeptSweep] = field(default_factory=list)
    pairs: list[KeptPair] = field(default_factory=list)
    notes: str = ""
    schema_version: int = MEASURE_SESSION_SCHEMA_VERSION
    saved_app_version: str = APP_VERSION
    created_at: str = field(default_factory=_now)
    #: Runtime-only: the file this session was loaded from or last saved to.
    #: Deliberately not serialized so a session file stays portable.
    source_path: str | None = None

    def __post_init__(self) -> None:
        self.two_channel = bool(self.two_channel)
        self.bottom_mode = "separate" if str(self.bottom_mode) == "separate" else "combined"
        self.level_mode = "dbspl" if str(self.level_mode) == "dbspl" else "ref_1khz"
        self.hrtf_path = _optional_str(self.hrtf_path)
        self.hrtf_name = _optional_str(self.hrtf_name)
        self.hrtf_enabled = bool(self.hrtf_enabled)
        self.notes = str(self.notes or "")

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "saved_app_version": self.saved_app_version,
            "created_at": self.created_at,
            "metadata": session_data_to_dict(self.metadata),
            "two_channel": self.two_channel,
            "bottom_mode": self.bottom_mode,
            "level_mode": self.level_mode,
            "hrtf_path": self.hrtf_path,
            "hrtf_name": self.hrtf_name,
            "hrtf_enabled": self.hrtf_enabled,
            "sweeps": [item.to_dict() for item in self.sweeps],
            "pairs": [item.to_dict() for item in self.pairs],
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MeasureSession:
        payload = dict(data or {})
        version = int(payload.get("schema_version") or 0)
        if version > MEASURE_SESSION_SCHEMA_VERSION:
            raise UnsupportedMeasureSessionVersion(
                f"Unsupported Measure session schema version: {version}. "
                "This session was saved by a newer Fastgraph."
            )
        # Anything an older file does not carry is filled in with this
        # version's defaults, so the session is upgraded in memory and a
        # re-save writes the current schema.
        return cls(
            metadata=session_data_from_dict(payload.get("metadata")),
            two_channel=bool(payload.get("two_channel", False)),
            bottom_mode=str(payload.get("bottom_mode") or "combined"),
            level_mode=str(payload.get("level_mode") or "ref_1khz"),
            hrtf_path=payload.get("hrtf_path"),
            hrtf_name=payload.get("hrtf_name"),
            hrtf_enabled=bool(payload.get("hrtf_enabled", False)),
            sweeps=[KeptSweep.from_dict(item) for item in payload.get("sweeps") or []],
            pairs=[KeptPair.from_dict(item) for item in payload.get("pairs") or []],
            notes=str(payload.get("notes") or ""),
            schema_version=MEASURE_SESSION_SCHEMA_VERSION,
            saved_app_version=str(payload.get("saved_app_version") or ""),
            created_at=str(payload.get("created_at") or _now()),
        )

    # -- queries ---------------------------------------------------------

    def is_empty(self) -> bool:
        return not self.sweeps and not self.pairs

    def kept_count(self) -> int:
        """How many captures the active workspace holds."""
        return len(self.pairs) if self.two_channel else len(self.sweeps)

    def pair_objects(self) -> list[TwoChannelCurvePair]:
        """The kept pairs as the runtime type :mod:`dms.two_channel` uses."""
        return [item.to_curve_pair() for item in self.pairs]

    def sweep_curves(self) -> list[Curve]:
        return [item.curve for item in self.sweeps]

    def curves(
        self,
        *,
        selection: str = "combined",
        n_points: int = DEFAULT_COMBINE_POINTS,
    ) -> list[Curve]:
        """The curves of the active workspace, mirroring the Measure plots.

        In single-channel mode this is simply the kept sweeps. In two-channel
        mode it is one curve per kept pair: the selected channel for
        ``selection`` of ``"channel_1"``/``"channel_2"``, otherwise the power
        mean of both channels on an ``n_points`` grid.
        """
        if not self.two_channel:
            return self.sweep_curves()
        pairs = self.pair_objects()
        if selection == "channel_1":
            return channel_curves(pairs, 1)
        if selection == "channel_2":
            return channel_curves(pairs, 2)
        return combined_pair_curves(pairs, n_points=n_points)

    # -- mutation --------------------------------------------------------

    def add_sweep(
        self,
        freqs: Any,
        mag_db: Any,
        *,
        diagnostics: Any = None,
        timing_quality: Any = None,
        distortion: Any = None,
        note: str = "",
        kept_at: str | None = None,
    ) -> KeptSweep:
        """Append one kept sweep, stamping ``kept_at`` unless one is supplied."""
        sweep = KeptSweep(
            freqs=freqs,
            mag_db=mag_db,
            kept_at=kept_at or _now(),
            diagnostics=diagnostics,
            timing_quality=timing_quality,
            distortion_summary=distortion,
            note=note,
        )
        self.sweeps.append(sweep)
        return sweep

    def add_pair(
        self,
        channel_1: Any,
        channel_2: Any = None,
        *,
        channel_1_diagnostics: Any = None,
        channel_2_diagnostics: Any = None,
        channel_1_timing_quality: Any = None,
        channel_2_timing_quality: Any = None,
        channel_1_distortion: Any = None,
        channel_2_distortion: Any = None,
        kept_at: str | None = None,
    ) -> KeptPair:
        """Append one kept pair.

        ``channel_1`` may be a :class:`~dms.two_channel.TwoChannelCurvePair`
        on its own, or the two channels may be passed as ``(freqs, mag_db)``
        tuples or :class:`KeptSweep` objects.
        """
        stamp = kept_at or _now()
        if isinstance(channel_1, TwoChannelCurvePair) and channel_2 is None:
            source = channel_1
            channel_1 = source.channel_1
            channel_2 = source.channel_2
            if channel_1_diagnostics is None:
                channel_1_diagnostics = source.channel_1_diagnostics
            if channel_2_diagnostics is None:
                channel_2_diagnostics = source.channel_2_diagnostics
        if channel_2 is None:
            raise ValueError("A kept pair needs both channels.")
        pair = KeptPair(
            channel_1=_as_kept_sweep(
                channel_1,
                diagnostics=channel_1_diagnostics,
                timing_quality=channel_1_timing_quality,
                distortion=channel_1_distortion,
                kept_at=stamp,
            ),
            channel_2=_as_kept_sweep(
                channel_2,
                diagnostics=channel_2_diagnostics,
                timing_quality=channel_2_timing_quality,
                distortion=channel_2_distortion,
                kept_at=stamp,
            ),
            kept_at=stamp,
        )
        self.pairs.append(pair)
        return pair

    # -- construction ----------------------------------------------------

    @classmethod
    def from_window_state(
        cls,
        *,
        session_data: SessionData,
        kept_curves: Sequence[Curve] | None = None,
        pairs: Iterable[Any] | None = None,
        two_channel: bool = False,
        bottom_mode: str = "combined",
        level_mode: str = "ref_1khz",
        hrtf_path: str | None = None,
        hrtf_name: str | None = None,
        hrtf_enabled: bool = False,
        sweep_diagnostics: Sequence[Any] | None = None,
        sweep_timing_quality: Sequence[Any] | None = None,
        sweep_distortion: Sequence[Any] | None = None,
        notes: str = "",
        created_at: str | None = None,
        source_path: str | None = None,
    ) -> MeasureSession:
        """Build a session from the Measure tab's plain runtime state.

        Everything is a plain argument so the UI wiring stays a one-liner and
        this module never has to import the window. The optional per-sweep
        sequences are positional companions to ``kept_curves``; a shorter
        sequence simply leaves the remaining sweeps without that field.
        """
        session = cls(
            metadata=session_data,
            two_channel=two_channel,
            bottom_mode=bottom_mode,
            level_mode=level_mode,
            hrtf_path=hrtf_path,
            hrtf_name=hrtf_name,
            hrtf_enabled=hrtf_enabled,
            notes=notes,
            created_at=created_at or _now(),
            source_path=source_path,
        )

        def _at(sequence: Sequence[Any] | None, index: int) -> Any:
            if not sequence or index >= len(sequence):
                return None
            return sequence[index]

        for index, curve in enumerate(kept_curves or []):
            freqs, mag_db = curve
            session.add_sweep(
                freqs,
                mag_db,
                diagnostics=_at(sweep_diagnostics, index),
                timing_quality=_at(sweep_timing_quality, index),
                distortion=_at(sweep_distortion, index),
            )
        for pair in pairs or []:
            session.add_pair(pair)
        return session
