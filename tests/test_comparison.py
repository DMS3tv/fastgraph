"""Target comparison: loading, deltas, deviation scoring, EQ and A/B layers.

Pure NumPy; nothing here builds a widget.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import dms.comparison as comparison
from dms.comparison import (
    COMMON_GRID,
    DEFAULT_BANDS,
    DeltaResult,
    EqSuggestion,
    PeakingFilter,
    compute_preamp_db,
    delta_curve,
    deviation_score,
    eq_response_db,
    format_deviation_summary,
    format_eq_apo,
    format_eq_table,
    load_reference_from_measure_session,
    load_reference_from_txt,
    load_target_curve,
    resample_to_common,
    suggest_eq,
)
from dms.processing import compute_rms_average

GRID = np.asarray(COMMON_GRID, dtype=float)


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_text(text, encoding=encoding)
    return path


def _flat_delta(values: np.ndarray) -> DeltaResult:
    return DeltaResult(freqs=GRID, delta_db=np.asarray(values, dtype=float), offset_db=0.0)


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------


def test_load_target_two_column_normalizes_at_1khz(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "target.txt",
        "* Target: Example\n20\t9.0\n1000\t4.0\n20000\t1.0\n",
    )

    freqs, mag_db, warnings = load_target_curve(path)

    assert np.allclose(freqs, [20.0, 1000.0, 20000.0])
    # Every level moves down by the 4 dB that sat at 1 kHz.
    assert np.allclose(mag_db, [5.0, 0.0, -3.0])
    assert warnings == []


def test_load_target_accepts_bom_and_decimal_commas(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "euro.txt",
        "20;9,0\n1000;4,0\n20000;1,0\n",
        encoding="utf-8-sig",
    )

    freqs, mag_db, warnings = load_target_curve(path)

    assert np.allclose(freqs, [20.0, 1000.0, 20000.0])
    assert np.allclose(mag_db, [5.0, 0.0, -3.0])
    assert warnings == []


def test_load_target_six_column_uses_median(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "variation.txt",
        "* Export Type: Variation Band\n"
        "20\t-6\t-3\t9\t3\t6\n"
        "1000\t-6\t-3\t4\t3\t6\n"
        "20000\t-6\t-3\t1\t3\t6\n",
    )

    freqs, mag_db, warnings = load_target_curve(path)

    assert np.allclose(freqs, [20.0, 1000.0, 20000.0])
    assert np.allclose(mag_db, [5.0, 0.0, -3.0])
    assert any("median column" in warning for warning in warnings)


def test_load_target_propagates_parser_warnings(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mixed.txt",
        "20\t9.0\n"
        "100\t7.0\t7.0\t7.0\t7.0\t7.0\n"  # a stray six-column row
        "1000\t4.0\n"
        "1000\t4.0\n"  # a repeated frequency
        "20000\t1.0\n",
    )

    _freqs, _mag_db, warnings = load_target_curve(path)

    assert any("unexpected column count" in warning for warning in warnings)
    assert any("repeated frequency" in warning for warning in warnings)


def test_load_target_warns_when_1khz_is_outside_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "short.txt", "20\t3.0\n100\t1.0\n200\t0.0\n")

    _freqs, mag_db, warnings = load_target_curve(path)

    assert any("1 kHz normalization" in warning for warning in warnings)
    # The nearest end of the file (200 Hz, 0 dB) became the anchor.
    assert mag_db[-1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The common grid
# ---------------------------------------------------------------------------


def test_common_grid_matches_the_rms_average_grid() -> None:
    freqs, _ = compute_rms_average([(np.array([20.0, 20000.0]), np.array([0.0, 0.0]))])

    assert np.array_equal(freqs, GRID)
    assert GRID.size == 1200
    assert GRID[0] == pytest.approx(20.0)
    assert GRID[-1] == pytest.approx(20000.0)
    assert 1000.0 in GRID


def test_common_grid_is_read_only() -> None:
    with pytest.raises(ValueError):
        COMMON_GRID[0] = 1.0


def test_resample_to_common_interpolates_on_log_f_and_holds_edges() -> None:
    freqs = np.array([100.0, 1000.0, 10000.0])
    values = np.array([0.0, 6.0, 12.0])

    resampled = resample_to_common(freqs, values)

    # Linear on log f: the geometric midpoint of 100 and 1000 gets half of 6 dB.
    at_316 = float(np.interp(np.log10(316.227766), np.log10(GRID), resampled))
    assert at_316 == pytest.approx(3.0, abs=0.02)
    # Edges held, not extrapolated.
    assert resampled[0] == pytest.approx(0.0)
    assert resampled[-1] == pytest.approx(12.0)


def test_resample_to_common_sorts_unordered_input() -> None:
    ordered = resample_to_common([100.0, 1000.0, 10000.0], [0.0, 6.0, 12.0])
    shuffled = resample_to_common([1000.0, 10000.0, 100.0], [6.0, 12.0, 0.0])

    assert np.allclose(ordered, shuffled)


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------


def test_delta_is_measurement_minus_target() -> None:
    """A measurement 3 dB louder than the target reads +3, not -3."""
    target = np.zeros_like(GRID)
    measurement = np.full_like(GRID, 3.0)

    result = delta_curve(GRID, measurement, GRID, target, offset_mode="none")

    assert np.allclose(result.delta_db, 3.0, atol=1e-9)
    assert result.offset_db == pytest.approx(0.0)
    assert np.array_equal(result.freqs, GRID)


def test_delta_offset_mode_1khz_zeroes_the_reference_point() -> None:
    target = np.zeros_like(GRID)
    measurement = np.full_like(GRID, 3.0)

    result = delta_curve(GRID, measurement, GRID, target, offset_mode="1khz")

    assert result.offset_db == pytest.approx(3.0)
    assert np.allclose(result.delta_db, 0.0, atol=1e-9)


def test_delta_offset_mode_mean_200_2k_uses_the_midrange_mean() -> None:
    target = np.zeros_like(GRID)
    tilt = 6.0 * np.log10(GRID / 1000.0)  # 0 dB at 1 kHz, tilted elsewhere

    anchored = delta_curve(GRID, tilt, GRID, target, offset_mode="1khz")
    midrange = delta_curve(GRID, tilt, GRID, target, offset_mode="mean_200_2k")

    # Anchoring at 1 kHz removes nothing from a curve that is already 0 there.
    assert anchored.offset_db == pytest.approx(0.0, abs=0.01)
    # The mean of the tilt over 200-2000 Hz sits at their geometric mean.
    expected = 6.0 * np.log10(np.sqrt(200.0 * 2000.0) / 1000.0)
    assert midrange.offset_db == pytest.approx(expected, abs=0.02)
    band = (GRID >= 200.0) & (GRID <= 2000.0)
    assert float(np.mean(midrange.delta_db[band])) == pytest.approx(0.0, abs=1e-9)


def test_delta_rejects_an_unknown_offset_mode() -> None:
    with pytest.raises(ValueError):
        delta_curve(GRID, GRID * 0.0, GRID, GRID * 0.0, offset_mode="loudest")


def test_delta_smoothing_removes_fine_ripple() -> None:
    ripple = 2.0 * np.sin(2.0 * np.pi * np.log2(GRID) * 8.0)  # 1/8-octave period

    rough = delta_curve(
        GRID, ripple, GRID, np.zeros_like(GRID), smoothing_fraction=None, offset_mode="none"
    )
    smooth = delta_curve(
        GRID, ripple, GRID, np.zeros_like(GRID), smoothing_fraction=12, offset_mode="none"
    )

    assert np.max(np.abs(rough.delta_db)) == pytest.approx(2.0, abs=0.05)
    # A 1/12-octave Gaussian leaves about a fifth of a 1/8-octave ripple.
    # Only the interior is checked: the kernel pads with the edge value, so
    # the first and last points keep more of the ripple than the middle does.
    assert np.max(np.abs(smooth.delta_db[100:-100])) < 0.6
    assert np.sqrt(np.mean(np.square(smooth.delta_db))) < 0.3 * np.sqrt(
        np.mean(np.square(rough.delta_db))
    )


def test_delta_smoothing_is_linear_over_the_subtraction() -> None:
    """Smoothing the difference equals smoothing both curves first."""
    rng = np.random.default_rng(7)
    measurement = np.cumsum(rng.normal(0.0, 0.05, GRID.size))
    target = 3.0 * np.log10(GRID / 1000.0)

    combined = delta_curve(
        GRID, measurement, GRID, target, smoothing_fraction=12, offset_mode="none"
    )
    from dms.processing import smooth_fractional_octave

    _, smooth_measure = smooth_fractional_octave(GRID, measurement, fraction=12)
    _, smooth_target = smooth_fractional_octave(GRID, target, fraction=12)

    assert np.allclose(combined.delta_db, smooth_measure - smooth_target, atol=1e-9)


# ---------------------------------------------------------------------------
# Deviation scoring
# ---------------------------------------------------------------------------


def _shelf_and_dip() -> np.ndarray:
    """+6 dB below 200 Hz, -3 dB from 2 kHz to 10 kHz, flat elsewhere."""
    delta = np.zeros_like(GRID)
    delta[GRID < 200.0] = 6.0
    delta[(GRID > 2000.0) & (GRID < 10000.0)] = -3.0
    return delta


def test_deviation_score_reports_each_band() -> None:
    score = deviation_score(_flat_delta(_shelf_and_dip()))
    bands = {band.name: band for band in score.bands}

    assert [band.name for band in score.bands] == [name for name, _, _ in DEFAULT_BANDS]
    assert bands["Bass"].rms_db == pytest.approx(6.0, abs=0.2)
    assert bands["Bass"].mean_db == pytest.approx(6.0, abs=0.2)
    assert bands["Bass"].max_abs_db == pytest.approx(6.0, abs=0.2)
    assert 20.0 <= bands["Bass"].max_abs_freq_hz <= 200.0

    assert bands["Mids"].rms_db == pytest.approx(0.0, abs=0.2)
    assert bands["Treble"].rms_db == pytest.approx(3.0, abs=0.2)
    assert bands["Treble"].mean_db == pytest.approx(-3.0, abs=0.2)
    assert bands["Air"].rms_db == pytest.approx(0.0, abs=0.2)


def test_deviation_overall_stops_at_10k_and_matches_its_own_band() -> None:
    delta = np.zeros_like(GRID)
    delta[GRID > 10000.0] = 20.0  # a wild top octave

    score = deviation_score(_flat_delta(delta))

    assert score.overall_rms_db == pytest.approx(0.0, abs=1e-9)
    assert score.match_percent == pytest.approx(100.0)


def test_match_percent_falls_as_deviation_grows() -> None:
    small = deviation_score(_flat_delta(_shelf_and_dip()))
    large = deviation_score(_flat_delta(_shelf_and_dip() * 2.0))
    perfect = deviation_score(_flat_delta(np.zeros_like(GRID)))

    assert perfect.match_percent == pytest.approx(100.0)
    assert small.match_percent < perfect.match_percent
    assert large.match_percent < small.match_percent
    # The heuristic: 10 points per dB of overall RMS deviation.
    assert small.match_percent == pytest.approx(100.0 - 10.0 * small.overall_rms_db, abs=1e-9)


def test_match_percent_is_clamped_to_zero() -> None:
    score = deviation_score(_flat_delta(np.full_like(GRID, 40.0)))

    assert score.match_percent == 0.0


def test_deviation_score_handles_a_band_with_no_points() -> None:
    score = deviation_score(
        _flat_delta(_shelf_and_dip()), bands=(("Ultrasound", 30000.0, 40000.0),)
    )

    assert score.bands[0].rms_db == 0.0
    assert np.isnan(score.bands[0].max_abs_freq_hz)


def test_format_deviation_summary_has_one_line_per_band() -> None:
    score = deviation_score(_flat_delta(_shelf_and_dip()))

    lines = format_deviation_summary(score).splitlines()

    assert len(lines) == len(DEFAULT_BANDS) + 1
    for band, line in zip(DEFAULT_BANDS, lines):
        assert line.startswith(band[0])
    assert "Bass" in lines[0] and "RMS  6.00 dB" in lines[0]
    assert lines[-1].startswith("Overall") and "match" in lines[-1]


# ---------------------------------------------------------------------------
# EQ suggestion
# ---------------------------------------------------------------------------


def test_eq_recovers_a_known_single_filter() -> None:
    known = PeakingFilter(freq_hz=3000.0, gain_db=5.0, q=2.0)
    # The measurement sits below the target exactly where the filter boosts.
    delta = _flat_delta(-eq_response_db([known], GRID))

    suggestion = suggest_eq(delta)

    assert len(suggestion.filters) == 1
    fitted = suggestion.filters[0]
    assert fitted.gain_db == pytest.approx(known.gain_db, abs=0.3)
    assert fitted.freq_hz == pytest.approx(known.freq_hz, rel=0.10)
    assert fitted.q == pytest.approx(known.q, rel=0.20)


def test_eq_recovers_two_filters_and_flattens_the_delta() -> None:
    known = [
        PeakingFilter(freq_hz=120.0, gain_db=-4.0, q=1.0),
        PeakingFilter(freq_hz=6000.0, gain_db=6.0, q=3.0),
    ]
    response = eq_response_db(known, GRID)
    delta = _flat_delta(-response)
    initial_rms = float(np.sqrt(np.mean(np.square(response))))

    suggestion = suggest_eq(delta)

    assert len(suggestion.filters) >= 2
    assert suggestion.residual_rms_db < 0.2 * initial_rms


def test_eq_stops_at_max_filters() -> None:
    rng = np.random.default_rng(3)
    lumpy = np.cumsum(rng.normal(0.0, 0.4, GRID.size))
    lumpy -= float(np.mean(lumpy))

    suggestion = suggest_eq(_flat_delta(lumpy), max_filters=3)

    assert len(suggestion.filters) <= 3


def test_eq_gains_are_clipped_to_the_limit() -> None:
    needed = eq_response_db([PeakingFilter(1500.0, 20.0, 1.5)], GRID)

    suggestion = suggest_eq(_flat_delta(-needed), max_gain_db=12.0)

    assert suggestion.filters
    assert max(abs(item.gain_db) for item in suggestion.filters) <= 12.0 + 1e-9
    assert suggestion.filters[0].gain_db == pytest.approx(12.0, abs=1e-6)


def test_eq_ignores_filters_below_the_minimum_gain() -> None:
    tiny = eq_response_db([PeakingFilter(3000.0, 0.4, 2.0)], GRID)

    suggestion = suggest_eq(_flat_delta(-tiny), min_gain_db=0.5)

    assert suggestion.filters == []
    assert suggestion.preamp_db == 0.0


def test_preamp_offsets_the_peak_of_the_combined_response() -> None:
    filters = [PeakingFilter(100.0, 6.0, 1.0), PeakingFilter(5000.0, 4.0, 1.0)]

    preamp = compute_preamp_db(filters, GRID)
    peak = float(np.max(eq_response_db(filters, GRID)))

    assert preamp == pytest.approx(-peak, abs=1e-9)
    assert preamp == pytest.approx(-6.0, abs=0.1)


def test_preamp_is_zero_when_nothing_is_boosted() -> None:
    assert compute_preamp_db([PeakingFilter(1000.0, -6.0, 1.0)], GRID) == 0.0
    assert compute_preamp_db([], GRID) == 0.0


def test_suggest_eq_preamp_matches_its_own_filters() -> None:
    known = [PeakingFilter(200.0, 5.0, 1.2), PeakingFilter(7000.0, -6.0, 2.0)]
    suggestion = suggest_eq(_flat_delta(-eq_response_db(known, GRID)))

    peak = float(np.max(eq_response_db(suggestion.filters, GRID)))
    assert suggestion.preamp_db == pytest.approx(-max(0.0, peak), abs=0.1)


def test_suggest_eq_without_preamp_reports_zero() -> None:
    known = [PeakingFilter(200.0, 5.0, 1.2)]
    suggestion = suggest_eq(_flat_delta(-eq_response_db(known, GRID)), preamp=False)

    assert suggestion.filters
    assert suggestion.preamp_db == 0.0


def test_residual_is_the_delta_plus_the_suggested_response() -> None:
    known = [PeakingFilter(300.0, -5.0, 1.5), PeakingFilter(4000.0, 4.0, 2.5)]
    delta = _flat_delta(-eq_response_db(known, GRID))

    suggestion = suggest_eq(delta)
    applied = delta.delta_db + eq_response_db(suggestion.filters, GRID)

    assert np.allclose(suggestion.residual_delta_db, applied, atol=1e-12)
    assert suggestion.residual_rms_db == pytest.approx(
        float(np.sqrt(np.mean(np.square(applied)))), abs=1e-12
    )


def test_eq_response_of_no_filters_is_silence() -> None:
    assert np.allclose(eq_response_db([], GRID), 0.0)


def test_peaking_response_peaks_at_its_centre_frequency() -> None:
    response = eq_response_db([PeakingFilter(1000.0, 6.0, 2.0)], GRID)

    assert float(np.max(response)) == pytest.approx(6.0, abs=0.05)
    assert GRID[int(np.argmax(response))] == pytest.approx(1000.0, rel=0.01)
    assert response[0] == pytest.approx(0.0, abs=0.05)
    assert response[-1] == pytest.approx(0.0, abs=0.05)


def test_format_eq_apo_is_exact() -> None:
    suggestion = EqSuggestion(
        filters=[
            PeakingFilter(freq_hz=105.0, gain_db=-4.5, q=1.41),
            PeakingFilter(freq_hz=3000.0, gain_db=5.0, q=2.0),
        ],
        preamp_db=-5.0,
        residual_rms_db=0.5,
        residual_delta_db=np.zeros(3),
    )

    assert format_eq_apo(suggestion) == (
        "Preamp: -5.0 dB\n"
        "Filter 1: ON PK Fc 105 Hz Gain -4.5 dB Q 1.41\n"
        "Filter 2: ON PK Fc 3000 Hz Gain 5.0 dB Q 2.00"
    )


def test_format_eq_apo_of_an_empty_suggestion_is_just_the_preamp() -> None:
    assert format_eq_apo(EqSuggestion()) == "Preamp: 0.0 dB"


def test_format_eq_table_lists_every_filter() -> None:
    suggestion = EqSuggestion(
        filters=[PeakingFilter(105.0, -4.5, 1.41)],
        preamp_db=-5.0,
        residual_rms_db=0.5,
    )

    lines = format_eq_table(suggestion).splitlines()

    assert "Preamp -5.0 dB" in lines[0]
    assert "residual RMS 0.50 dB" in lines[0]
    assert len(lines) == 3
    assert lines[2].split() == ["1", "105", "-4.5", "1.41"]


def test_format_eq_table_says_so_when_nothing_is_suggested() -> None:
    assert "no filters suggested" in format_eq_table(EqSuggestion())


# ---------------------------------------------------------------------------
# A/B reference layers
# ---------------------------------------------------------------------------


def test_load_reference_from_txt(tmp_path: Path) -> None:
    path = _write(tmp_path / "Sennheiser HD600.txt", "20\t9\n1000\t4\n20000\t1\n")

    layer = load_reference_from_txt(path, color_hint="#15f4ee")

    assert layer.name == "Sennheiser HD600"
    assert layer.source_path == path
    assert layer.color_hint == "#15f4ee"
    assert np.allclose(layer.mag_db, [5.0, 0.0, -3.0])


def test_load_reference_from_txt_can_keep_absolute_levels(tmp_path: Path) -> None:
    path = _write(tmp_path / "spl.txt", "20\t99\n1000\t94\n20000\t91\n")

    layer = load_reference_from_txt(path, name="Loud", normalize=False)

    assert layer.name == "Loud"
    assert np.allclose(layer.mag_db, [99.0, 94.0, 91.0])


def _measure_session_payload() -> dict:
    return {
        "schema_version": 1,
        "metadata": {"rig": "Rig", "brand": "DMS", "model": "Demo"},
        "two_channel": False,
        "sweeps": [
            {"freqs": [20.0, 1000.0, 20000.0], "mag_db": [0.0, 0.0, 0.0]},
            {"freqs": [20.0, 1000.0, 20000.0], "mag_db": [6.0, 0.0, 6.0]},
        ],
        "pairs": [],
    }


#: Power mean of 0 dB and 6 dB.
_POWER_MEAN_0_AND_6 = 10.0 * np.log10((1.0 + 10.0**0.6) / 2.0)


def test_load_reference_from_measure_session(tmp_path: Path) -> None:
    path = tmp_path / "demo.fastgraph-measure.json"
    path.write_text(json.dumps(_measure_session_payload()), encoding="utf-8")

    layer = load_reference_from_measure_session(path)

    assert layer.name == "DMS Demo"
    assert layer.source_path == path
    assert np.array_equal(layer.freqs, GRID)
    # The two sweeps are averaged in power, then anchored at 1 kHz.
    assert layer.mag_db[0] == pytest.approx(_POWER_MEAN_0_AND_6, abs=0.02)
    assert float(np.interp(1000.0, layer.freqs, layer.mag_db)) == pytest.approx(0.0, abs=1e-9)


def test_load_reference_from_measure_session_falls_back_to_raw_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session module may not be importable; the JSON fields still are."""
    path = tmp_path / "demo.fastgraph-measure.json"
    path.write_text(json.dumps(_measure_session_payload()), encoding="utf-8")

    def _no_module(_path: Path):
        raise ImportError("dms.measure_session does not exist yet")

    monkeypatch.setattr(comparison, "_session_curves_via_module", _no_module)
    layer = load_reference_from_measure_session(path, name="Fallback")

    assert layer.name == "Fallback"
    assert layer.mag_db[0] == pytest.approx(_POWER_MEAN_0_AND_6, abs=0.02)


def test_load_reference_from_measure_session_rejects_an_empty_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.fastgraph-measure.json"
    path.write_text(json.dumps({"schema_version": 1, "sweeps": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_reference_from_measure_session(path)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_measurement_against_target_scores_and_corrects(tmp_path: Path) -> None:
    """A file-loaded target, a synthetic measurement, and the whole pipeline."""
    target_path = _write(
        tmp_path / "harman.txt",
        "\n".join(f"{f:.4f}\t{v:.4f}" for f, v in zip(GRID, np.zeros_like(GRID))),
    )
    target_freqs, target_db, warnings = load_target_curve(target_path)
    assert warnings == []

    flaw = PeakingFilter(freq_hz=250.0, gain_db=4.0, q=1.2)
    measurement = eq_response_db([flaw], GRID)

    delta = delta_curve(GRID, measurement, target_freqs, target_db)
    score = deviation_score(delta)
    suggestion = suggest_eq(delta)

    assert score.match_percent < 100.0
    assert suggestion.filters
    assert suggestion.filters[0].gain_db < 0.0  # the fix cuts the bump
    assert suggestion.residual_rms_db < 0.15 * score.overall_rms_db
    assert format_eq_apo(suggestion).startswith("Preamp: ")
