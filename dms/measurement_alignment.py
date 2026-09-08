"""
Pure measurement alignment and marker detection helpers.

These functions operate on already-recorded mono audio and a
MeasurementSignalLayout. They intentionally avoid Qt and sounddevice so timing
behavior can be characterized with synthetic Bluetooth-like recordings.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from dms.measurement_layout import MeasurementSignalLayout


@dataclass(frozen=True)
class AlignmentSettings:
    latency: str = "low"
    bluetooth_headphone_mode: bool = False
    # Minimum peak-to-background confidence of the sweep correlation. Applies
    # in every mode. 0 turns the check off. Rolloff at the band edges raises
    # this value, so it is safe for headphones with little output at 20 Hz or
    # 20 kHz.
    start_alignment_confidence_min: float = 6.0
    end_marker_confidence_min: float = 7.0
    timing_drift_max_ms: float = 35.0
    # Minimum level margin of the middle 60 % of the aligned sweep above the
    # measured noise floor, in dB. 0 turns the check off. A recording is only
    # rejected when BOTH this margin and the alignment confidence fall below
    # their minimums; each metric alone has valid-measurement counterexamples.
    sweep_noise_margin_min_db: float = 3.0
    # SNR below this value produces a warning, never a rejection. 0 disables.
    snr_warn_db: float = 10.0
    # Hard floor on the normalized correlation coefficient at the chosen sweep
    # start. Valid sweeps sit at 0.39 or above, silence and unrelated signals
    # at 0.04 or below. Not user-configurable.
    peak_correlation_min: float = 0.10


class MeasurementFailureReason:
    LOW_START_CONFIDENCE = "low_start_confidence"
    LOW_END_MARKER_CONFIDENCE = "low_end_marker_confidence"
    TIMING_DRIFT_TOO_LARGE = "timing_drift_too_large"
    SHORT_RECORDING = "short_recording"
    SHORT_ALIGNED_RECORDING = "short_aligned_recording"
    END_MARKER_UNVERIFIED = "end_marker_unverified"
    LOW_SNR = "low_snr"
    INCOMPLETE_SWEEP = "incomplete_sweep"
    INVALID_RECORDING = "invalid_recording"


class MeasurementWarningReason:
    BLUETOOTH_MARGINAL_DRIFT = "bluetooth_marginal_drift"
    BLUETOOTH_SWEEP_FALLBACK = "bluetooth_sweep_fallback"
    LOW_SNR = "low_snr"


@dataclass(frozen=True)
class StartAlignmentResult:
    selected_sweep_start: int
    sweep_correlation_candidate: int
    marker_locked_candidate: Optional[int]
    start_confidence: float
    start_marker_confidence: float
    # Peak-to-background confidence of the sweep correlation. This is the
    # value that start_confidence now reports; it is repeated here so the
    # two other components stay visible in diagnostics.
    background_confidence: float = 0.0
    # Peak-to-next-best confidence. Reported only; a valid sweep with a strong
    # secondary arrival scores close to 1.0 here, so it must not gate.
    nextbest_confidence: float = 0.0
    # Normalized correlation coefficient at the sweep-correlation candidate.
    peak_correlation: float = 0.0


@dataclass(frozen=True)
class SweepIntegrity:
    """Level evidence that the aligned window contains the played sweep."""

    noise_rms: float
    signal_rms: float
    midband_margin_db: float
    coverage_db: tuple[float, ...]


@dataclass(frozen=True)
class EndMarkerResult:
    selected_sweep_start: int
    marker_1_start: int
    marker_2_start: int
    marker_confidence: float
    timing_error_samples: int
    timing_error_ms: float
    spacing_error_samples: int
    raw_marker_confidence: Optional[float] = None
    marker_agreement: Optional[float] = None
    marker_identity_ratio: Optional[float] = None
    marker_template_stretch: Optional[float] = None


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
    raw_marker_confidence: Optional[float] = None
    marker_agreement: Optional[float] = None
    marker_identity_ratio: Optional[float] = None
    marker_template_stretch: Optional[float] = None
    snr_db: Optional[float] = None
    failure_reason: Optional[str] = None
    failure_message: Optional[str] = None
    warning_reason: Optional[str] = None
    warning_message: Optional[str] = None
    buffer_size: Optional[int] = None
    alignment_mode: Optional[str] = None
    marker_failure_reason: Optional[str] = None
    peak_correlation: Optional[float] = None
    start_background_confidence: Optional[float] = None
    start_nextbest_confidence: Optional[float] = None
    midband_margin_db: Optional[float] = None
    coverage_db: Optional[tuple[float, ...]] = None
    sweep_noise_margin_min_db: Optional[float] = None


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
    #: Recording captured after the sweep ended, for impulse-response tails.
    #: ``aligned_recording`` itself stays exactly sweep-length; analysis code
    #: that wants the decay concatenates this onto it. May be empty.
    aligned_recording_tail: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )


_BLUETOOTH_FALLBACK_MIN_SWEEP_CONFIDENCE = 3.0
_BLUETOOTH_FALLBACK_STRONG_SWEEP_CONFIDENCE = 5.0
_BLUETOOTH_FALLBACK_MIN_START_MARKER_CONFIDENCE = 3.5
# Real Bluetooth runs in the diagnostics log have a median SNR of 16 dB; the
# old 18 dB floor made the fallback unreachable in practice.
_BLUETOOTH_FALLBACK_MIN_SNR_DB = 10.0
_BLUETOOTH_FALLBACK_MIN_SWEEP_RMS = 1e-7
# Normalized correlation coefficient of the deconvolution-free sweep match.
# Unlike the old raw time-domain dot product with the undeconvolved sweep,
# this is not defeated by a headphone's phase response.
_BLUETOOTH_FALLBACK_MIN_PEAK_CORRELATION = 0.10
# End-marker quality floors. Below these the marker pair is not trusted for
# timing; the sweep-correlation fallback is used instead of failing. Only a
# reversed marker order (identity below 1.0) blocks the fallback outright.
_MARKER_MIN_AGREEMENT = 0.18
_MARKER_MIN_IDENTITY_RATIO = 1.02


def _diagnostics_from_results(
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start: Optional[StartAlignmentResult] = None,
    end: Optional[EndMarkerResult] = None,
    snr_db: Optional[float] = None,
    failure_reason: Optional[str] = None,
    failure_message: Optional[str] = None,
    warning_reason: Optional[str] = None,
    warning_message: Optional[str] = None,
    alignment_mode: Optional[str] = None,
    marker_failure_reason: Optional[str] = None,
    integrity: Optional[SweepIntegrity] = None,
) -> MeasurementDiagnostics:
    selected_sweep_start = None
    if end is not None:
        selected_sweep_start = end.selected_sweep_start
    elif start is not None:
        selected_sweep_start = start.selected_sweep_start

    if alignment_mode is None:
        alignment_mode = "marker" if settings.bluetooth_headphone_mode else "sweep"

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
        raw_marker_confidence=(
            end.raw_marker_confidence if end is not None else None
        ),
        marker_agreement=end.marker_agreement if end is not None else None,
        marker_identity_ratio=(
            end.marker_identity_ratio if end is not None else None
        ),
        marker_template_stretch=(
            end.marker_template_stretch if end is not None else None
        ),
        snr_db=snr_db,
        failure_reason=failure_reason,
        failure_message=failure_message,
        warning_reason=warning_reason,
        warning_message=warning_message,
        alignment_mode=alignment_mode,
        marker_failure_reason=marker_failure_reason,
        peak_correlation=(
            float(start.peak_correlation) if start is not None else None
        ),
        start_background_confidence=(
            float(start.background_confidence) if start is not None else None
        ),
        start_nextbest_confidence=(
            float(start.nextbest_confidence) if start is not None else None
        ),
        midband_margin_db=(
            float(integrity.midband_margin_db) if integrity is not None else None
        ),
        coverage_db=(
            tuple(float(v) for v in integrity.coverage_db)
            if integrity is not None
            else None
        ),
        sweep_noise_margin_min_db=float(settings.sweep_noise_margin_min_db),
    )


def _raise_alignment_error(
    message: str,
    reason: str,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start: Optional[StartAlignmentResult] = None,
    end: Optional[EndMarkerResult] = None,
    snr_db: Optional[float] = None,
    integrity: Optional[SweepIntegrity] = None,
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
            integrity=integrity,
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
    if diagnostics.warning_reason:
        lines.append(f"- Warning reason: {diagnostics.warning_reason}")
    if diagnostics.alignment_mode:
        lines.append(f"- Alignment mode: {diagnostics.alignment_mode}")
    if diagnostics.marker_failure_reason:
        lines.append(f"- Marker failure reason: {diagnostics.marker_failure_reason}")
    lines.extend(
        [
            f"- Selected sweep start: {fmt_int(diagnostics.selected_sweep_start)}",
            f"- Sweep-correlation candidate: {fmt_int(diagnostics.sweep_correlation_candidate)}",
            f"- Start confidence: {fmt_float(diagnostics.start_confidence)} "
            f"(min {diagnostics.start_alignment_confidence_min:.1f})",
        ]
    )
    if diagnostics.peak_correlation is not None:
        lines.append(
            f"- Sweep correlation peak: {diagnostics.peak_correlation:.3f}"
        )
    if diagnostics.start_nextbest_confidence is not None:
        lines.append(
            f"- Next-best alignment ratio: {diagnostics.start_nextbest_confidence:.1f} "
            "(reported only)"
        )
    if diagnostics.midband_margin_db is not None:
        margin_min = diagnostics.sweep_noise_margin_min_db
        min_text = f" (min {margin_min:.1f} dB)" if margin_min is not None else ""
        lines.append(
            f"- Mid-band level above noise: {diagnostics.midband_margin_db:.1f} dB{min_text}"
        )
    if diagnostics.coverage_db:
        coverage = " ".join(f"{value:.0f}" for value in diagnostics.coverage_db)
        lines.append(f"- Sweep coverage by tenth (dB above noise): {coverage}")
    if diagnostics.bluetooth_headphone_mode:
        lines.extend(
            [
                f"- Marker-locked candidate: {fmt_int(diagnostics.marker_locked_candidate)}",
                f"- Start marker confidence: {fmt_float(diagnostics.start_marker_confidence)}",
                f"- End markers: {fmt_int(diagnostics.marker_1_start)}, {fmt_int(diagnostics.marker_2_start)}",
                f"- End confidence: {fmt_float(diagnostics.marker_confidence)} "
                f"(min {diagnostics.end_marker_confidence_min:.1f})",
                f"- Drift: {fmt_float(diagnostics.timing_error_ms, ' ms')} "
                f"(max {diagnostics.timing_drift_max_ms:.1f} ms)",
                f"- Marker spacing error: {fmt_int(diagnostics.spacing_error_samples)} samples",
            ]
        )
    lines.append(f"- SNR: {fmt_float(diagnostics.snr_db, ' dB')}")
    if diagnostics.marker_agreement is not None:
        if diagnostics.raw_marker_confidence is not None:
            lines.append(
                f"- Raw end confidence: {diagnostics.raw_marker_confidence:.1f}"
            )
        lines.append(
            f"- Marker chip agreement: {diagnostics.marker_agreement:.2f}"
        )
    if diagnostics.marker_identity_ratio is not None:
        lines.append(
            f"- Marker identity ratio: {diagnostics.marker_identity_ratio:.2f}"
        )
    if diagnostics.marker_template_stretch is not None:
        lines.append(
            f"- Marker template stretch: {diagnostics.marker_template_stretch:.3f}x"
        )
    if diagnostics.failure_message:
        lines.append(f"- Message: {diagnostics.failure_message}")
    if diagnostics.warning_message:
        lines.append(f"- Warning: {diagnostics.warning_message}")
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
            MeasurementFailureReason.END_MARKER_UNVERIFIED,
            MeasurementFailureReason.LOW_SNR,
            MeasurementFailureReason.INCOMPLETE_SWEEP,
            MeasurementFailureReason.INVALID_RECORDING,
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


#: Substrings that identify an audio-device or stream failure rather than a
#: measurement-quality failure. Sourced from every ``error.emit`` in
#: ``SweepWorker._run_inner`` (``dms/audio_engine.py``) plus the device guard in
#: ``MainWindow._start_next_sweep``. Matched case-insensitively.
#: Only a device that is gone or lacks the channel is hopeless. A PortAudio
#: stream error can be transient (a busy exclusive-mode device, a hiccup at
#: stream start), so it keeps its retry prompt.
_DEVICE_FAILURE_TOKENS = (
    "device unavailable",
    "not available (device has",
    "input device unavailable",
    "output device unavailable",
    "selected device is unavailable",
)


def is_device_failure(
    message: str,
    failure_reason: Optional[str],
) -> bool:
    """Return whether a sweep failure came from the audio device, not the data.

    A device failure is terminal: retrying it only produces the same error, so
    two-channel mode must not offer a "retry the pair?" prompt for it.

    ``failure_reason`` is set only by the measurement-quality checks that raise
    through :func:`_raise_alignment_error`; when it is present the failure is by
    definition a data-quality failure, never a device one.
    """
    if failure_reason is not None:
        return False
    msg = (message or "").lower()
    return any(token in msg for token in _DEVICE_FAILURE_TOKENS)


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


def _marker_component_agreement(
    signal_window: np.ndarray,
    marker: np.ndarray,
    chip_count: int = 7,
) -> float:
    """
    Return how consistently a candidate matches the marker across chips.

    A narrow resonant peak can produce a large whole-marker correlation at one
    lag. Coded packets should also agree across their short time chips, so this
    score down-weights candidates explained by only part of the marker.
    """
    sig = np.asarray(signal_window).astype(np.float64, copy=False)
    pat = np.asarray(marker).astype(np.float64, copy=False)
    if len(sig) < len(pat) or len(pat) < chip_count:
        return 0.0

    sig = sig[:len(pat)]
    edges = np.linspace(0, len(pat), chip_count + 1, dtype=int)
    agreements = []
    for idx in range(chip_count):
        lo = edges[idx]
        hi = edges[idx + 1]
        if hi - lo < 4:
            continue
        sig_chip = sig[lo:hi]
        pat_chip = pat[lo:hi]
        sig_norm = float(np.sqrt(np.sum(np.square(sig_chip))))
        pat_norm = float(np.sqrt(np.sum(np.square(pat_chip))))
        if sig_norm <= 1e-12 or pat_norm <= 1e-12:
            continue
        corr = float(np.dot(sig_chip, pat_chip) / (sig_norm * pat_norm))
        agreements.append(max(0.0, corr))
    if not agreements:
        return 0.0
    return float(np.mean(agreements))


def _marker_identity_ratio(
    signal_window: np.ndarray,
    marker: np.ndarray,
    alternate_marker: Optional[np.ndarray],
) -> float:
    """Return intended-marker match strength relative to the alternate marker."""
    if alternate_marker is None:
        return 10.0
    sig = np.asarray(signal_window).astype(np.float64, copy=False)
    pat = np.asarray(marker).astype(np.float64, copy=False)
    alt = np.asarray(alternate_marker).astype(np.float64, copy=False)
    if len(sig) < len(pat) or len(pat) != len(alt):
        return 0.0
    sig = sig[:len(pat)]
    sig_norm = float(np.sqrt(np.sum(np.square(sig))))
    pat_norm = float(np.sqrt(np.sum(np.square(pat))))
    alt_norm = float(np.sqrt(np.sum(np.square(alt))))
    if sig_norm <= 1e-12 or pat_norm <= 1e-12 or alt_norm <= 1e-12:
        return 0.0
    intended = abs(float(np.dot(sig, pat) / (sig_norm * pat_norm)))
    alternate = abs(float(np.dot(sig, alt) / (sig_norm * alt_norm)))
    return intended / max(alternate, 1e-12)


def _stretched_marker(marker: np.ndarray, stretch: float) -> np.ndarray:
    pat = np.asarray(marker).astype(np.float32, copy=False)
    if len(pat) == 0:
        return pat
    target_n = max(1, int(round(len(pat) * float(stretch))))
    if target_n == len(pat):
        return pat
    src_x = np.arange(len(pat), dtype=np.float64)
    dst_x = np.linspace(0.0, float(len(pat) - 1), target_n)
    return np.interp(dst_x, src_x, pat).astype(np.float32)


def _marker_template_variants(
    marker: np.ndarray,
    alternate_marker: Optional[np.ndarray],
    bluetooth_headphone_mode: bool,
) -> list[tuple[np.ndarray, Optional[np.ndarray], float]]:
    """
    Return marker templates used for detection.

    Bluetooth devices can introduce small sample-rate or codec clock drift, so
    the recorded timing markers may be slightly time-stretched relative to the
    generated packet. Standard mode keeps the exact template only.
    """
    if not bool(bluetooth_headphone_mode):
        return [(np.asarray(marker), alternate_marker, 1.0)]

    stretch_factors = (1.0, 0.99, 1.01, 0.98, 1.02, 0.965, 1.035, 0.95, 1.05)
    variants: list[tuple[np.ndarray, Optional[np.ndarray], float]] = []
    seen_lengths: set[int] = set()
    for stretch in stretch_factors:
        stretched = _stretched_marker(marker, stretch)
        if len(stretched) in seen_lengths:
            continue
        seen_lengths.add(len(stretched))
        stretched_alt = (
            _stretched_marker(alternate_marker, stretch)
            if alternate_marker is not None
            else None
        )
        variants.append((stretched, stretched_alt, float(stretch)))
    return variants


def _marker_peak_candidates(
    corr: np.ndarray,
    marker_region: np.ndarray,
    marker: np.ndarray,
    alternate_marker: Optional[np.ndarray],
    search_start: int,
    expected_start: int,
    fs: int,
    max_peaks: int = 8,
    marker_stretch: float = 1.0,
) -> list[tuple[int, float, float, float, int, float]]:
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
        agreement = _marker_component_agreement(
            np.asarray(marker_region)[idx:idx + len(marker)],
            marker,
        )
        identity_ratio = _marker_identity_ratio(
            np.asarray(marker_region)[idx:idx + len(marker)],
            marker,
            alternate_marker,
        )
        confidence = float(mag[idx] / max(rms, 1e-12))
        candidates.append(
            (
                int(search_start + idx),
                confidence,
                agreement,
                identity_ratio,
                len(marker),
                float(marker_stretch),
            )
        )
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
    peak_corr = float(abs(corr_valid[start_idx]))
    # Peak-to-background is the gating confidence. Peak-to-next-best is
    # reported only: a valid sweep with a strong secondary arrival (an echo,
    # a reflection, a codec re-sync) scores about 1.0 there, identical to
    # silence, which made the old min(bg, next) value reject good sweeps.
    start_conf = float(conf_bg)
    bluetooth_mode = bool(settings.bluetooth_headphone_mode)
    min_start_conf = float(settings.start_alignment_confidence_min)
    if bluetooth_mode and min_start_conf > 0.0:
        min_start_conf = min(min_start_conf, 3.0)

    start_marker_conf = 0.0
    marker_locked_candidate = None
    if bluetooth_mode and len(layout.start_marker) > 0:
        start_marker_search = int(round(0.35 * layout.fs))
        sm_lo = max(0, start_idx - start_marker_search)
        sm_hi = min(
            len(rec), start_idx + start_marker_search + len(layout.start_marker)
        )
        sm_region = rec[sm_lo:sm_hi]
        start_marker_match = None
        for marker_template, _alternate_template, marker_stretch in _marker_template_variants(
            layout.start_marker,
            None,
            True,
        ):
            sm_corr = normalized_corr_valid(sm_region, marker_template)
            if len(sm_corr) == 0:
                continue
            sm_offset = int(np.argmax(np.abs(sm_corr)))
            candidate_conf = peak_to_rms_confidence(sm_corr)
            if start_marker_match is None or candidate_conf > start_marker_match[0]:
                start_marker_match = (
                    float(candidate_conf),
                    sm_lo + sm_offset,
                    len(marker_template),
                    float(marker_stretch),
                )

        if start_marker_match is not None:
            start_marker_conf, sm_idx, marker_len, marker_stretch = start_marker_match
            if start_marker_conf >= max(3.5, min_start_conf * 0.7):
                marker_locked_start = (
                    sm_idx
                    + marker_len
                    + int(round(layout.start_marker_gap_samples * marker_stretch))
                )
                max_start_idx = len(rec) - sweep_n
                if marker_locked_start <= max_start_idx:
                    start_idx = marker_locked_start
                    marker_locked_candidate = int(marker_locked_start)

    # Hard floor on the correlation coefficient: silence, hum, tones and
    # unrelated signals sit at 0.04 or below, valid sweeps at 0.39 or above.
    # In Bluetooth mode a successful start-marker lock is accepted instead,
    # because codec clock stretch can decorrelate the sweep while the coded
    # marker still locks.
    peak_corr_min = float(settings.peak_correlation_min)
    if (
        peak_corr_min > 0.0
        and peak_corr < peak_corr_min
        and (not bluetooth_mode or marker_locked_candidate is None)
    ):
        _raise_alignment_error(
            f"Low start-alignment confidence (sweep correlation {peak_corr:.3f}; "
            f"minimum {peak_corr_min:.2f}). No sweep signal was detected in the "
            "recording. Check the output device, the input device, the seal, "
            "and the playback level.",
            MeasurementFailureReason.LOW_START_CONFIDENCE,
            layout,
            settings,
            start=StartAlignmentResult(
                selected_sweep_start=int(start_idx),
                sweep_correlation_candidate=int(sweep_start_candidate),
                marker_locked_candidate=marker_locked_candidate,
                start_confidence=float(start_conf),
                start_marker_confidence=float(start_marker_conf),
                background_confidence=float(conf_bg),
                nextbest_confidence=float(conf_next),
                peak_correlation=peak_corr,
            ),
        )

    # In Bluetooth mode a strong start marker is independent evidence that
    # the sweep start is right, so a weak sweep correlation alone does not
    # fail the measurement when the marker lock succeeded. The reported
    # confidence is never inflated to hide that decision.
    if (
        bluetooth_mode
        and min_start_conf > 0.0
        and start_conf < min_start_conf
        and marker_locked_candidate is None
    ):
        start_result = StartAlignmentResult(
            selected_sweep_start=int(start_idx),
            sweep_correlation_candidate=int(sweep_start_candidate),
            marker_locked_candidate=marker_locked_candidate,
            start_confidence=float(start_conf),
            start_marker_confidence=float(start_marker_conf),
            background_confidence=float(conf_bg),
            nextbest_confidence=float(conf_next),
            peak_correlation=peak_corr,
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
        background_confidence=float(conf_bg),
        nextbest_confidence=float(conf_next),
        peak_correlation=peak_corr,
    )


def _standard_mode_end_result(start_result: StartAlignmentResult) -> EndMarkerResult:
    return EndMarkerResult(
        selected_sweep_start=int(start_result.selected_sweep_start),
        marker_1_start=-1,
        marker_2_start=-1,
        marker_confidence=0.0,
        timing_error_samples=0,
        timing_error_ms=0.0,
        spacing_error_samples=0,
    )


def _validate_aligned_sweep_window(
    rec: np.ndarray,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start_result: StartAlignmentResult,
    end_result: EndMarkerResult,
) -> tuple[int, int]:
    start_idx = int(end_result.selected_sweep_start)
    end_idx = start_idx + layout.sweep_samples
    if start_idx < 0 or end_idx > len(rec):
        _raise_alignment_error(
            "Aligned recording shorter than expected. "
            "Please increase post-sweep silence or use Bluetooth mode.",
            MeasurementFailureReason.SHORT_ALIGNED_RECORDING,
            layout,
            settings,
            start=start_result,
            end=end_result if bool(settings.bluetooth_headphone_mode) else None,
        )
    return start_idx, end_idx


def _tail_after_sweep(
    rec: np.ndarray,
    end_idx: int,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
) -> np.ndarray:
    """Return the recorded decay that follows the aligned sweep.

    In standard mode the whole post-sweep silence is available. In Bluetooth
    mode the end markers are played after a shorter gap, so the tail stops at
    the gap: an end-marker packet inside the analysis window would show up as
    a spurious reflection in the impulse response.

    An empty tail is legal — a recording that stops at the sweep simply has no
    decay to offer.
    """
    end_idx = int(end_idx)
    if end_idx < 0 or end_idx >= len(rec):
        return np.zeros(0, dtype=np.float32)
    available = int(layout.post_silence_samples)
    if bool(settings.bluetooth_headphone_mode):
        available = min(int(layout.end_marker_gap_samples), available)
    if available <= 0:
        return np.zeros(0, dtype=np.float32)
    stop = min(end_idx + available, len(rec))
    return np.asarray(rec[end_idx:stop]).astype(np.float32, copy=False)


def _bluetooth_sweep_fallback_result(
    rec: np.ndarray,
    sweep: np.ndarray,
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start_result: StartAlignmentResult,
    marker_failure_reason: str,
    end_result: Optional[EndMarkerResult] = None,
) -> Optional[MeasurementAlignmentResult]:
    if not bool(settings.bluetooth_headphone_mode):
        return None

    fallback_start = int(start_result.sweep_correlation_candidate)
    fallback_start_result = StartAlignmentResult(
        selected_sweep_start=fallback_start,
        sweep_correlation_candidate=int(start_result.sweep_correlation_candidate),
        marker_locked_candidate=start_result.marker_locked_candidate,
        start_confidence=float(start_result.start_confidence),
        start_marker_confidence=float(start_result.start_marker_confidence),
        background_confidence=float(start_result.background_confidence),
        nextbest_confidence=float(start_result.nextbest_confidence),
        peak_correlation=float(start_result.peak_correlation),
    )
    fallback_end_result = end_result
    if fallback_end_result is None:
        fallback_end_result = EndMarkerResult(
            selected_sweep_start=fallback_start,
            marker_1_start=-1,
            marker_2_start=-1,
            marker_confidence=0.0,
            timing_error_samples=0,
            timing_error_ms=0.0,
            spacing_error_samples=0,
        )
    else:
        raw_marker_conf = (
            end_result.raw_marker_confidence
            if end_result.raw_marker_confidence is not None
            else end_result.marker_confidence
        )
        strong_marker_conf_min = min(float(settings.end_marker_confidence_min), 2.5)
        # A reversed marker order means the recording is not what was played;
        # nothing downstream can be trusted. Weak chip agreement or a small
        # identity margin only means the markers are unusable for timing,
        # which is exactly the case the sweep-correlation fallback exists for.
        reversed_marker_order = (
            raw_marker_conf >= 1.0
            and end_result.marker_identity_ratio is not None
            and end_result.marker_identity_ratio < 1.0
        )
        strong_invalid_drift = (
            marker_failure_reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
            and raw_marker_conf >= strong_marker_conf_min
        )
        if reversed_marker_order or strong_invalid_drift:
            return None

        fallback_end_result = EndMarkerResult(
            selected_sweep_start=fallback_start,
            marker_1_start=end_result.marker_1_start,
            marker_2_start=end_result.marker_2_start,
            marker_confidence=end_result.marker_confidence,
            timing_error_samples=end_result.timing_error_samples,
            timing_error_ms=end_result.timing_error_ms,
            spacing_error_samples=end_result.spacing_error_samples,
            raw_marker_confidence=end_result.raw_marker_confidence,
            marker_agreement=end_result.marker_agreement,
            marker_identity_ratio=end_result.marker_identity_ratio,
            marker_template_stretch=end_result.marker_template_stretch,
        )

    start_idx, end_idx = _validate_aligned_sweep_window(
        rec,
        layout,
        settings,
        fallback_start_result,
        fallback_end_result,
    )
    sweep_rec = rec[start_idx:end_idx].astype(np.float32, copy=False)
    sweep_rms = float(np.sqrt(np.mean(np.square(sweep_rec))))
    snr_db = _estimate_bluetooth_fallback_snr_db(
        rec,
        sweep_rec,
        start_idx,
        layout,
    )

    sweep_conf_ok = (
        fallback_start_result.start_confidence
        >= _BLUETOOTH_FALLBACK_MIN_SWEEP_CONFIDENCE
    )
    start_evidence_ok = (
        fallback_start_result.start_confidence
        >= _BLUETOOTH_FALLBACK_STRONG_SWEEP_CONFIDENCE
        or fallback_start_result.marker_locked_candidate is not None
        or fallback_start_result.start_marker_confidence
        >= _BLUETOOTH_FALLBACK_MIN_START_MARKER_CONFIDENCE
    )
    if (
        not sweep_conf_ok
        or not start_evidence_ok
        or snr_db < _BLUETOOTH_FALLBACK_MIN_SNR_DB
        or sweep_rms < _BLUETOOTH_FALLBACK_MIN_SWEEP_RMS
        or fallback_start_result.peak_correlation
        < _BLUETOOTH_FALLBACK_MIN_PEAK_CORRELATION
    ):
        return None

    # The fallback SNR uses marker-aware noise windows; derive the noise floor
    # from it so the integrity metrics stay comparable with the other paths.
    fallback_noise_rms = (
        sweep_rms / (10.0 ** (snr_db / 20.0)) if 0.0 < snr_db < 120.0 else 0.0
    )
    integrity = compute_sweep_integrity(sweep_rec, fallback_noise_rms)
    _enforce_sweep_integrity(
        layout,
        settings,
        fallback_start_result,
        integrity,
        snr_db,
        end_result=fallback_end_result,
    )
    warning_message = (
        "Bluetooth markers were unreliable; sweep correlation was used. "
        "Review repeatability before keeping."
    )
    return MeasurementAlignmentResult(
        aligned_recording=sweep_rec,
        aligned_recording_tail=_tail_after_sweep(rec, end_idx, layout, settings),
        start=fallback_start_result,
        end=fallback_end_result,
        snr_db=float(snr_db),
        diagnostics=_diagnostics_from_results(
            layout=layout,
            settings=settings,
            start=fallback_start_result,
            end=fallback_end_result,
            snr_db=float(snr_db),
            warning_reason=MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK,
            warning_message=warning_message,
            alignment_mode="sweep_fallback",
            marker_failure_reason=marker_failure_reason,
            integrity=integrity,
        ),
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
        int(round(0.22 * layout.fs))
        if bool(settings.bluetooth_headphone_mode)
        else int(round(0.08 * layout.fs))
    )
    end_conf_min = float(settings.end_marker_confidence_min)
    if bool(settings.bluetooth_headphone_mode):
        end_conf_min = min(end_conf_min, 2.5)

    pair_results = []
    marker_1 = layout.end_marker
    marker_2 = getattr(layout, "end_marker_2", layout.end_marker)
    marker_1_variants = _marker_template_variants(
        marker_1,
        marker_2,
        bool(settings.bluetooth_headphone_mode),
    )
    marker_2_variants = _marker_template_variants(
        marker_2,
        marker_1,
        bool(settings.bluetooth_headphone_mode),
    )
    max_reasonable_spacing_error = int(round(960.0 * float(layout.fs) / 48000.0))
    for cand in candidates:
        expected_marker_1 = cand + sweep_n + layout.end_marker_gap_samples
        expected_marker_2 = (
            expected_marker_1 + len(marker_1) + layout.end_marker_pair_gap_samples
        )

        search_start_1 = max(0, expected_marker_1 - marker_search)
        search_stop_1 = min(len(rec), expected_marker_1 + marker_search + len(marker_1))
        marker_region_1 = rec[search_start_1:search_stop_1]
        peaks_1 = []
        for marker_template, alternate_template, marker_stretch in marker_1_variants:
            marker_corr_1 = normalized_corr_valid(marker_region_1, marker_template)
            if len(marker_corr_1) == 0:
                continue
            peaks_1.extend(
                _marker_peak_candidates(
                    marker_corr_1,
                    marker_region_1,
                    marker_template,
                    alternate_template,
                    search_start_1,
                    expected_marker_1,
                    layout.fs,
                    marker_stretch=marker_stretch,
                )
            )
        if not peaks_1:
            continue

        search_start_2 = max(0, expected_marker_2 - marker_search)
        search_stop_2 = min(len(rec), expected_marker_2 + marker_search + len(marker_2))
        marker_region_2 = rec[search_start_2:search_stop_2]
        peaks_2 = []
        for marker_template, alternate_template, marker_stretch in marker_2_variants:
            marker_corr_2 = normalized_corr_valid(marker_region_2, marker_template)
            peaks_2.extend(
                _marker_peak_candidates(
                    marker_corr_2,
                    marker_region_2,
                    marker_template,
                    alternate_template,
                    search_start_2,
                    expected_marker_2,
                    layout.fs,
                    marker_stretch=marker_stretch,
                )
            )

        penalty_unit = max(1.0, 0.01 * layout.fs)
        for (
            marker_start_1,
            marker_conf_1,
            agreement_1,
            identity_1,
            marker_len_1,
            stretch_1,
        ) in peaks_1:
            for (
                marker_start_2,
                marker_conf_2,
                agreement_2,
                identity_2,
                _marker_len_2,
                stretch_2,
            ) in peaks_2:
                spacing_stretch = 0.5 * (stretch_1 + stretch_2)
                spacing_expected = marker_len_1 + int(
                    round(layout.end_marker_pair_gap_samples * spacing_stretch)
                )
                spacing_observed = marker_start_2 - marker_start_1
                if spacing_observed <= 0:
                    continue

                timing_err_1 = abs(marker_start_1 - expected_marker_1)
                timing_err_2 = abs(marker_start_2 - expected_marker_2)
                timing_err = max(timing_err_1, timing_err_2)
                spacing_err = abs(spacing_observed - spacing_expected)
                marker_conf = min(marker_conf_1, marker_conf_2)
                agreement = min(agreement_1, agreement_2)
                identity_ratio = min(identity_1, identity_2)
                identity_penalty = max(0.0, 1.2 - identity_ratio) * 8.0
                score = marker_conf - (timing_err / penalty_unit) - (
                    spacing_err / penalty_unit
                ) + (2.0 * agreement) - identity_penalty
                result = (
                    score,
                    cand,
                    marker_start_1,
                    marker_start_2,
                    timing_err,
                    marker_conf,
                    spacing_err,
                    agreement,
                    identity_ratio,
                    stretch_1,
                    stretch_2,
                )
                pair_results.append(result)

    if not pair_results:
        _raise_alignment_error(
            "Unable to verify end marker timing.",
            MeasurementFailureReason.END_MARKER_UNVERIFIED,
            layout,
            settings,
            start=start_result,
        )

    best_result = max(pair_results, key=lambda result: result[0])
    viable_results = [
        result for result in pair_results
        if (
            result[5] >= max(1.8, end_conf_min * 0.85)
            and result[7] >= _MARKER_MIN_AGREEMENT
            and result[8] >= _MARKER_MIN_IDENTITY_RATIO
        )
    ]
    best_err_result = None
    if viable_results:
        max_viable_conf = max(result[5] for result in viable_results)
        strong_results = [
            result for result in viable_results
            if result[5] >= max(1.8, 0.60 * max_viable_conf)
        ]
        best_err_result = min(
            strong_results,
            key=lambda result: (
                result[6] > max_reasonable_spacing_error,
                result[4],
                result[6],
                -result[5],
            ),
        )

    chosen = best_err_result if best_err_result is not None else best_result
    (
        _score,
        start_idx,
        marker_start_1,
        marker_start_2,
        timing_err,
        marker_conf,
        spacing_err,
        agreement,
        identity_ratio,
        stretch_1,
        stretch_2,
    ) = chosen
    raw_marker_conf = marker_conf
    if agreement < _MARKER_MIN_AGREEMENT or identity_ratio < _MARKER_MIN_IDENTITY_RATIO:
        # The pair is not trusted for timing. Zeroing the confidence routes
        # the caller to the sweep-correlation fallback; the raw value stays
        # in diagnostics.
        marker_conf = 0.0
    drift_ms = 1000.0 * timing_err / float(layout.fs)
    marker_template_stretch = 0.5 * (float(stretch_1) + float(stretch_2))
    return EndMarkerResult(
        selected_sweep_start=int(start_idx),
        marker_1_start=int(marker_start_1),
        marker_2_start=int(marker_start_2),
        marker_confidence=float(marker_conf),
        timing_error_samples=int(timing_err),
        timing_error_ms=float(drift_ms),
        spacing_error_samples=int(spacing_err),
        raw_marker_confidence=float(raw_marker_conf),
        marker_agreement=float(agreement),
        marker_identity_ratio=float(identity_ratio),
        marker_template_stretch=float(marker_template_stretch),
    )


def _rms(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(values, dtype=np.float64)))))


def _ratio_db(signal_rms: float, noise_rms: float) -> float:
    if noise_rms > 1e-12 and signal_rms > 0.0:
        return float(20.0 * np.log10(signal_rms / noise_rms))
    if signal_rms > 0.0:
        return 120.0
    return 0.0


def noise_floor_rms(
    rec_mono: np.ndarray,
    post_noise_start: int,
    start_idx: int,
    fs: int,
) -> float:
    """RMS of the silence just before the sweep and just after it."""
    noise_win_n = int(round(0.12 * fs))
    rec = np.asarray(rec_mono)
    pre_noise = rec[max(0, start_idx - noise_win_n):start_idx]
    post_noise = rec[
        post_noise_start:min(len(rec), post_noise_start + noise_win_n)
    ]
    noise_parts = [seg for seg in (pre_noise, post_noise) if len(seg) > 8]
    if not noise_parts:
        return 0.0
    return _rms(np.concatenate(noise_parts))


def estimate_snr_db(
    rec_mono: np.ndarray,
    aligned_recording: np.ndarray,
    post_noise_start: int,
    start_idx: int,
    fs: int,
) -> float:
    noise_rms = noise_floor_rms(rec_mono, post_noise_start, start_idx, fs)
    return _ratio_db(_rms(aligned_recording), noise_rms)


def compute_sweep_integrity(
    aligned_recording: np.ndarray,
    noise_rms: float,
    *,
    segments: int = 10,
) -> SweepIntegrity:
    """
    Level evidence that the aligned window holds the played sweep.

    ``midband_margin_db`` is the RMS of the middle 60 % of the aligned sweep
    relative to the noise floor. For a 20 Hz to 20 kHz log sweep that span
    covers roughly 63 Hz to 6.3 kHz, so headphones with little output at the
    band edges still score well. ``coverage_db`` reports the same ratio for
    each tenth of the sweep so a rolled-off but valid sweep reads as such in
    diagnostics.
    """
    aligned = np.asarray(aligned_recording, dtype=np.float64)
    n = len(aligned)
    if n == 0:
        return SweepIntegrity(
            noise_rms=float(noise_rms),
            signal_rms=0.0,
            midband_margin_db=0.0,
            coverage_db=tuple(0.0 for _ in range(segments)),
        )
    lo = int(round(0.2 * n))
    hi = max(lo + 1, int(round(0.8 * n)))
    midband_rms = _rms(aligned[lo:hi])
    edges = np.linspace(0, n, segments + 1, dtype=int)
    coverage = tuple(
        _ratio_db(_rms(aligned[edges[i]:edges[i + 1]]), noise_rms)
        if edges[i + 1] > edges[i]
        else 0.0
        for i in range(segments)
    )
    return SweepIntegrity(
        noise_rms=float(noise_rms),
        signal_rms=_rms(aligned),
        midband_margin_db=_ratio_db(midband_rms, noise_rms),
        coverage_db=coverage,
    )


def _enforce_sweep_integrity(
    layout: MeasurementSignalLayout,
    settings: AlignmentSettings,
    start_result: StartAlignmentResult,
    integrity: SweepIntegrity,
    snr_db: float,
    end_result: Optional[EndMarkerResult] = None,
) -> None:
    """
    Reject a recording only when both the alignment confidence and the
    mid-band level margin are below their minimums.

    Either metric alone has valid-measurement counterexamples: a sweep with a
    strong echo can score low on confidence while sitting 40 dB above the
    noise, and a very quiet but correct sweep can align with high confidence
    while sitting only 4 dB above the noise. Silence, hum, tones and unrelated
    signals fail both. A threshold of 0 turns that half of the check off, and
    the check needs both halves.
    """
    conf_min = float(settings.start_alignment_confidence_min)
    margin_min = float(settings.sweep_noise_margin_min_db)
    if conf_min <= 0.0 or margin_min <= 0.0:
        return
    conf = float(start_result.background_confidence)
    margin = float(integrity.midband_margin_db)
    if conf < conf_min and margin < margin_min:
        _raise_alignment_error(
            f"No usable sweep signal (alignment confidence {conf:.1f}, minimum "
            f"{conf_min:.1f}; mid-band level {margin:.1f} dB above noise, minimum "
            f"{margin_min:.1f} dB). Check the seal, the playback level, and the "
            "input device, then retry.",
            MeasurementFailureReason.LOW_SNR,
            layout,
            settings,
            start=start_result,
            end=end_result,
            snr_db=snr_db,
            integrity=integrity,
        )


def _low_snr_warning(
    settings: AlignmentSettings,
    snr_db: float,
) -> tuple[Optional[str], Optional[str]]:
    warn_db = float(settings.snr_warn_db)
    if warn_db > 0.0 and snr_db < warn_db:
        return (
            MeasurementWarningReason.LOW_SNR,
            f"Low signal-to-noise ratio ({snr_db:.1f} dB; warning below "
            f"{warn_db:.1f} dB). Check the seal, the playback level, and ambient "
            "noise before keeping this measurement.",
        )
    return None, None


def _estimate_bluetooth_fallback_snr_db(
    rec_mono: np.ndarray,
    aligned_recording: np.ndarray,
    start_idx: int,
    layout: MeasurementSignalLayout,
) -> float:
    noise_win_n = int(round(0.12 * layout.fs))
    rec = np.asarray(rec_mono)
    pre_noise_stop = max(
        0,
        int(start_idx)
        - int(layout.start_marker_gap_samples)
        - len(layout.start_marker),
    )
    pre_noise = rec[max(0, pre_noise_stop - noise_win_n):pre_noise_stop]
    post_noise_start = (
        int(start_idx)
        + layout.sweep_samples
        + layout.end_marker_gap_samples
        + len(layout.end_marker)
        + layout.end_marker_pair_gap_samples
        + len(getattr(layout, "end_marker_2", layout.end_marker))
    )
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
    if not np.all(np.isfinite(rec)):
        _raise_alignment_error(
            "Recording contains invalid samples. Check the input device and retry.",
            MeasurementFailureReason.INVALID_RECORDING,
            layout,
            settings,
        )

    start_result = find_start_alignment(rec, sweep, layout, settings)
    bluetooth_mode = bool(settings.bluetooth_headphone_mode)
    if not bluetooth_mode:
        end_result = _standard_mode_end_result(start_result)
        start_idx, end_idx = _validate_aligned_sweep_window(
            rec, layout, settings, start_result, end_result
        )
        sweep_rec = rec[start_idx:end_idx].astype(np.float32, copy=False)
        noise_rms = noise_floor_rms(rec, end_idx, start_idx, layout.fs)
        snr_db = _ratio_db(_rms(sweep_rec), noise_rms)
        integrity = compute_sweep_integrity(sweep_rec, noise_rms)
        _enforce_sweep_integrity(
            layout, settings, start_result, integrity, snr_db
        )
        warning_reason, warning_message = _low_snr_warning(settings, snr_db)
        return MeasurementAlignmentResult(
            aligned_recording=sweep_rec,
            aligned_recording_tail=_tail_after_sweep(
                rec, end_idx, layout, settings
            ),
            start=start_result,
            end=end_result,
            snr_db=float(snr_db),
            diagnostics=_diagnostics_from_results(
                layout=layout,
                settings=settings,
                start=start_result,
                end=None,
                snr_db=float(snr_db),
                warning_reason=warning_reason,
                warning_message=warning_message,
                integrity=integrity,
            ),
        )

    try:
        end_result = find_end_markers(rec, layout, settings, start_result)
    except MeasurementAlignmentError as exc:
        fallback = _bluetooth_sweep_fallback_result(
            rec=rec,
            sweep=sweep,
            layout=layout,
            settings=settings,
            start_result=start_result,
            marker_failure_reason=exc.reason,
        )
        if fallback is not None:
            return fallback
        raise

    start_idx, end_idx = _validate_aligned_sweep_window(
        rec, layout, settings, start_result, end_result
    )
    sweep_rec = rec[start_idx:end_idx].astype(np.float32, copy=False)
    # Level evidence is computed before any marker decision so that every
    # failure below carries SNR and integrity metrics in its diagnostics.
    noise_rms = noise_floor_rms(
        rec,
        end_result.marker_2_start + len(getattr(layout, "end_marker_2", layout.end_marker)),
        end_result.selected_sweep_start,
        layout.fs,
    )
    snr_db = _ratio_db(_rms(sweep_rec), noise_rms)
    integrity = compute_sweep_integrity(sweep_rec, noise_rms)

    end_conf_min = float(settings.end_marker_confidence_min)
    if bool(settings.bluetooth_headphone_mode):
        end_conf_min = min(end_conf_min, 2.5)
    marker_conf = end_result.marker_confidence
    max_spacing_error_samples = int(round(960.0 * float(layout.fs) / 48000.0))
    max_drift_samples = int(
        round((float(settings.timing_drift_max_ms) / 1000.0) * layout.fs)
    )
    marginal_ceiling_samples = int(round(0.160 * layout.fs))
    bluetooth_marginal_floor = 2.0
    bluetooth_start_evidence_ok = (
        start_result.start_confidence >= 3.0
        and (
            start_result.marker_locked_candidate is not None
            or start_result.start_marker_confidence >= 3.5
        )
    )
    can_accept_bluetooth_marginal = (
        bluetooth_mode
        and end_result.timing_error_samples <= marginal_ceiling_samples
        and bluetooth_start_evidence_ok
        and end_result.spacing_error_samples <= max_spacing_error_samples
        and marker_conf >= bluetooth_marginal_floor
        and (
            end_result.timing_error_samples > max_drift_samples
            or marker_conf < end_conf_min
        )
    )
    if marker_conf < end_conf_min and not can_accept_bluetooth_marginal:
        fallback = _bluetooth_sweep_fallback_result(
            rec=rec,
            sweep=sweep,
            layout=layout,
            settings=settings,
            start_result=start_result,
            marker_failure_reason=MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
            end_result=end_result,
        )
        if fallback is not None:
            return fallback
        _raise_alignment_error(
            f"Low end-marker confidence ({marker_conf:.1f}). "
            "Timing reliability is low; retrying is recommended.",
            MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
            layout,
            settings,
            start=start_result,
            end=end_result,
            snr_db=snr_db,
            integrity=integrity,
        )

    if (
        bluetooth_mode
        and end_result.spacing_error_samples > max_spacing_error_samples
    ):
        ms = 1000.0 * end_result.timing_error_samples / float(layout.fs)
        fallback = _bluetooth_sweep_fallback_result(
            rec=rec,
            sweep=sweep,
            layout=layout,
            settings=settings,
            start_result=start_result,
            marker_failure_reason=MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
            end_result=end_result,
        )
        if fallback is not None:
            return fallback
        _raise_alignment_error(
            f"Timing drift too large ({ms:.1f} ms). "
            "Please retry; Bluetooth marker spacing was unstable.",
            MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
            layout,
            settings,
            start=start_result,
            end=end_result,
            snr_db=snr_db,
            integrity=integrity,
        )

    warning_reason = None
    warning_message = None
    if can_accept_bluetooth_marginal:
        ms = 1000.0 * end_result.timing_error_samples / float(layout.fs)
        warning_reason = MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
        warning_message = (
            f"Bluetooth timing drift is marginal (drift {ms:.1f} ms, "
            f"end confidence {marker_conf:.1f}). "
            "Review repeatability before keeping this measurement."
        )
    elif end_result.timing_error_samples > max_drift_samples:
        ms = 1000.0 * end_result.timing_error_samples / float(layout.fs)
        if bluetooth_mode:
            fallback = _bluetooth_sweep_fallback_result(
                rec=rec,
                sweep=sweep,
                layout=layout,
                settings=settings,
                start_result=start_result,
                marker_failure_reason=MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
                end_result=end_result,
            )
            if fallback is not None:
                return fallback
            hint = "Please retry; Bluetooth timing jitter exceeded the current tolerance."
            _raise_alignment_error(
                f"Timing drift too large ({ms:.1f} ms). {hint}",
                MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
                layout,
                settings,
                start=start_result,
                end=end_result,
                snr_db=snr_db,
                integrity=integrity,
            )
        else:
            hint = "Please retry and consider high latency mode."
            _raise_alignment_error(
                f"Timing drift too large ({ms:.1f} ms). {hint}",
                MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
                layout,
                settings,
                start=start_result,
                end=end_result,
                snr_db=snr_db,
                integrity=integrity,
            )

    if bluetooth_mode:
        if warning_reason is None:
            marker_conf = max(marker_conf, end_conf_min)
        end_result = EndMarkerResult(
            selected_sweep_start=end_result.selected_sweep_start,
            marker_1_start=end_result.marker_1_start,
            marker_2_start=end_result.marker_2_start,
            marker_confidence=float(marker_conf),
            timing_error_samples=end_result.timing_error_samples,
            timing_error_ms=end_result.timing_error_ms,
            spacing_error_samples=end_result.spacing_error_samples,
            raw_marker_confidence=end_result.raw_marker_confidence,
            marker_agreement=end_result.marker_agreement,
            marker_identity_ratio=end_result.marker_identity_ratio,
            marker_template_stretch=end_result.marker_template_stretch,
        )

    _enforce_sweep_integrity(
        layout, settings, start_result, integrity, snr_db, end_result=end_result
    )
    if warning_reason is None:
        warning_reason, warning_message = _low_snr_warning(settings, snr_db)
    return MeasurementAlignmentResult(
        aligned_recording=sweep_rec,
        aligned_recording_tail=_tail_after_sweep(rec, end_idx, layout, settings),
        start=start_result,
        end=end_result,
        snr_db=float(snr_db),
        diagnostics=_diagnostics_from_results(
            layout=layout,
            settings=settings,
            start=start_result,
            end=end_result,
            snr_db=float(snr_db),
            warning_reason=warning_reason,
            warning_message=warning_message,
            integrity=integrity,
        ),
    )
