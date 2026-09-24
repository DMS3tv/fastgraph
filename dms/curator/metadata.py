"""Metadata normalization and poster text defaults."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from dms.curator.models import GraphState, LayerState, poster_default
from dms.curator.transforms import visible_display_layers

FIELD_ALIASES = {
    "rig": "rig",
    "brand": "brand",
    "model": "model",
    "modelnumber": "model_number",
    "assettag": "asset_tag",
    "firmware": "firmware",
    "eqapplied": "eq_applied",
    "anctransparency": "anc_transparency",
    "ancmode": "anc_mode",
    "transparencymode": "transparency_mode",
    "formfactor": "form_factor",
    "inearfitment": "in_ear_fitment",
    "inearfitting": "in_ear_fitment",
    "acoustictype": "acoustic_type",
    "padstipsnotes": "pads_notes",
    "connection": "connection",
    "channelside": "channel_side",
    "hrtf": "hrtf_name",
    "hrtfname": "hrtf_name",
    "compensated": "compensated",
    "curvetype": "curve_type",
    "variationsweeps": "variation_sweeps",
    "sweeps": "variation_sweeps",
    "source": "source",
}


def canonicalize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return original metadata plus stable, lower-case field names."""

    source = dict(metadata or {})
    result = dict(source)
    for key, value in source.items():
        canonical = FIELD_ALIASES.get(_key_token(key))
        if canonical and canonical not in result:
            result[canonical] = value

    anc_value = str(result.get("anc_transparency") or "").strip()
    if anc_value:
        normalized = anc_value.casefold()
        result.setdefault("anc_mode", normalized == "anc")
        result.setdefault("transparency_mode", normalized == "transparency")

    for key in ("eq_applied", "anc_mode", "transparency_mode", "compensated"):
        if key in result:
            result[key] = _as_bool(result[key])
    return result


def shared_metadata(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return canonical values that are equal in all supplied metadata maps."""

    normalized = [canonicalize_metadata(item) for item in items]
    if not normalized:
        return {}
    shared: dict[str, Any] = {}
    keys = set.intersection(*(set(item) for item in normalized))
    for key in keys:
        values = [item[key] for item in normalized]
        if all(value == values[0] for value in values[1:]):
            shared[key] = values[0]
    return shared


def automatic_export_values(
    state: GraphState,
    primary_layer: LayerState | None,
) -> dict[str, str]:
    """Build editable poster values from graph and headphone metadata."""

    visible = visible_display_layers(state.layers, state.smoothing_fraction)
    kinds = {curve.kind for _layer, curve in visible}
    has_variation = "variation" in kinds
    title = "FREQUENCY RESPONSE & VARIATION" if has_variation else "FREQUENCY RESPONSE"

    metadata = canonicalize_metadata(primary_layer.curve.metadata if primary_layer else {})
    product = " ".join(
        part.strip()
        for part in (str(metadata.get("brand") or ""), str(metadata.get("model") or ""))
        if part.strip()
    )

    detail_parts: list[str] = []
    if product:
        detail_parts.append(product.upper())
    anc = _anc_label(metadata)
    if anc:
        detail_parts.append(anc)
    if "eq_applied" in metadata:
        detail_parts.append("EQ ON" if bool(metadata["eq_applied"]) else "STANDARD")
    connection = str(metadata.get("connection") or "").strip()
    if connection:
        detail_parts.append(connection.upper())

    rig = str(metadata.get("rig") or "").strip()
    hrtf_name = str(metadata.get("hrtf_name") or "").strip()
    if primary_layer is not None and primary_layer.hrtf is not None:
        hrtf_name = str(getattr(primary_layer.hrtf, "name", "") or hrtf_name).strip()
    footer_center = ", ".join(part for part in (rig, hrtf_name) if part)

    asset_tag = str(metadata.get("asset_tag") or "").strip()
    footer_left_1 = asset_tag or product or poster_default("footer1")

    if has_variation and bool(metadata.get("compensated")):
        variation_label = "Frequency Response + HpTF Variation"
    elif has_variation:
        variation_label = "Frequency Response Variation"
    elif bool(metadata.get("compensated")):
        variation_label = "Compensated Frequency Response"
    else:
        variation_label = "Frequency Response"

    return {
        "title": title,
        "fixture": " | ".join(detail_parts),
        "footer": footer_center,
        "footer1": footer_left_1,
        "footer2": poster_default("footer2"),
        "legend_bounds": poster_default("legend_bounds"),
        "legend_variation": variation_label,
    }


def metadata_has_identity(metadata: Mapping[str, Any] | None) -> bool:
    normalized = canonicalize_metadata(metadata)
    return any(
        str(normalized.get(key) or "").strip() for key in ("brand", "model", "asset_tag", "rig")
    )


def _key_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    return normalized in {"1", "true", "yes", "on", "enabled", "anc", "transparency"}


def _anc_label(metadata: Mapping[str, Any]) -> str:
    if bool(metadata.get("anc_mode")):
        return "ANC ON"
    if bool(metadata.get("transparency_mode")):
        return "TRANSPARENCY"
    if "anc_mode" in metadata or "transparency_mode" in metadata or "anc_transparency" in metadata:
        return "ANC OFF"
    return ""
