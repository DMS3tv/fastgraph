"""Parametric EQ suggestion for the loaded target curve.

The window hands this dialog the measurement average and the target it is
being compared against; everything else — the delta, the greedy bell fit and
the two text renderings — comes from :mod:`dms.comparison`, so the dialog is
only controls, a read-only report and two ways to get the preset out.

The fit is recomputed whenever a control changes rather than on a button, so
the residual and the match percentage always describe the filters on screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from dms.comparison import (
    OFFSET_MODES,
    DeltaResult,
    delta_curve,
    deviation_score,
    format_eq_apo,
    format_eq_table,
    suggest_eq,
)
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import (
    ModernDoubleSpinBox as QDoubleSpinBox,
    ModernSpinBox as QSpinBox,
)


#: Offset modes in the order the combo shows them, with the wording used
#: everywhere else in the Measure tab.
_OFFSET_LABELS = (
    ("1 kHz", "1khz"),
    ("Mean 200 Hz - 2 kHz", "mean_200_2k"),
    ("None", "none"),
)

DEFAULT_MAX_FILTERS = 8
DEFAULT_MAX_GAIN_DB = 12.0


class EqSuggestionDialog(QDialog):
    """Suggest peaking filters that bring a measurement onto its target."""

    def __init__(
        self,
        measurement: tuple[np.ndarray, np.ndarray],
        target: tuple[np.ndarray, np.ndarray],
        *,
        offset_mode: str = "1khz",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("EQ Suggestion")
        self.setMinimumWidth(520)
        self._measurement = (
            np.asarray(measurement[0], dtype=float),
            np.asarray(measurement[1], dtype=float),
        )
        self._target = (
            np.asarray(target[0], dtype=float),
            np.asarray(target[1], dtype=float),
        )
        self._suggestion = None
        self._apo_text = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Peaking filters fitted greedily to the difference between the "
            "measured average and the target. They are a starting point, not "
            "a calibration."
        )
        intro.setWordWrap(True)
        intro.setProperty("tone", "muted")
        layout.addWidget(intro)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Max filters"))
        self._max_filters_spin = QSpinBox()
        self._max_filters_spin.setRange(1, 10)
        self._max_filters_spin.setValue(DEFAULT_MAX_FILTERS)
        self._max_filters_spin.setMinimumWidth(78)
        controls.addWidget(self._max_filters_spin)

        controls.addWidget(QLabel("Max gain"))
        self._max_gain_spin = QDoubleSpinBox()
        self._max_gain_spin.setRange(1.0, 24.0)
        self._max_gain_spin.setSingleStep(0.5)
        self._max_gain_spin.setDecimals(1)
        self._max_gain_spin.setSuffix(" dB")
        self._max_gain_spin.setValue(DEFAULT_MAX_GAIN_DB)
        controls.addWidget(self._max_gain_spin)

        controls.addWidget(QLabel("Align on"))
        self._offset_combo = QComboBox()
        for label, value in _OFFSET_LABELS:
            self._offset_combo.addItem(label, value)
        index = self._offset_combo.findData(
            offset_mode if offset_mode in OFFSET_MODES else "1khz"
        )
        self._offset_combo.setCurrentIndex(max(0, index))
        self._offset_combo.setMaximumWidth(190)
        controls.addWidget(self._offset_combo)
        controls.addStretch(1)
        layout.addLayout(controls)

        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setObjectName("eq_suggestion_report")
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setFixedPitch(True)
        self._report.setFont(mono)
        self._report.setMinimumHeight(240)
        layout.addWidget(self._report, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._copy_btn = QPushButton("Copy Equalizer APO")
        self._copy_btn.clicked.connect(self._copy_apo)
        buttons.addWidget(self._copy_btn)
        self._save_btn = QPushButton("Save APO .txt…")
        self._save_btn.clicked.connect(self._save_apo)
        buttons.addWidget(self._save_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self._max_filters_spin.valueChanged.connect(self._recompute)
        self._max_gain_spin.valueChanged.connect(self._recompute)
        self._offset_combo.currentIndexChanged.connect(self._recompute)
        self._recompute()

    # -- state -----------------------------------------------------------

    def offset_mode(self) -> str:
        return str(self._offset_combo.currentData() or "1khz")

    def apo_text(self) -> str:
        """The Equalizer APO preset currently on screen."""
        return self._apo_text

    def report_text(self) -> str:
        return self._report.toPlainText()

    # -- work ------------------------------------------------------------

    def _delta(self) -> DeltaResult:
        return delta_curve(
            self._measurement[0],
            self._measurement[1],
            self._target[0],
            self._target[1],
            offset_mode=self.offset_mode(),
        )

    def _recompute(self, *_args) -> None:
        delta = self._delta()
        before = deviation_score(delta)
        suggestion = suggest_eq(
            delta,
            max_filters=int(self._max_filters_spin.value()),
            max_gain_db=float(self._max_gain_spin.value()),
        )
        after = deviation_score(
            DeltaResult(
                freqs=delta.freqs,
                delta_db=np.asarray(suggestion.residual_delta_db, dtype=float),
                offset_db=delta.offset_db,
            )
        )
        self._suggestion = suggestion
        self._apo_text = format_eq_apo(suggestion)
        self._report.setPlainText(
            "\n".join(
                (
                    format_eq_table(suggestion),
                    "",
                    f"Residual RMS   {before.overall_rms_db:.2f} dB  ->  "
                    f"{suggestion.residual_rms_db:.2f} dB",
                    f"Match          {before.match_percent:.0f} %  ->  "
                    f"{after.match_percent:.0f} %",
                    "",
                    "Equalizer APO",
                    self._apo_text,
                )
            )
        )

    def _copy_apo(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:  # pragma: no cover - no clipboard in some hosts
            return
        clipboard.setText(self._apo_text)

    def _save_apo(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Equalizer APO Preset",
            "fastgraph-eq.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return
        try:
            Path(path_str).write_text(self._apo_text + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Save Failed",
                f"Could not write the EQ preset.\n\n{exc}",
            )
            return
        self.setWindowTitle(f"EQ Suggestion — saved {Path(path_str).name}")
