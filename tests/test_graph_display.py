import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen

from dms.graph_display import (
    EXPORT_FREQUENCY_MARKERS,
    FREQUENCY_MARKERS,
    FREQUENCY_TICKS,
    RETRO_GRAPH_MAX_BINS,
    STIPPLE_DASH_PATTERNS,
    retro_step_group,
    retro_step_series,
    stipple_trace_pen,
    uses_retro_steps,
)
from dms.style_tokens import DARK_TOKENS, DITHER_TOKENS


def test_retro_steps_reduce_display_points_and_preserve_local_extrema() -> None:
    freqs = np.geomspace(20.0, 20000.0, 1200)
    values = np.sin(np.log(freqs) * 4.0)
    values[517] = 12.0
    values[833] = -11.0
    source_freqs = np.array(freqs, copy=True)
    source_values = np.array(values, copy=True)

    stepped_freqs, stepped_values = retro_step_series(freqs, values)

    assert len(stepped_freqs) < len(freqs)
    assert len(stepped_freqs) <= RETRO_GRAPH_MAX_BINS * 4 + 1
    assert np.max(stepped_values) == 12.0
    assert np.min(stepped_values) == -11.0
    np.testing.assert_array_equal(freqs, source_freqs)
    np.testing.assert_array_equal(values, source_values)

    np.testing.assert_array_equal(stepped_freqs[1::2], stepped_freqs[2::2])
    np.testing.assert_array_equal(stepped_values[0:-1:2], stepped_values[1::2])


def test_retro_step_group_keeps_curves_on_shared_frequency_transitions() -> None:
    freqs = np.geomspace(20.0, 20000.0, 1200)
    lower = -2.0 + np.sin(np.log(freqs))
    upper = lower + 4.0

    stepped_freqs, stepped_lower, stepped_upper = retro_step_group(freqs, (lower, upper))

    assert len(stepped_freqs) == len(stepped_lower) == len(stepped_upper)
    assert np.all(stepped_lower <= stepped_upper)
    np.testing.assert_array_equal(stepped_freqs[1::2], stepped_freqs[2::2])


def test_registered_classic_graph_themes_select_the_step_renderer() -> None:
    assert uses_retro_steps("fastgraph95")
    assert uses_retro_steps("fastgraph95_dark")
    assert uses_retro_steps("hackerman95")
    assert not uses_retro_steps("dark")
    assert not uses_retro_steps("hackerman95", brand_mode=True)


def test_stipple_trace_pen_preserves_non_stipple_pen() -> None:
    source = QPen(QColor("#123456"), 2.75, Qt.PenStyle.DashDotLine)

    result = stipple_trace_pen(source, 3, DARK_TOKENS)

    assert result.color() == source.color()
    assert result.widthF() == source.widthF()
    assert result.style() == source.style()
    assert result.dashPattern() == source.dashPattern()


def test_stipple_trace_pen_uses_each_pattern_and_cycles() -> None:
    source = QPen(QColor("#E9E2D4"), 3.25)

    for index, expected in enumerate(STIPPLE_DASH_PATTERNS):
        result = stipple_trace_pen(source, index, DITHER_TOKENS)
        assert result.color() == source.color()
        assert result.widthF() == source.widthF()
        if expected is None:
            assert result.style() == Qt.PenStyle.SolidLine
        else:
            assert result.style() == Qt.PenStyle.CustomDashLine
            assert result.dashPattern() == list(expected)

    cycled = stipple_trace_pen(source, 9, DITHER_TOKENS)
    assert cycled.style() == Qt.PenStyle.CustomDashLine
    assert cycled.dashPattern() == [6.0, 3.0]


def test_curator_image_export_does_not_import_ui_modules() -> None:
    code = (
        "import sys, dms.curator.export_image;"
        "print([m for m in sys.modules if m == 'dms.ui' or m.startswith('dms.ui.')])"
    )
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )
    assert result.stdout.strip() == "[]"


def test_frequency_markers_include_1k_3k_8k_and_10k_weights() -> None:
    ticks = dict(FREQUENCY_TICKS)

    assert ticks[1000] == "1k"
    assert ticks[3000] == "3k"
    assert ticks[8000] == "8k"
    assert ticks[10000] == "10k"
    for markers in (FREQUENCY_MARKERS, EXPORT_FREQUENCY_MARKERS):
        assert 8000 not in markers
        assert markers[1000][4] > markers[3000][4]
        assert markers[10000][4] > markers[3000][4]
