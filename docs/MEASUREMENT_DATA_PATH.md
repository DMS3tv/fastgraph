# The measurement data path

This document is for anyone verifying that FastGraph does not alter a measured
frequency response beyond the operations it advertises. It follows one sweep
from the signal that leaves the sound card to the file that reaches disk or
Squiglink, names the function responsible for each step, states exactly what
that step does to magnitude values, and points at the test that pins it.

The short version: **the recording is never scaled.** Alignment only slices
it. After deconvolution, every change to a magnitude is one of four things:

| Operation | Where | Effect on values |
|---|---|---|
| Constant offset | 1 kHz normalization, dB SPL offset, Curator offset | adds one number to every point |
| Power mean | log-cell resampling, RMS average | combines neighbouring or repeated values as power |
| Gaussian smoothing | 1/N-octave display smoothing | weighted average over a log-frequency window |
| HRTF subtraction | compensation | subtracts the selected HRTF curve point by point |

Nothing else touches the numbers. Function names are stable; line numbers are
not, so none are given. Run `python -m pytest tests/test_measurement_path.py`
for the tests written specifically for this document.

## 1. Stimulus

`generate_log_sweep` in `dms/processing.py` builds a Farina logarithmic sine
sweep with 10 ms raised-cosine fades at each end and returns float32.
`MeasureController.start_next_sweep` multiplies it by the output-level gain. The
scaled sweep is both what plays and what the deconvolution later uses as its
reference, so the gain cancels out of the transfer function.

`build_measurement_layout` in `dms/measurement_layout.py` places a 0.2 s wake
primer, a gap, pre-silence, the excitation, and post-silence. In Bluetooth
mode the excitation is wrapped in coded start and end markers. Markers are
timing aids only; they are outside the sweep and never enter the transfer
function.

*Values changed:* none.
*Tests:* `tests/test_measurement_layout.py`.

## 2. Playback and recording

`SweepWorker._run_inner` in `dms/audio_engine.py` calls `sounddevice.playrec`
in float32, keeps the selected input channel, and hands the raw recording to
alignment. On success it emits `finished(aligned + tail, sweep)`: the aligned
sweep-length slice, followed by the post-sweep tail that the impulse-response
window may use. It concatenates; it does not scale.

*Values changed:* none.
*Tests:* `tests/test_measurement_path.py` (tail concatenation),
`tests/test_audio_devices.py`.

## 3. Alignment

`align_recording_to_layout` in `dms/measurement_alignment.py` finds where the
sweep starts in the recording (`find_start_alignment`, normalized
cross-correlation) and returns `recording[start:start + len(sweep)]` as
float32. In Bluetooth mode the coded markers refine the start; when they are
unreliable, `_bluetooth_sweep_fallback_result` uses the sweep correlation
instead. The time-stretch search in the marker code stretches the marker
*templates*, never the recording.

The integrity gate (`_enforce_sweep_integrity`) decides whether the sweep is
accepted; it rejects only when both the start confidence and the mid-band
noise margin are below their minimums. It never modifies samples. A rejected
sweep produces an error, not a curve.

*Values changed:* none. The output is a slice of the input.
*Tests:* `tests/test_measurement_alignment.py` asserts
`aligned_recording == sweep` to 1e-6 for synthetic recordings in eleven
cases; `tests/test_measurement_integrity.py`; `tests/test_bluetooth_reliability.py`.

## 4. Deconvolution and spectrum

`compute_frequency_response` in `dms/processing.py` wraps three steps:

1. `deconvolve_sweep`: `H = Y · conj(X) / (|X|² + ε)` with
   `ε = 1e-12 · max|X|²`, on an FFT twice the longest input so harmonic
   distortion products cannot alias onto the linear response. The inverse
   FFT gives the impulse response.
2. `window_impulse_response`: keeps 5 ms before the direct arrival and a
   tail of `min(recorded tail, 0.45 · Δt₂, 300 ms)` after it, with half-Hann
   tapers of 2 ms and 20 ms. `Δt₂` is where the second-harmonic packet
   lands, so the window excludes distortion products. When no tail was
   recorded the full impulse response is kept.
3. `frequency_response_from_ir`: zero-pads to a bin spacing of at most
   0.1 Hz and returns `20·log10|FFT|` inside the measurement band.

*Values changed:* this is the measurement. The regularization `ε` is
twelve orders of magnitude below the sweep energy and has no effect inside
the band.
*Tests:* `tests/test_processing_deconvolution.py` shows the windowed result
matches the legacy direct spectral division within 0.1 dB on a linear
system, recovers H2/H3 of a known nonlinear system within 0.2 dB, and does
not change the linear response of that system by more than 0.2 dB.

## 5. Level reference and resampling

In `MeasureController.on_sweep_finished`:

- **1 kHz reference mode** (default): `normalize_at_1khz` subtracts the
  linearly interpolated value at 1000 Hz. Constant offset.
- **dB SPL mode**: `absolute_spl_offset_db` adds one constant derived from
  the calibrated microphone sensitivity and the output level. No
  re-zeroing. If the input device is not calibrated the controller falls back
  to the 1 kHz mode and says so in the status bar.
- Two-channel mode: `shared_normalize_pair_at_1khz` in `dms/two_channel.py`
  subtracts one shared offset from both channels so their relative level
  survives.

Then `downsample_to_log_points` resamples to 600 log-spaced points. Each
output point is the power mean of the FFT bins inside its log cell; cells
with fewer than two bins fall back to linear interpolation. The 1 kHz point
is kept exact.

*Values changed:* one constant offset, then a power mean per cell.
*Tests:* `tests/test_measurement_path.py` (normalize, SPL path, the
600-point chain), `tests/test_processing_resample.py` (power mean, exact
1 kHz, fallback), `tests/test_processing_spl.py` (94 dB SPL ↔ 1 Pa; pure
offset), `tests/test_two_channel.py`.

## 6. Keep, average, variation, HRTF

- `MeasureController.on_keep` stores the 600-point curve unchanged.
- `MeasureController.recompute_average` calls `compute_rms_average`: every kept
  curve is interpolated onto a shared 1200-point log grid and the curves
  are combined as a power mean, then re-zeroed at 1 kHz unless in dB SPL
  mode.
- `MeasureController.variation_from_curves` builds the population band: each
  curve interpolated onto the grid, HRTF applied, smoothed at 1/48 octave,
  then the 10th/25th/50th/75th/90th percentiles across curves.
- HRTF compensation is `HRTFCurve.apply` in `dms/hrtf.py`:
  `curve − hrtf`, with the HRTF's edge values held outside its range.
  `apply_to_variation` combines the population spread with the HRTF spread
  in quadrature.

*Values changed:* power mean, constant offset, HRTF subtraction, smoothing
(variation band only).
*Tests:* `tests/test_measurement_path.py` (RMS average is a power mean and
re-zeroes only when asked), `tests/test_hrtf_edges.py`,
`tests/test_hrtf_variation_combination.py`,
`tests/test_main_window_population_variation.py`.

## 7. Display

`MeasureController.bottom_curve_for_display` smooths the average with
`smooth_fractional_octave` at 1/48 octave for the bottom plot. Kept curves
on the top plot are drawn unsmoothed.

`smooth_fractional_octave` is a Gaussian on the log-frequency axis whose
full width at half maximum is 1/N octave. Data that is not log-spaced (for
example an unsmoothed REW export, which is linear in frequency) is
resampled onto a uniform log grid first, smoothed there, and interpolated
back, so the bandwidth is the same at every frequency. This was the bug in
release 0.4.2, where the kernel width was fixed in samples and flattened
the bass of linear-grid files.

`DualPlotWidget._display_curve` in `dms/ui/dual_plot_widget.py` performs
no arithmetic. In the retro themes it passes the curve through
`retro_step_series`, which keeps a subset of the original samples (extremes
preserved) and draws them as a staircase; it invents no values.

*Values changed:* smoothing only, and only on the bottom plot.
*Tests:* `tests/test_processing_smoothing.py` (byte-identical regression on
log grids; linear-grid equivalence), `tests/test_graph_display.py`.

## 8. Export and upload

`export_curve` in `dms/export.py` writes frequency and dB with four and six
decimals and records what was done in the header: `* Smoothing: 1/48 octave`,
`* Level: dB SPL (calibrated)` or the 1 kHz normalization line, the HRTF
file, and the number of averaged sweeps. It writes the values it is given.

- **Export Average**, **Export All** and the **Squiglink upload** all write
  `MeasureController.bottom_curve_for_display()`: the same smoothed, compensated curve the
  bottom plot shows. The rule is *export what you display*.
- **Export Variation** writes the six-column percentile band.
- `upload_export_sftp` in `dms/squiglink.py` sends the exported file with
  `sftp.put`, unchanged.

*Values changed:* none beyond the smoothing already on screen.
*Tests:* `tests/test_export.py` (headers), `tests/test_main_window_export_all.py`
(Export All equals Export Average), `tests/test_measurement_path.py`
(upload body equals the export).

## Curator imports

`parse_measurement_txt` in `dms/curator/parser.py` reads any REW-style text
file (any delimiter, decimal commas, byte-order marks, UTF-16, extra
columns). It drops rows with non-positive frequency and averages repeated
frequencies. Values are otherwise stored as read.

`CuratorWidget.add_curve` stores `−(value at 1 kHz)` as the layer's vertical
offset so the layer reads 0 dB at 1 kHz; the file is not modified.
`apply_layer_transform` in `dms/curator/transforms.py` applies the HRTF
(subtraction) and the offset (constant). `smooth_curve` is display-only and
uses the same `smooth_fractional_octave`. `combine_variation_layers` pools
several bands as a mixture of normals weighted by sweep count.

*Values changed:* constant offset, HRTF subtraction, display smoothing,
band pooling.
*Tests:* `tests/test_curator_parser.py`, `tests/test_curator_transforms.py`,
`tests/test_measurement_path.py` (offset to zero at 1 kHz).

## What to check if you suspect a change

1. Feed a known file through Curator and compare the exported PNG's curve
   with the file at a few frequencies; the only differences should be the
   1 kHz offset and, if enabled, smoothing.
2. Run `tests/test_processing_deconvolution.py`: the synthetic linear
   system round-trips within 0.1 dB.
3. Run `tests/test_measurement_path.py`: every offset, mean and
   concatenation above is asserted numerically.
4. Outside `dms/processing.py`, `dms/hrtf.py` and `dms/curator/transforms.py`
   only two modules compute on magnitudes, and both are display or
   comparison features rather than the measurement: `dms/comparison.py`
   (target delta, deviation score, EQ fit; it re-zeroes the target at 1 kHz
   and never writes back to a kept curve) and the R&D tab
   (`dms/ui/rnd_widget.py`: per-layer vertical offset and display
   smoothing, both recorded in the R&D export header). Anything else that
   assigns a magnitude is copying or storing.
