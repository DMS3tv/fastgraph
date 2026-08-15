import numpy as np

from dms.graph_display import (
    RETRO_GRAPH_MAX_BINS,
    retro_step_group,
    retro_step_series,
    uses_retro_steps,
)


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

    stepped_freqs, stepped_lower, stepped_upper = retro_step_group(
        freqs, (lower, upper)
    )

    assert len(stepped_freqs) == len(stepped_lower) == len(stepped_upper)
    assert np.all(stepped_lower <= stepped_upper)
    np.testing.assert_array_equal(stepped_freqs[1::2], stepped_freqs[2::2])


def test_registered_classic_graph_themes_select_the_step_renderer() -> None:
    assert uses_retro_steps("fastgraph95")
    assert uses_retro_steps("fastgraph95_dark")
    assert uses_retro_steps("hackerman95")
    assert not uses_retro_steps("dark")
    assert not uses_retro_steps("hackerman95", brand_mode=True)
