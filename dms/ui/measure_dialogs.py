"""Dialogs the Measure and R&D workspaces open from the main window."""

from collections.abc import Callable

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
)

from dms.measurement_alignment import MeasurementWarningReason, format_diagnostics_summary
from dms.processing import HarmonicAnalysis
from dms.recovery import RecoveryCandidate
from dms.ui.modern_button import ModernButton as QPushButton

#: Band the pass/fail summary reports THD over.
_DISTORTION_SUMMARY_F_MIN = 100.0
_DISTORTION_SUMMARY_F_MAX = 10000.0


class RnDRecoveryDialog(QDialog):
    """Select and act on a recoverable R&D session."""

    RESTORE = "restore"
    DISCARD = "discard"
    KEEP = "keep"

    def __init__(self, candidates: list[RecoveryCandidate], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover R&D Session")
        self.setModal(True)
        self.action = self.KEEP
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Fastgraph found R&D session data that was not cleared during a normal exit.")
        )
        self._candidate_combo = QComboBox()
        for candidate in candidates:
            self._candidate_combo.addItem(candidate.label, candidate)
        layout.addWidget(self._candidate_combo)

        row = QHBoxLayout()
        restore = QPushButton("Restore Session")
        restore.clicked.connect(lambda: self._finish(self.RESTORE))
        discard = QPushButton("Discard")
        discard.clicked.connect(lambda: self._finish(self.DISCARD))
        keep = QPushButton("Keep for Later")
        keep.clicked.connect(lambda: self._finish(self.KEEP))
        row.addWidget(restore)
        row.addWidget(discard)
        row.addStretch(1)
        row.addWidget(keep)
        layout.addLayout(row)

    def selected_candidate(self) -> RecoveryCandidate:
        return self._candidate_combo.currentData()

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()


class MeasureRecoveryDialog(QDialog):
    """Select and act on a recoverable Measure session.

    Deliberately the R&D dialog's twin: the two workspaces recover the same
    way, so anything learned in one reads the same in the other.
    """

    RESTORE = "restore"
    DISCARD = "discard"
    KEEP = "keep"

    def __init__(
        self,
        candidates: list[RecoveryCandidate],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover Measure Session")
        self.setModal(True)
        self.action = self.KEEP
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Fastgraph found Measure session data that was not cleared during a normal exit."
            )
        )
        self._candidate_combo = QComboBox()
        for candidate in candidates:
            self._candidate_combo.addItem(candidate.label, candidate)
        layout.addWidget(self._candidate_combo)

        row = QHBoxLayout()
        restore = QPushButton("Restore Session")
        restore.clicked.connect(lambda: self._finish(self.RESTORE))
        discard = QPushButton("Discard")
        discard.clicked.connect(lambda: self._finish(self.DISCARD))
        keep = QPushButton("Keep for Later")
        keep.clicked.connect(lambda: self._finish(self.KEEP))
        row.addWidget(restore)
        row.addWidget(discard)
        row.addStretch(1)
        row.addWidget(keep)
        layout.addLayout(row)

    def selected_candidate(self) -> RecoveryCandidate:
        return self._candidate_combo.currentData()

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()


class TestLevelDialog(QDialog):
    def __init__(
        self,
        snapshot_fn: Callable[[], tuple[float, float | None, str]],
        play_noise_fn: Callable[[], str | None] | None = None,
        calibrated: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._snapshot_fn = snapshot_fn
        self._play_noise_fn = play_noise_fn
        self._calibrated = calibrated
        self.setWindowTitle("DMS fastgraph — Test Level")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Live input test level for the currently selected input channel.\n"
            "If calibrated, SPL is shown. Otherwise, use dBFS + noise ping to verify routing."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._device_label = QLabel("Device: —")
        layout.addWidget(self._device_label)

        self._dbfs_label = QLabel("— dBFS")
        self._dbfs_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dbfs_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(self._dbfs_label)

        self._spl_label = QLabel("— dB SPL")
        self._spl_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spl_label.setStyleSheet("font-size: 30px; font-weight: bold;")
        layout.addWidget(self._spl_label)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setProperty("tone", "muted")
        if self._calibrated:
            self._hint_label.setText("SPL is calibrated for this input device.")
        else:
            self._hint_label.setText(
                "This device is not SPL-calibrated yet. dB SPL is unavailable; "
                "use dBFS changes to confirm signal."
            )
        layout.addWidget(self._hint_label)

        if self._play_noise_fn is not None:
            self._noise_btn = QPushButton("Play Noise Ping")
            self._noise_btn.clicked.connect(self._play_noise_ping)
            layout.addWidget(self._noise_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_values)
        self._timer.start(100)
        self._update_values()

    def _update_values(self) -> None:
        dbfs, spl, device_name = self._snapshot_fn()
        self._device_label.setText(f"Device: {device_name or '—'}")
        self._dbfs_label.setText(f"{dbfs:.1f} dBFS")
        if spl is None:
            self._spl_label.setText("— dB SPL")
        else:
            self._spl_label.setText(f"{spl:.1f} dB SPL")

    def _play_noise_ping(self) -> None:
        if self._play_noise_fn is None:
            return
        err = self._play_noise_fn()
        if err:
            self._hint_label.setText(err)
        elif self._calibrated:
            self._hint_label.setText("Noise ping sent. Confirm input response and SPL stability.")
        else:
            self._hint_label.setText("Noise ping sent. Confirm input level responds in dBFS.")


def _thd_band_summary(
    analysis: HarmonicAnalysis | None,
) -> tuple[float, float, float] | None:
    """(median THD %, max THD %, frequency of the max) over 100 Hz - 10 kHz.

    Returns None when there is nothing measurable in the band — the summary
    line is then simply omitted rather than showing NaNs.
    """
    if analysis is None:
        return None
    freqs = np.asarray(getattr(analysis, "freqs", []), dtype=float)
    percent = np.asarray(getattr(analysis, "thd_percent", []), dtype=float)
    if freqs.size == 0 or percent.size != freqs.size:
        return None
    band = (
        (freqs >= _DISTORTION_SUMMARY_F_MIN)
        & (freqs <= _DISTORTION_SUMMARY_F_MAX)
        & np.isfinite(percent)
    )
    if not np.any(band):
        return None
    values = percent[band]
    band_freqs = freqs[band]
    peak = int(np.argmax(values))
    return float(np.median(values)), float(values[peak]), float(band_freqs[peak])


class PassFailDialog(QDialog):
    KEEP = "keep"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(
        self,
        index: int,
        total: int,
        timing_quality: tuple[float, float, float, float] | None = None,
        diagnostics: object | None = None,
        distortion: HarmonicAnalysis | None = None,
        deviation_summary: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._choice = self.CANCEL
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("Review Measurement")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(360)
        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        summary = QLabel(f"Review measurement {index} of {total} and choose whether to keep it.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        detail = QLabel("The latest sweep is shown in teal in the top plot while you decide.")
        detail.setWordWrap(True)
        detail.setProperty("tone", "muted")
        layout.addWidget(detail)

        self._add_timing_box(layout, timing_quality, diagnostics, distortion)

        self._add_deviation_box(layout, deviation_summary)

        if diagnostics is not None:
            details_toggle = QToolButton()
            details_toggle.setText("Measurement Diagnostics")
            details_toggle.setCheckable(True)
            details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            layout.addWidget(details_toggle)

            details = QLabel(format_diagnostics_summary(diagnostics))
            details.setObjectName("diagnostic_details")
            details.setWordWrap(True)
            details.setVisible(False)
            layout.addWidget(details)
            details_toggle.toggled.connect(details.setVisible)
            details_toggle.toggled.connect(lambda _checked: self.adjustSize())

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        keep_btn = QPushButton("Keep")
        keep_btn.setDefault(True)
        keep_btn.setObjectName("btn_keep")
        keep_btn.clicked.connect(self._accept_keep)
        button_row.addWidget(keep_btn)

        fail_btn = QPushButton("Fail / Redo")
        fail_btn.setObjectName("btn_fail")
        fail_btn.clicked.connect(self._accept_fail)
        button_row.addWidget(fail_btn)

        cancel_btn = QPushButton("Cancel Queue")
        cancel_btn.setRole("warning")
        cancel_btn.clicked.connect(self._accept_cancel)
        button_row.addWidget(cancel_btn)

        layout.addLayout(button_row)
        fail_shortcut = QShortcut(QKeySequence("F"), self)
        fail_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        fail_shortcut.activated.connect(self._accept_fail)
        self.adjustSize()

    def _add_timing_box(
        self,
        layout: QVBoxLayout,
        timing_quality: tuple[float, float, float, float] | None,
        diagnostics: object | None,
        distortion: HarmonicAnalysis | None,
    ) -> None:
        if timing_quality is not None:
            start_conf, end_conf, drift_ms, snr_db = timing_quality
            bluetooth_mode = bool(getattr(diagnostics, "bluetooth_headphone_mode", False))
            warning_message = (
                getattr(diagnostics, "warning_message", None) if diagnostics is not None else None
            )
            timing_box = QFrame()
            timing_box.setObjectName("diagnostic_box")
            timing_box_layout = QVBoxLayout(timing_box)
            timing_box_layout.setContentsMargins(10, 8, 10, 8)
            timing_box_layout.setSpacing(4)
            if bluetooth_mode:
                quality_text = (
                    f"Timing Quality - start: {start_conf:.1f}, "
                    f"end: {end_conf:.1f}, drift: {drift_ms:.1f} ms, "
                    f"SNR: {snr_db:.1f} dB"
                )
            else:
                quality_text = f"Sweep Quality - alignment: {start_conf:.1f}, SNR: {snr_db:.1f} dB"
            timing = QLabel(quality_text)
            timing.setWordWrap(True)
            timing.setProperty("tone", "muted")
            timing_box_layout.addWidget(timing)
            if warning_message:
                warning_reason = getattr(diagnostics, "warning_reason", None)
                warning_title = (
                    "Signal warning"
                    if warning_reason == MeasurementWarningReason.LOW_SNR
                    else "Bluetooth timing marginal"
                )
                warning = QLabel(f"{warning_title} - {warning_message}")
                warning.setWordWrap(True)
                warning.setProperty("tone", "warning")
                timing_box_layout.addWidget(warning)
            summary = _thd_band_summary(distortion)
            if summary is not None:
                median_pct, max_pct, max_freq = summary
                thd_label = QLabel(
                    f"THD 100 Hz-10 kHz: {median_pct:.2f} % "
                    f"(max {max_pct:.2f} % @ {max_freq:.0f} Hz)"
                )
                thd_label.setWordWrap(True)
                thd_label.setProperty("tone", "muted")
                timing_box_layout.addWidget(thd_label)
            if bluetooth_mode:
                timing_box.setToolTip(
                    "Timing quality guide:\n"
                    "Start confidence: higher is better.\n"
                    "End confidence: higher is better.\n"
                    "Drift (ms): lower is better.\n\n"
                    "SNR (dB): higher is better.\n\n"
                    "Confidence guide (rough):\n"
                    ">= 12 strong, 9-12 good, 7-9 borderline, < 7 weak.\n\n"
                    "Drift guide:\n"
                    "< 5 ms excellent\n"
                    "5-15 ms good\n"
                    "15-35 ms acceptable\n"
                    "> 35 ms may hurt repeatability.\n\n"
                    "SNR guide:\n"
                    ">= 35 dB excellent\n"
                    "25-35 dB good\n"
                    "15-25 dB usable\n"
                    "< 15 dB noisy."
                )
            else:
                timing_box.setToolTip(
                    "Sweep quality guide:\n"
                    "Alignment confidence: higher is better.\n"
                    "SNR (dB): higher is better.\n\n"
                    "SNR guide:\n"
                    ">= 35 dB excellent\n"
                    "25-35 dB good\n"
                    "15-25 dB usable\n"
                    "< 15 dB noisy."
                )
            layout.addWidget(timing_box)

    def _add_deviation_box(self, layout: QVBoxLayout, deviation_summary: str | None) -> None:
        if deviation_summary:
            # How far this one sweep sits from the loaded target. Muted and
            # monospaced: it is context for the decision, not the decision.
            deviation_box = QFrame()
            deviation_box.setObjectName("diagnostic_box")
            deviation_layout = QVBoxLayout(deviation_box)
            deviation_layout.setContentsMargins(10, 8, 10, 8)
            deviation_layout.setSpacing(4)
            heading = QLabel("Deviation from target")
            heading.setProperty("tone", "muted")
            deviation_layout.addWidget(heading)
            deviation_label = QLabel(deviation_summary)
            deviation_label.setObjectName("diagnostic_details")
            deviation_label.setProperty("tone", "muted")
            deviation_label.setTextFormat(Qt.TextFormat.PlainText)
            deviation_layout.addWidget(deviation_label)
            layout.addWidget(deviation_box)

    def choice(self) -> str:
        return self._choice

    def _accept_keep(self) -> None:
        self._choice = self.KEEP
        self.accept()

    def _accept_fail(self) -> None:
        self._choice = self.FAIL
        self.accept()

    def _accept_cancel(self) -> None:
        self._choice = self.CANCEL
        self.accept()


class RnDReviewDialog(QDialog):
    KEEP_NO_CHANGE = "keep_no_change"
    KEEP_CHANGE = "keep_change"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(
        self,
        previous_name: str,
        timing_quality: tuple[float, float, float, float] | None = None,
        diagnostics: object | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._choice = self.CANCEL
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("Review R&D Measurement")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        summary = QLabel("Review the latest R&D sweep before adding it to the session.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        previous = QLabel(
            f"Previous kept measurement: {previous_name}"
            if previous_name
            else "Previous kept measurement: none"
        )
        previous.setProperty("tone", "muted")
        previous.setWordWrap(True)
        layout.addWidget(previous)

        if timing_quality is not None:
            start_conf, end_conf, drift_ms, snr_db = timing_quality
            bluetooth_mode = bool(getattr(diagnostics, "bluetooth_headphone_mode", False))
            if bluetooth_mode:
                quality_text = (
                    f"Timing Quality - start: {start_conf:.1f}, end: {end_conf:.1f}, "
                    f"drift: {drift_ms:.1f} ms, SNR: {snr_db:.1f} dB"
                )
            else:
                quality_text = f"Sweep Quality - alignment: {start_conf:.1f}, SNR: {snr_db:.1f} dB"
            timing = QLabel(quality_text)
            timing.setProperty("tone", "muted")
            timing.setWordWrap(True)
            layout.addWidget(timing)

        if diagnostics is not None:
            details_toggle = QToolButton()
            details_toggle.setText("Measurement Diagnostics")
            details_toggle.setCheckable(True)
            details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            layout.addWidget(details_toggle)
            details = QLabel(format_diagnostics_summary(diagnostics))
            details.setObjectName("diagnostic_details")
            details.setWordWrap(True)
            details.setVisible(False)
            layout.addWidget(details)
            details_toggle.toggled.connect(details.setVisible)
            details_toggle.toggled.connect(lambda _checked: self.adjustSize())

        self._notes = QTextEdit()
        self._notes.setPlaceholderText(
            "Change notes, design/sample details, pads, EQ, fixture notes..."
        )
        self._notes.setMaximumHeight(96)
        layout.addWidget(self._notes)

        button_row = QHBoxLayout()
        no_change_btn = QPushButton("Keep: No Change")
        no_change_btn.setDefault(True)
        no_change_btn.setObjectName("btn_keep")
        no_change_btn.clicked.connect(self._accept_no_change)
        button_row.addWidget(no_change_btn)

        change_btn = QPushButton("Keep: Change...")
        change_btn.setObjectName("btn_keep")
        change_btn.clicked.connect(self._accept_change)
        button_row.addWidget(change_btn)

        fail_btn = QPushButton("Fail / Redo")
        fail_btn.setObjectName("btn_fail")
        fail_btn.clicked.connect(self._accept_fail)
        button_row.addWidget(fail_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._accept_cancel)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)
        fail_shortcut = QShortcut(QKeySequence("F"), self)
        fail_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        fail_shortcut.activated.connect(self._accept_fail)

    def choice(self) -> str:
        return self._choice

    def notes(self) -> str:
        return self._notes.toPlainText().strip()

    def _accept_no_change(self) -> None:
        self._choice = self.KEEP_NO_CHANGE
        self.accept()

    def _accept_change(self) -> None:
        self._choice = self.KEEP_CHANGE
        self.accept()

    def _accept_fail(self) -> None:
        self._choice = self.FAIL
        self.accept()

    def _accept_cancel(self) -> None:
        self._choice = self.CANCEL
        self.accept()


class SquiglinkAuthDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_username: str = "",
        initial_password: str = "",
        remember: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload to Squiglink")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter your Squiglink SFTP credentials."))

        layout.addWidget(QLabel("Username"))
        self._username = QLineEdit()
        self._username.setText(initial_username)
        layout.addWidget(self._username)

        layout.addWidget(QLabel("Password"))
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setText(initial_password)
        layout.addWidget(self._password)

        self._remember = QCheckBox("Remember credentials on this device")
        self._remember.setChecked(remember)
        layout.addWidget(self._remember)

        layout.addWidget(QLabel("Name Modifier"))
        layout.addWidget(
            QLabel("Optional. Type here if you're using different tips, pads, EQ modes, etc")
        )
        self._name_modifier = QLineEdit()
        self._name_modifier.setPlaceholderText("")
        layout.addWidget(self._name_modifier)

        self._status = QLabel("")
        self._status.setProperty("tone", "error")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        upload_btn = QPushButton("Upload")
        upload_btn.clicked.connect(self._accept_if_valid)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(upload_btn)
        layout.addLayout(btn_row)

    def _accept_if_valid(self) -> None:
        if not self.username():
            self._status.setText("Username is required.")
            return
        if not self.password():
            self._status.setText("Password is required.")
            return
        self.accept()

    def username(self) -> str:
        return self._username.text().strip()

    def password(self) -> str:
        return self._password.text()

    def remember_credentials(self) -> bool:
        return self._remember.isChecked()

    def name_modifier(self) -> str:
        return self._name_modifier.text().strip()


class SquiglinkUploadMetadataDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_brand: str = "",
        initial_model: str = "",
        initial_channel_side: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload Metadata Required")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Fill required metadata to continue upload."))

        form = QFormLayout()
        self._brand = QLineEdit()
        self._brand.setText(initial_brand)
        form.addRow("Brand *", self._brand)

        self._model = QLineEdit()
        self._model.setText(initial_model)
        form.addRow("Model *", self._model)

        self._channel_side = QComboBox()
        self._channel_side.addItems(["", "L", "R"])
        self._channel_side.setCurrentText(initial_channel_side.strip().upper())
        form.addRow("Channel Side *", self._channel_side)
        layout.addLayout(form)

        self._status = QLabel("")
        self._status.setProperty("tone", "error")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save and Continue")
        save_btn.clicked.connect(self._accept_if_valid)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _accept_if_valid(self) -> None:
        missing = []
        if not self.brand():
            missing.append("Brand")
        if not self.model():
            missing.append("Model")
        if self.channel_side() not in {"L", "R"}:
            missing.append("Channel Side")
        if missing:
            self._status.setText(f"Required: {', '.join(missing)}")
            return
        self.accept()

    def brand(self) -> str:
        return self._brand.text().strip()

    def model(self) -> str:
        return self._model.text().strip()

    def channel_side(self) -> str:
        return self._channel_side.currentText().strip().upper()
