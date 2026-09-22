import numpy as np

from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
    shared_normalize_pair_at_1khz,
)


def _curve(level: float):
    return np.array([100.0, 1000.0, 10000.0]), np.array([level, level, level])


def test_shared_normalization_preserves_delta_and_sets_power_mean_reference() -> None:
    freqs = np.array([100.0, 1000.0, 10000.0])
    first = np.array([2.0, 4.0, 6.0])
    second = np.array([-2.0, 0.0, 2.0])

    normalized_first, normalized_second = shared_normalize_pair_at_1khz(freqs, first, freqs, second)

    np.testing.assert_allclose(normalized_first - normalized_second, first - second)
    ref_power = (10.0 ** (normalized_first[1] / 10.0) + 10.0 ** (normalized_second[1] / 10.0)) / 2.0
    assert abs(10.0 * np.log10(ref_power)) < 1e-10


def test_combined_curves_use_power_mean_without_renormalizing() -> None:
    [combined] = combined_pair_curves([TwoChannelCurvePair(_curve(0.0), _curve(6.0))], n_points=3)
    expected = 10.0 * np.log10((1.0 + 10.0**0.6) / 2.0)
    np.testing.assert_allclose(combined[1], expected)


def test_pair_helpers_keep_channel_order() -> None:
    pairs = [TwoChannelCurvePair(_curve(1.0), _curve(2.0))]
    assert channel_curves(pairs, 1)[0][1][0] == 1.0
    assert channel_curves(pairs, 2)[0][1][0] == 2.0
    assert combined_pair_curves(pairs)
