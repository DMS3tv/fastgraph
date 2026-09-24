from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import numpy as np

from dms.processing import DEFAULT_SMOOTHING, VariationBand, percentile_band
from dms.session import SessionData
from dms.style_tokens import DEFAULT_TRACE_COLORS as DEFAULT_COLORS

SCHEMA_VERSION = 1


class UnsupportedSessionVersion(ValueError):
    """A session file written by a newer Fastgraph than this one.

    It is a distinct type so callers can tell "this file is from the future"
    apart from "this file is damaged": the first is recoverable by updating
    Fastgraph, the second is not.
    """


@dataclass
class RnDMeasurement:
    name: str
    freqs: np.ndarray
    mag_db: np.ndarray
    metadata: dict[str, Any]
    rig: str
    input_device_label: str
    input_channel_index: int
    input_channel_label: str
    output_device_label: str
    id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    notes: str = ""
    change_status: str = "no_change"
    milestone: bool = False
    top_visible: bool = True
    pinned: bool = False
    color: str = DEFAULT_COLORS[0]
    hrtf_path: str = ""
    hrtf_name: str = ""
    vertical_offset_db: float = 0.0
    photos: list[RnDPhoto] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "freqs": self.freqs.astype(float).tolist(),
            "mag_db": self.mag_db.astype(float).tolist(),
            "metadata": dict(self.metadata),
            "rig": self.rig,
            "input_device_label": self.input_device_label,
            "input_channel_index": int(self.input_channel_index),
            "input_channel_label": self.input_channel_label,
            "output_device_label": self.output_device_label,
            "timestamp": self.timestamp,
            "notes": self.notes,
            "change_status": self.change_status,
            "milestone": self.milestone,
            "top_visible": self.top_visible,
            "pinned": self.pinned,
            "color": self.color,
            "hrtf_path": self.hrtf_path,
            "hrtf_name": self.hrtf_name,
            "vertical_offset_db": float(self.vertical_offset_db),
            "photos": [photo.to_dict() for photo in self.photos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RnDMeasurement:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name") or "R&D Measurement"),
            freqs=np.array(data.get("freqs") or [], dtype=float),
            mag_db=np.array(data.get("mag_db") or [], dtype=float),
            metadata=dict(data.get("metadata") or {}),
            rig=str(data.get("rig") or ""),
            input_device_label=str(data.get("input_device_label") or ""),
            input_channel_index=int(data.get("input_channel_index") or 0),
            input_channel_label=str(data.get("input_channel_label") or ""),
            output_device_label=str(data.get("output_device_label") or ""),
            timestamp=str(data.get("timestamp") or datetime.now(UTC).isoformat()),
            notes=str(data.get("notes") or ""),
            change_status=str(data.get("change_status") or "no_change"),
            milestone=bool(data.get("milestone")),
            top_visible=bool(data.get("top_visible", True)),
            pinned=bool(data.get("pinned", False)),
            color=str(data.get("color") or DEFAULT_COLORS[0]),
            hrtf_path=str(data.get("hrtf_path") or ""),
            hrtf_name=str(data.get("hrtf_name") or ""),
            vertical_offset_db=float(data.get("vertical_offset_db") or 0.0),
            photos=[RnDPhoto.from_dict(item) for item in data.get("photos") or []],
        )


@dataclass
class RnDPhoto:
    """A compact, session-serializable reference to a managed JPEG attachment."""

    id: str = field(default_factory=lambda: uuid4().hex)
    display_name: str = "Photo"
    caption: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    file_name: str = ""
    # Runtime-only location in Fastgraph's staging directory. It is intentionally
    # not written to session JSON so sessions stay portable.
    runtime_path: str = ""

    def __post_init__(self) -> None:
        if not self.file_name:
            self.file_name = f"{self.id}.jpg"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "caption": self.caption,
            "timestamp": self.timestamp,
            "file_name": self.file_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RnDPhoto:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            display_name=str(data.get("display_name") or "Photo"),
            caption=str(data.get("caption") or ""),
            timestamp=str(data.get("timestamp") or datetime.now(UTC).isoformat()),
            file_name=str(data.get("file_name") or ""),
        )


@dataclass
class RnDGroup:
    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    notes: str = ""
    expanded: bool = True
    visible: bool = True
    pinned: bool = False
    milestone: bool = False
    variation_enabled: bool = False
    vertical_offset_db: float = 0.0
    color: str = DEFAULT_COLORS[0]
    measurement_ids: list[str] = field(default_factory=list)
    photos: list[RnDPhoto] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "notes": self.notes,
            "expanded": self.expanded,
            "visible": self.visible,
            "pinned": self.pinned,
            "milestone": self.milestone,
            "variation_enabled": self.variation_enabled,
            "vertical_offset_db": float(self.vertical_offset_db),
            "color": self.color,
            "measurement_ids": list(self.measurement_ids),
            "photos": [photo.to_dict() for photo in self.photos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RnDGroup:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name") or "Group"),
            notes=str(data.get("notes") or ""),
            expanded=bool(data.get("expanded", True)),
            visible=bool(data.get("visible", True)),
            pinned=bool(data.get("pinned", False)),
            milestone=bool(data.get("milestone")),
            variation_enabled=bool(data.get("variation_enabled")),
            vertical_offset_db=float(data.get("vertical_offset_db") or 0.0),
            color=str(data.get("color") or DEFAULT_COLORS[0]),
            measurement_ids=[str(item) for item in data.get("measurement_ids") or []],
            photos=[RnDPhoto.from_dict(item) for item in data.get("photos") or []],
        )


@dataclass
class RnDSession:
    measurements: list[RnDMeasurement] = field(default_factory=list)
    groups: list[RnDGroup] = field(default_factory=list)
    ungrouped_order: list[str] = field(default_factory=list)
    selected_id: str | None = None
    hrtf_path: str | None = None
    hrtf_name: str = ""
    hrtf_enabled: bool = False
    preference_bounds_enabled: bool = False
    target_visible: bool = False
    target_name: str = ""
    target_path: str = ""
    target_freqs: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    target_mag_db: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    target_offset_db: float = 0.0
    smoothing_fraction: int = DEFAULT_SMOOTHING
    delta_mode_enabled: bool = False
    schema_version: int = SCHEMA_VERSION
    saved_app_version: str = ""
    # Runtime-only: the file this session was loaded from or last saved to. It
    # is deliberately not serialized so a session file stays portable, and it
    # is what lets a Save As avoid deleting the destination's attachments.
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "saved_app_version": self.saved_app_version,
            "measurements": [item.to_dict() for item in self.measurements],
            "groups": [item.to_dict() for item in self.groups],
            "ungrouped_order": list(self.ungrouped_order),
            "selected_id": self.selected_id,
            "hrtf_path": self.hrtf_path,
            "hrtf_name": self.hrtf_name,
            "hrtf_enabled": self.hrtf_enabled,
            "preference_bounds_enabled": self.preference_bounds_enabled,
            "target_visible": self.target_visible,
            "target_name": self.target_name,
            "target_path": self.target_path,
            "target_freqs": self.target_freqs.astype(float).tolist(),
            "target_mag_db": self.target_mag_db.astype(float).tolist(),
            "target_offset_db": float(self.target_offset_db),
            "smoothing_fraction": int(self.smoothing_fraction),
            "delta_mode_enabled": bool(self.delta_mode_enabled),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RnDSession:
        version = int(data.get("schema_version") or 0)
        if version > SCHEMA_VERSION:
            raise UnsupportedSessionVersion(
                f"Unsupported R&D session schema version: {version}. "
                "This session was saved by a newer Fastgraph."
            )
        # Older files are read with this version's defaults for anything they
        # do not carry, and are upgraded in memory so a re-save is current.
        session = cls(
            schema_version=SCHEMA_VERSION,
            saved_app_version=str(data.get("saved_app_version") or ""),
            measurements=[
                RnDMeasurement.from_dict(item) for item in data.get("measurements") or []
            ],
            groups=[RnDGroup.from_dict(item) for item in data.get("groups") or []],
            ungrouped_order=[str(item) for item in data.get("ungrouped_order") or []],
            selected_id=data.get("selected_id"),
            hrtf_path=data.get("hrtf_path"),
            hrtf_name=str(data.get("hrtf_name") or ""),
            hrtf_enabled=bool(data.get("hrtf_enabled")),
            preference_bounds_enabled=bool(data.get("preference_bounds_enabled", False)),
            target_visible=bool(data.get("target_visible", False)),
            target_name=str(data.get("target_name") or ""),
            target_path=str(data.get("target_path") or ""),
            target_freqs=np.array(data.get("target_freqs") or [], dtype=float),
            target_mag_db=np.array(data.get("target_mag_db") or [], dtype=float),
            target_offset_db=float(data.get("target_offset_db") or 0.0),
            smoothing_fraction=int(data.get("smoothing_fraction") or DEFAULT_SMOOTHING),
            delta_mode_enabled=bool(data.get("delta_mode_enabled", False)),
        )
        session.repair_ordering()
        return session

    def measurement_by_id(self, measurement_id: str) -> RnDMeasurement | None:
        return next((item for item in self.measurements if item.id == measurement_id), None)

    def group_by_id(self, group_id: str) -> RnDGroup | None:
        return next((item for item in self.groups if item.id == group_id), None)

    def parent_group_id(self, measurement_id: str) -> str | None:
        for group in self.groups:
            if measurement_id in group.measurement_ids:
                return group.id
        return None

    def repair_ordering(self) -> None:
        valid_ids = {item.id for item in self.measurements}
        seen: set[str] = set()
        self.ungrouped_order = [
            item
            for item in self.ungrouped_order
            if item in valid_ids and not (item in seen or seen.add(item))
        ]
        for group in self.groups:
            group.measurement_ids = [
                item
                for item in group.measurement_ids
                if item in valid_ids and not (item in seen or seen.add(item))
            ]
        for measurement in self.measurements:
            if measurement.id not in seen:
                self.ungrouped_order.append(measurement.id)
                seen.add(measurement.id)

    def is_empty(self) -> bool:
        return not self.measurements and not self.groups


def generate_measurement_name(
    session: SessionData,
    input_label: str,
    input_channel_label: str,
    existing_names: set[str],
) -> str:
    identity = session.asset_tag.strip() or " ".join(
        part for part in (session.brand.strip(), session.model.strip()) if part
    )
    if not identity:
        identity = "Unknown"
    base = " - ".join(
        part
        for part in (
            identity,
            session.rig.strip(),
            input_label.strip(),
            input_channel_label.strip(),
        )
        if part
    )
    if not base:
        base = "R&D Measurement"
    if base not in existing_names:
        return base
    suffix = 2
    while f"{base} ({suffix})" in existing_names:
        suffix += 1
    return f"{base} ({suffix})"


def group_variation(
    measurements: list[RnDMeasurement],
    *,
    smoothing_fraction: int = DEFAULT_SMOOTHING,
) -> VariationBand | None:
    if len(measurements) < 2:
        return None
    return percentile_band(
        [(item.freqs, item.mag_db) for item in measurements], smoothing=smoothing_fraction
    )
