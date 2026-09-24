"""Optional brand plugin: a private package that adds one branded mode.

The public app ships no brand. A brand package is found by the module name
in ``FASTGRAPH_BRAND_PLUGIN`` (default ``fastgraph_brand``) and must expose a
``BRAND`` built from :class:`Brand`. Without one, the brand mode is absent.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dms.style_tokens import ThemeTokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Brand:
    label: str  # settings group title
    short_label: str  # prefix for poster wording in the Curator
    tokens: ThemeTokens
    theme_colors: dict[str, str]
    stylesheet_extra: str  # appended to the standard stylesheet built from the tokens
    trace_palette: list[str]
    menu_colors: Callable[[str], list[str]]  # layer colour menu, current colour first
    poster_size: tuple[int, int]
    poster_defaults: dict[str, str]  # keys: footer1, footer2, legend_bounds, legend_variation
    draw_poster: Callable[..., list[str]]  # (painter, state, size) -> text warnings
    export_warnings: Callable[..., list[str]]  # (state, size) -> text warnings
    font_status: Callable[[], Any]  # .heading_family, .mono_family, .missing_families
    export_all_replaces_upload: bool = False
    on_settings_load: Callable[[dict[str, Any]], None] | None = None


_UNSET: Any = object()
_brand: Any = _UNSET


def _load() -> Brand | None:
    name = os.environ.get("FASTGRAPH_BRAND_PLUGIN", "fastgraph_brand").strip()
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            logger.exception("Brand plugin %s failed to import", name)
        return None
    return getattr(module, "BRAND", None)


def active() -> Brand | None:
    """The installed brand, or None. Loaded once per process."""
    global _brand
    if _brand is _UNSET:
        _brand = _load()
    return _brand


def reset() -> None:
    """Forget the cached brand so the next ``active()`` looks again (tests)."""
    global _brand
    _brand = _UNSET
