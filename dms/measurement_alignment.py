"""
Pure measurement alignment and marker detection helpers.

These functions operate on already-recorded mono audio and a
MeasurementSignalLayout. They intentionally avoid Qt and sounddevice so timing
behavior can be characterized with synthetic Bluetooth-like recordings.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from dms.measurement_layout import MeasurementSignalLayout


@dataclass(frozen=True)
class AlignmentSettings:
    latency: str = "low"
    bluetooth_headphone_mode: bool = False
    start_alignment_confidence_min: float = 9.0
    end_marker_confidence_min: float = 7.0
    timing_drift_max_ms: float = 35.0


class MeasurementFailureReason:
    LOW_START_CONFIDENCE = "low_start_confidence"
    LOW_END_MARKER_CONFIDENCE = "low_end_marker_confidence"
    TIMING_DRIFT_TOO_LARGE = "timing_drift_too_large"
    SHORT_RECORDING = "short_recording"
    SHORT_ALIGNED_RECORDING = "short_aligned_recording"
    END_MARKER_UNVERIFIED = "end_marker_unverified"


@dataclass(frozen=True)
class StartAlignmentResult:
    selected_sweep_start: int
    sweep_correlation_candidate: int
    marker_locked_candidate: Optional[int]
    start_confidence: float
    start_marker_confidence: float


@dataclass(frozen=True)
class EndMarkerResult:
    selected_sweep_start: int
    marker_1_start: int
    marker_2_start: int
    marker_confidence: float
    timing_error_samples: int
    timing_error_ms: float
    spacing_error_samples: int


@dataclass(frozen=True)
class MeasurementDiagnostics:
    fs: int
    bluetooth_headphone_mode: bool
    latency: str
    start_alignment_confidence_min: float
    end_marker_confidence_min: float
    timing_drift_max_ms: float
    selected_sweep_start: Optional[int] = None
    sweep_correlation_candidate: Optional[int] = None
    marker_locked_candidate: Optional[int] = None
    start_confidence: Optional[float] = None
    start_marker_confidence: Optional[float] = None
    marker_1_start: Optional[int] = None
    marker_2_start: Optional[int] = None
    marker_confidence: Optional[float] = None
    timing_error_samples: Optional[int] = None
    timing_error_ms: Optional[float] = None
    spacing_error_samples: Optional[int] = None
    snr_db: Optional[float] = None
    failure_reason: Optional[str] = None
    failure_message: Optional[str] = None
    buffer_size: Optional[int] = None


class MeasurementAlignmentError(ValueError):
    def __init__(
        self,
        message: str,
        reason: str,
        diagnostics: MeasurementDiagnostics,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class MeasurementAlignmentResult:
    aligned_recording: np.ndarray
    start: StartAlignmentResult
    end: EndMarkerResult
    snr_db: float
    diagnostics: MeasurementDiagnostics


def _diagnostics_from_results(
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start: Optional[StartAlignmentResult] = None,
    end: Optional[EndMarkerResult] = None,
    snr_db: Optional[float] = None,
    failure_reason: Optional[str] = None,
    failure_message: Optional[str] = None,
) -> MeasurementDiagnostics:
    selected_sweep_start = None
    if end is not None:
        selected_sweep_start = end.selected_sweep_start
    elif start is not None:
        selected_sweep_start = start.selected_sweep_start

    return MeasurementDiagnostics(
        fs=int(layout.fs),
        bluetooth_headphone_mode=bool(settings.bluetooth_headphone_mode),
        latency=str(settings.latency),
        start_alignment_confidence_min=float(
            settings.start_alignment_confidence_min
        ),
        end_marker_confidence_min=float(settings.end_marker_confidence_min),
        timing_drift_max_ms=float(settings.timing_drift_max_ms),
        selected_sweep_start=selected_sweep_start,
        sweep_correlation_candidate=(
            start.sweep_correlation_candidate if start is not None else None
        ),
        marker_locked_candidate=(
            start.marker_locked_candidate if start is not None else None
        ),
        start_confidence=start.start_confidence if start is not None else None,
        start_marker_confidence=(
            start.start_marker_confidence if start is not None else None
        ),
        marker_1_start=end.marker_1_start if end is not None else None,
        marker_2_start=end.marker_2_start if end is not None else None,
        marker_confidence=end.marker_confidence if end is not None else None,
        timing_error_samples=(
            end.timing_error_samples if end is not None else None
        ),
        timing_error_ms=end.timing_error_ms if end is not None else None,
        spacing_error_samples=(
            end.spacing_error_samples if end is not None else None
        ),
        snr_db=snr_db,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )


def _raise_alignment_error(
    message: str,
    reason: str,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start: Optional[StartAlignmentResult] = None,
    end: Optional[EndMarkerResult] = None,
    snr_db: Optional[float] = None,
) -> None:
    raise MeasurementAlignmentError(
        message,
        reason,
        _diagnostics_from_results(
            layout=layout,
            settings=settings,
            start=start,
            end=end,
            snr_db=snr_db,
            failure_reason=reason,
            failure_message=message,
        ),
    )


def format_diagnostics_summary(diagnostics: MeasurementDiagnostics) -> str:
    def fmt_int(value: Optional[int]) -> str:
        return "n/a" if value is None else str(value)

    def fmt_float(value: Optional[float], suffix: str = "") -> str:
        return "n/a" if value is None else f"{value:.1f}{suffix}"

    lines = [
        "Measurement diagnostics:",
        f"- Mode: {'Bluetooth' if diagnostics.bluetooth_headphone_mode else 'Standard'}",
        f"- Latency: {diagnostics.latency}",
    ]
    if diagnostics.buffer_size is not None:
        lines.append(f"- Buffer: {diagnostics.buffer_size}")
    if diagnostics.failure_reason:
        lines.append(f"- Failure reason: {diagnostics.failure_reason}")
    lines.extend(
        [
            f"- Selected sweep start: {fmt_int(diagnostics.selected_sweep_start)}",
            f"- Sweep-correlation candidate: {fmt_int(diagnostics.sweep_correlation_candidate)}",
            f"- Marker-locked candidate: {fmt_int(diagnostics.marker_locked_candidate)}",
            f"- Start confidence: {fmt_float(diagnostics.start_confidence)} "
            f"(min {diagnostics.start_alignment_confidence_min:.1f})",
            f"- Start marker confidence: {fmt_float(diagnostics.start_marker_confidence)}",
            f"- End markers: {fmt_int(diagnostics.marker_1_start)}, {fmt_int(diagnostics.marker_2_start)}",
            f"- End confidence: {fmt_float(diagnostics.marker_confidence)} "
            f"(min {diagnostics.end_marker_confidence_min:.1f})",
            f"- Drift: {fmt_float(diagnostics.timing_error_ms, ' ms')} "
            f"(max {diagnostics.timing_drift_max_ms:.1f} ms)",
            f"- Marker spacing error: {fmt_int(diagnostics.spacing_error_samples)} samples",
            f"- SNR: {fmt_float(diagnostics.snr_db, ' dB')}",
        ]
    )
    if diagnostics.failure_message:
        lines.append(f"- Message: {diagnostics.failure_message}")
    return "\n".join(lines)


def is_retryable_timing_failure(
    message: str,
    failure_reason: Optional[str],
) -> bool:
    """Return whether a measurement failure should trigger timing retry UI."""
    if failure_reason is not None:
        return failure_reason in {
            MeasurementFailureReason.LOW_START_CONFIDENCE,
            MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
            MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
        }

    msg = message.lower()
    return any(
        token in msg
        for token in [
            "start-alignment confidence",
            "end-marker confidence",
            "timing drift",
        ]
    )


def normalized_corr_valid(signal: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """Return valid cross-correlation sequence between signal and pattern."""
    sig = np.asarray(signal).astype(np.float64, copy=False)
    pat = np.asarray(pattern).astype(np.float64, copy=False)
    if len(sig) == 0 or len(pat) == 0:
        return np.array([], dtype=np.float64)
    full_len = len(sig) + len(pat) - 1
    nfft = int(2 ** np.ceil(np.log2(full_len)))
    corr_full = np.fft.irfft(
        np.fft.rfft(sig, n=nfft) * np.fft.rfft(pat[::-1], n=nfft),
        n=nfft,
    )[:full_len]
    return corr_full[len(pat) - 1: len(sig)]


def normalized_corrcoef_valid(signal: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """
    Return valid normalized cross-correlation coefficient sequence.

    Each lag is normalized by local signal-window energy and pattern energy.
    """
    corr = normalized_corr_valid(signal, pattern).astype(np.float64, copy=False)
    sig = np.asarray(signal).astype(np.float64, copy=False)
    pat = np.asarray(pattern).astype(np.float64, copy=False)
    m = len(pat)
    if m <= 0 or len(sig) < m:
        return np.array([], dtype=np.float64)

    pat_norm = float(np.sqrt(np.sum(np.square(pat))))
    if pat_norm <= 1e-12:
        return np.array([], dtype=np.float64)

    sig_sq = np.square(sig)
    csum = np.concatenate(([0.0], np.cumsum(sig_sq)))
    win_energy = csum[m:] - csum[:-m]
    win_norm = np.sqrt(np.maximum(win_energy, 1e-24))

    denom = np.maximum(win_norm * pat_norm, 1e-12)
    return corr / denom


def peak_to_rms_confidence(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(np.square(values))))
    return peak / max(rms, 1e-12)


def peak_to_background_confidence(
    values: np.ndarray,
    peak_idx: int,
    exclusion_radius: int,
) -> float:
    """
    Confidence metric using sidelobe floor instead of full-trace RMS.
    """
    if len(values) == 0:
        return 0.0
    mag = np.abs(values).astype(np.float64, copy=False)
    peak_idx = int(np.clip(peak_idx, 0, len(mag) - 1))
    peak = float(mag[peak_idx])

    lo = max(0, peak_idx - int(exclusion_radius))
    hi = min(len(mag), peak_idx + int(exclusion_radius) + 1)
    if lo >= hi:
        return peak / 1e-12

    mask = np.ones(len(mag), dtype=bool)
    mask[lo:hi] = False
    bg = mag[mask]
    if len(bg) < 16:
        floor = float(np.sqrt(np.mean(np.square(mag))))
    else:
        floor = float(np.sqrt(np.mean(np.square(bg))))
    return peak / max(floor, 1e-12)


def peak_to_nextbest_confidence(
    values: np.ndarray,
    peak_idx: int,
    exclusion_radius: int,
) -> float:
    """
    Confidence metric comparing the best peak with the next-best alternative.
    """
    if len(values) == 0:
        return 0.0
    mag = np.abs(values).astype(np.float64, copy=False)
    peak_idx = int(np.clip(peak_idx, 0, len(mag) - 1))
    peak = float(mag[peak_idx])

    lo = max(0, peak_idx - int(exclusion_radius))
    hi = min(len(mag), peak_idx + int(exclusion_radius) + 1)
    mask = np.ones(len(mag), dtype=bool)
    mask[lo:hi] = False
    others = mag[mask]
    if len(others) == 0:
        return peak / 1e-12
    next_best = float(np.max(others))
    return peak / max(next_best, 1e-12)


def _marker_peak_candidates(
    corr: np.ndarray,
    search_start: int,
    expected_start: int,
    fs: int,
    max_peaks: int = 8,
) -> list[tuple[int, float]]:
    """
    Return distinct marker candidates as absolute sample positions plus confidence.

    End-marker search windows can overlap because the marker pair is close
    together. Suppressing nearby correlation lobes lets pair scoring compare
    meaningful marker starts instead of several adjacent samples from one peak.
    """
    if len(corr) == 0:
        return []

    mag = np.abs(corr).astype(np.float64, copy=False)
    rms = float(np.sqrt(np.mean(np.square(corr))))
    min_distance = max(1, int(round(0.01 * fs)))
    ranked = np.argsort(mag)[::-1]
    selected: list[int] = []

    expected_offset = int(expected_start) - int(search_start)
    if 0 <= expected_offset < len(corr):
        selected.append(expected_offset)

    for idx in ranked:
        idx = int(idx)
        if any(abs(idx - existing) < min_distance for existing in selected):
            continue
        selected.append(idx)
        if len(selected) >= max_peaks:
            break

    candidates = []
    seen = set()
    for idx in selected:
        if idx in seen:
            continue
        seen.add(idx)
        confidence = float(mag[idx] / max(rms, 1e-12))
        candidates.append((int(search_start + idx), confidence))
    return candidates


def find_start_alignment(
    rec_mono: np.ndarray,
    sweep: np.ndarray,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
) -> StartAlignmentResult:
    rec = np.asarray(rec_mono)
    sweep_f32 = np.asarray(sweep).astype(np.float32, copy=False)
    sweep_n = layout.sweep_samples

    corr_valid = normalized_corrcoef_valid(rec, sweep_f32)
    if len(corr_valid) == 0:
        _raise_alignment_error(
            "Unable to align recording to sweep.",
            MeasurementFailureReason.LOW_START_CONFIDENCE,
            layout,
            settings,
        )

    max_extra_latency_s = 2.0 if str(settings.latency).lower() == "high" else 1.2
    start_search_radius = int(round(max_extra_latency_s * layout.fs))
    start_search_lo = max(0, layout.excitation_start_sample - start_search_radius)
    start_search_hi = min(
        len(corr_valid), layout.excitation_start_sample + start_search_radius
    )
    if start_search_hi - start_search_lo < 32:
        start_search_lo = 0
        start_search_hi = len(corr_valid)
    corr_start = corr_valid[start_search_lo:start_search_hi]
    start_offset = int(np.argmax(np.abs(corr_start)))
    sweep_start_candidate = start_search_lo + start_offset
    start_idx = sweep_start_candidate

    exclusion = max(int(round(0.025 * layout.fs)), int(round(0.01 * sweep_n)))
    conf_bg = peak_to_background_confidence(corr_valid, start_idx, exclusion)
    conf_next = peak_to_nextbest_confidence(corr_valid, start_idx, exclusion)
    start_conf = min(conf_bg, conf_next)
    min_start_conf = float(settings.start_alignment_confidence_min)
    if bool(settings.bluetooth_headphone_mode):
        min_start_conf = min(min_start_conf, 3.0)

    start_marker_search = int(round(0.35 * layout.fs))
    sm_lo = max(0, start_idx - start_marker_search)
    sm_hi = min(
        len(rec), start_idx + start_marker_search + len(layout.start_marker)
    )
    sm_region = rec[sm_lo:sm_hi]
    start_marker_conf = 0.0
    marker_locked_candidate = None
    sm_corr = normalized_corr_valid(sm_region, layout.start_marker)
    if len(sm_corr) > 0:
        sm_offset = int(np.argmax(np.abs(sm_corr)))
        sm_idx = sm_lo + sm_offset
        start_marker_conf = peak_to_rms_confidence(sm_corr)
        if start_marker_conf >= max(3.5, min_start_conf * 0.7):
            marker_locked_start = (
                sm_idx + len(layout.start_marker) + layout.start_marker_gap_samples
            )
            max_start_idx = len(rec) - sweep_n
            if marker_locked_start <= max_start_idx:
                start_idx = marker_locked_start
                marker_locked_candidate = int(marker_locked_start)
            start_conf = max(start_conf, min_start_conf)

    if start_conf < min_start_conf:
        start_result = StartAlignmentResult(
            selected_sweep_start=int(start_idx),
            sweep_correlation_candidate=int(sweep_start_candidate),
            marker_locked_candidate=marker_locked_candidate,
            start_confidence=float(start_conf),
            start_marker_confidence=float(start_marker_conf),
        )
        _raise_alignment_error(
            f"Low start-alignment confidence ({start_conf:.1f}; marker {start_marker_conf:.1f}). "
            "Please reduce noise, increase playback level, or use higher latency.",
            MeasurementFailureReason.LOW_START_CONFIDENCE,
            layout,
            settings,
            start=start_result,
        )

    return StartAlignmentResult(
        selected_sweep_start=int(start_idx),
        sweep_correlation_candidate=int(sweep_start_candidate),
        marker_locked_candidate=marker_locked_candidate,
        start_confidence=float(start_conf),
        start_marker_confidence=float(start_marker_conf),
    )


def find_end_markers(
    rec_mono: np.ndarray,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start_result: StartAlignmentResult,
) -> EndMarkerResult:
    rec = np.asarray(rec_mono)
    sweep_n = layout.sweep_samples
    max_start_idx = len(rec) - sweep_n
    if max_start_idx < 0:
        _raise_alignment_error(
            "Recording shorter than expected.",
            MeasurementFailureReason.SHORT_RECORDING,
            layout,
            settings,
            start=start_result,
        )

    candidates = [int(np.clip(start_result.sweep_correlation_candidate, 0, max_start_idx))]
    if start_result.selected_sweep_start != start_result.sweep_correlation_candidate:
        candidates.append(int(np.clip(start_result.selected_sweep_start, 0, max_start_idx)))

    marker_search = (
        int(round(0.14 * layout.fs))
        if bool(settings.bluetooth_headphone_mode)
        else int(round(0.08 * layout.fs))
    )
    end_conf_min = float(settings.end_marker_confidence_min)
    if bool(settings.bluetooth_headphone_mode):
        end_conf_min = min(end_conf_min, 2.5)

    best_result = None
    best_err_result = None
    marker = layout.end_marker
    for cand in candidates:
        expected_marker_1 = cand + sweep_n + layout.end_marker_gap_samples
        expected_marker_2 = (
            expected_marker_1 + len(marker) + layout.end_marker_pair_gap_samples
        )

        search_start_1 = max(0, expected_marker_1 - marker_search)
        search_stop_1 = min(len(rec), expected_marker_1 + marker_search + len(marker))
        marker_region_1 = rec[search_start_1:search_stop_1]
        marker_corr_1 = normalized_corr_valid(marker_region_1, marker)
        if len(marker_corr_1) == 0:
            continue
        peaks_1 = _marker_peak_candidates(
            marker_corr_1,
            search_start_1,
            expected_marker_1,
            layout.fs,
        )

        search_start_2 = max(0, expected_marker_2 - marker_search)
        search_stop_2 = min(len(rec), expected_marker_2 + marker_search + len(marker))
        marker_region_2 = rec[search_start_2:search_stop_2]
        marker_corr_2 = normalized_corr_valid(marker_region_2, marker)
        peaks_2 = _marker_peak_candidates(
            marker_corr_2,
            search_start_2,
            expected_marker_2,
            layout.fs,
        )

        spacing_expected = len(marker) + layout.end_marker_pair_gap_samples
        penalty_unit = max(1.0, 0.01 * layout.fs)
        for marker_start_1, marker_conf_1 in peaks_1:
            for marker_start_2, marker_conf_2 in peaks_2:
                spacing_observed = marker_start_2 - marker_start_1
                if spacing_observed <= 0:
                    continue

                timing_err_1 = abs(marker_start_1 - expected_marker_1)
                timing_err_2 = abs(marker_start_2 - expected_marker_2)
                timing_err = max(timing_err_1, timing_err_2)
                spacing_err = abs(spacing_observed - spacing_expected)
                marker_conf = min(marker_conf_1, marker_conf_2)
                score = marker_conf - (timing_err / penalty_unit) - (
                    spacing_err / penalty_unit
                )
                result = (
                    score,
                    cand,
                    marker_start_1,
                    marker_start_2,
                    timing_err,
                    marker_conf,
                    spacing_err,
                )
                if best_result is None or result[0] > best_result[0]:
                    best_result = result
                if marker_conf >= max(1.8, end_conf_min * 0.85):
                    if (
                        best_err_result is None
                        or timing_err < best_err_result[4]
                        or (
                            timing_err == best_err_result[4]
                            and spacing_err < best_err_result[6]
                        )
                        or (
                            timing_err == best_err_result[4]
                            and spacing_err == best_err_result[6]
                            and marker_conf > best_err_result[5]
                        )
                    ):
                        best_err_result = result

    if best_result is None and best_err_result is None:
        _raise_alignment_error(
            "Unable to verify end marker timing.",
            MeasurementFailureReason.END_MARKER_UNVERIFIED,
            layout,
            settings,
            start=start_result,
        )

    chosen = best_err_result if best_err_result is not None else best_result
    _, start_idx, marker_start_1, marker_start_2, timing_err, marker_conf, spacing_err = chosen
    drift_ms = 1000.0 * timing_err / float(layout.fs)
    return EndMarkerResult(
        selected_sweep_start=int(start_idx),
        marker_1_start=int(marker_start_1),
        marker_2_start=int(marker_start_2),
        marker_confidence=float(marker_conf),
        timing_error_samples=int(timing_err),
        timing_error_ms=float(drift_ms),
        spacing_error_samples=int(spacing_err),
    )


def estimate_snr_db(
    rec_mono: np.ndarray,
    aligned_recording: np.ndarray,
    marker_2_start: int,
    marker_len: int,
    start_idx: int,
    fs: int,
) -> float:
    noise_win_n = int(round(0.12 * fs))
    rec = np.asarray(rec_mono)
    pre_noise = rec[max(0, start_idx - noise_win_n):start_idx]
    post_noise_start = marker_2_start + marker_len
    post_noise = rec[
        post_noise_start:min(len(rec), post_noise_start + noise_win_n)
    ]
    noise_parts = [seg for seg in (pre_noise, post_noise) if len(seg) > 8]
    if noise_parts:
        noise_concat = np.concatenate(noise_parts)
        noise_rms = float(np.sqrt(np.mean(np.square(noise_concat))))
    else:
        noise_rms = 0.0
    signal_rms = float(np.sqrt(np.mean(np.square(aligned_recording))))
    if noise_rms > 1e-12 and signal_rms > 0.0:
        return float(20.0 * np.log10(signal_rms / noise_rms))
    if signal_rms > 0.0:
        return 120.0
    return 0.0


def align_recording_to_layout(
    rec_mono: np.ndarray,
    sweep: np.ndarray,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
) -> MeasurementAlignmentResult:
    rec = np.asarray(rec_mono)
    if len(rec) < layout.sweep_samples:
        _raise_alignment_error(
            "Recording shorter than expected.",
            MeasurementFailureReason.SHORT_RECORDING,
            layout,
            settings,
        )

    start_result = find_start_alignment(rec, sweep, layout, settings)
    end_result = find_end_markers(rec, layout, settings, start_result)

    end_idx = end_result.selected_sweep_start + layout.sweep_samples
    if end_idx > len(rec):
        _raise_alignment_error(
            "Aligned recording shorter than expected. "
            "Please increase post-sweep silence or use Bluetooth mode.",
            MeasurementFailureReason.SHORT_ALIGNED_RECORDING,
            layout,
            settings,
            start=start_result,
            end=end_result,
        )
    sweep_rec = rec[end_result.selected_sweep_start:end_idx].astype(
        np.float32, copy=False
    )

    end_conf_min = float(settings.end_marker_confidence_min)
    if bool(settings.bluetooth_headphone_mode):
        end_conf_min = min(end_conf_min, 2.5)
    marker_conf = end_result.marker_confidence
    if marker_conf < end_conf_min:
        _raise_alignment_error(
            f"Low end-marker confidence ({marker_conf:.1f}). "
            "Timing reliability is low; retrying is recommended.",
            MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
            layout,
            settings,
            start=start_result,
            end=end_result,
        )

    max_drift_samples = int(
        round((float(settings.timing_drift_max_ms) / 1000.0) * layout.fs)
    )
    if end_result.timing_error_samples > max_drift_samples:
        ms = 1000.0 * end_result.timing_error_samples / float(layout.fs)
        if bool(settings.bluetooth_headphone_mode):
            hint = "Please retry; Bluetooth timing jitter exceeded the current tolerance."
        else:
            hint = "Please retry and consider high latency mode."
        _raise_alignment_error(
            f"Timing drift too large ({ms:.1f} ms). {hint}",
            MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
            layout,
            settings,
            start=start_result,
            end=end_result,
        )

    if bool(settings.bluetooth_headphone_mode):
        marker_conf = max(marker_conf, end_conf_min)
        end_result = EndMarkerResult(
            selected_sweep_start=end_result.selected_sweep_start,
            marker_1_start=end_result.marker_1_start,
            marker_2_start=end_result.marker_2_start,
            marker_confidence=float(marker_conf),
            timing_error_samples=end_result.timing_error_samples,
            timing_error_ms=end_result.timing_error_ms,
            spacing_error_samples=end_result.spacing_error_samples,
        )

    snr_db = estimate_snr_db(
        rec,
        sweep_rec,
        end_result.marker_2_start,
        len(layout.end_marker),
        end_result.selected_sweep_start,
        layout.fs,
    )
    return MeasurementAlignmentResult(
        aligned_recording=sweep_rec,
        start=start_result,
        end=end_result,
        snr_db=float(snr_db),
        diagnostics=_diagnostics_from_results(
            layout=layout,
            settings=settings,
            start=start_result,
            end=end_result,
            snr_db=float(snr_db),
        ),
    )
