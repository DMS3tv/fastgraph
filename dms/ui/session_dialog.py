from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QDialogButtonBox, QVBoxLayout, QGroupBox,
    QLabel, QScrollArea, QWidget,
)
from PyQt6.QtCore import Qt
from dms.session import SessionData
from dms.settings_manager import SettingsManager


class SessionDialog(QDialog):
    _RIG_OPTIONS = [
        "KB500X",
        "B&K 4128",
        "B&K 5128",
        "KB006X",
        "EARS PRO 711",
        "RA0402 (711)",
    ]

    def __init__(
        self,
        settings: SettingsManager,
        parent=None,
        initial_session: SessionData | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Headphone Metadata")
        self.setMinimumWidth(480)
        self._settings = settings
        self._initial_session = initial_session
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        outer.addWidget(QLabel(
            "<b>Enter headphone / test setup info before measuring.</b>"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def line(placeholder="") -> QLineEdit:
            w = QLineEdit()
            w.setPlaceholderText(placeholder)
            return w

        self._rig = QComboBox()
        self._rig.setEditable(False)
        self._rig.addItems(self._RIG_OPTIONS)
        self._brand = line("e.g. Sennheiser")
        self._model = line("e.g. HD 800 S")
        self._model_number = line("optional")
        self._asset_tag = line("optional internal tag")
        self._firmware = line("optional")

        self._eq = QCheckBox("EQ applied")
        self._anc = QCheckBox("ANC active")
        self._transparency = QCheckBox("Transparency active")
        self._anc.toggled.connect(self._on_anc_toggled)
        self._transparency.toggled.connect(self._on_transparency_toggled)

        self._form_factor = QComboBox()
        self._form_factor.addItems(["over-ear", "on-ear", "in-ear"])
        self._form_factor.currentTextChanged.connect(self._on_form_factor_changed)

        self._in_ear_fitment = QComboBox()
        self._in_ear_fitment.addItems(["shallow fitment", "mid fitment", "deep fitment"])

        self._open_back = QComboBox()
        self._open_back.addItems(["open back", "closed back", "semi-open"])

        self._pads = line("e.g. foam tips size M")
        self._connection = QComboBox()
        self._connection.addItems([
            "wired analog",
            "wired USB",
            "bluetooth",
            "wireless dongle",
            "other",
        ])
        self._channel_side = QComboBox()
        self._channel_side.addItems(["", "L", "R"])
        self._channel_side.setMinimumWidth(120)

        form.addRow("Rig *", self._rig)
        form.addRow("Brand *", self._brand)
        form.addRow("Model *", self._model)
        form.addRow("Channel Side *", self._channel_side)
        form.addRow("Model Number", self._model_number)
        form.addRow("Asset Tag", self._asset_tag)
        form.addRow("Firmware", self._firmware)
        form.addRow("", self._eq)
        form.addRow("", self._anc)
        form.addRow("", self._transparency)
        form.addRow("Form Factor", self._form_factor)
        form.addRow("In-ear Fitment", self._in_ear_fitment)
        form.addRow("Acoustic Type", self._open_back)
        form.addRow("Pads / Tips Notes", self._pads)
        form.addRow("Connection", self._connection)
        self._fitment_label = form.labelForField(self._in_ear_fitment)
        self._load_initial_values()
        self._sync_in_ear_fitment_visibility()

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._status = QLabel("")
        self._status.setProperty("tone", "error")
        outer.addWidget(self._status)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _validate_and_accept(self) -> None:
        missing = []
        if not self._rig.currentText().strip():
            missing.append("Rig")
        if not self._brand.text().strip():
            missing.append("Brand")
        if not self._model.text().strip():
            missing.append("Model")
        if not self._channel_side.currentText().strip():
            missing.append("Channel Side")
        if missing:
            self._status.setText(f"Required: {', '.join(missing)}")
            return
        self.accept()

    def _load_initial_values(self) -> None:
        if self._initial_session is None:
            return

        s = self._initial_session
        rig_idx = self._rig.findText(s.rig)
        if rig_idx >= 0:
            self._rig.setCurrentIndex(rig_idx)
        self._brand.setText(s.brand)
        self._model.setText(s.model)
        self._model_number.setText(s.model_number)
        self._asset_tag.setText(s.asset_tag)
        self._firmware.setText(s.firmware)
        self._eq.setChecked(s.eq_applied)
        self._anc.setChecked(s.anc_mode)
        self._transparency.setChecked(getattr(s, "transparency_mode", False))
        self._form_factor.setCurrentText(s.form_factor)
        fitment = getattr(s, "in_ear_fitment", "")
        if fitment:
            self._in_ear_fitment.setCurrentText(fitment)
        self._open_back.setCurrentText("open back" if s.open_back else "closed back")
        self._pads.setText(s.pads_notes)
        self._connection.setCurrentText(s.connection)
        self._channel_side.setCurrentText((getattr(s, "channel_side", "") or "").strip().upper())

    def session_data(self) -> SessionData:
        return SessionData(
            rig=self._rig.currentText().strip(),
            brand=self._brand.text().strip(),
            model=self._model.text().strip(),
            model_number=self._model_number.text().strip(),
            asset_tag=self._asset_tag.text().strip(),
            firmware=self._firmware.text().strip(),
            eq_applied=self._eq.isChecked(),
            anc_mode=self._anc.isChecked(),
            transparency_mode=self._transparency.isChecked(),
            form_factor=self._form_factor.currentText(),
            in_ear_fitment=(
                self._in_ear_fitment.currentText()
                if self._form_factor.currentText() == "in-ear"
                else ""
            ),
            open_back=self._open_back.currentText() == "open back",
            pads_notes=self._pads.text().strip(),
            connection=self._connection.currentText(),
            channel_side=self._channel_side.currentText().strip().upper(),
        )

    def _on_anc_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._transparency.blockSignals(True)
        self._transparency.setChecked(False)
        self._transparency.blockSignals(False)

    def _on_transparency_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._anc.blockSignals(True)
        self._anc.setChecked(False)
        self._anc.blockSignals(False)

    def _on_form_factor_changed(self, _value: str) -> None:
        self._sync_in_ear_fitment_visibility()

    def _sync_in_ear_fitment_visibility(self) -> None:
        is_in_ear = self._form_factor.currentText() == "in-ear"
        self._in_ear_fitment.setVisible(is_in_ear)
        if self._fitment_label is not None:
            self._fitment_label.setVisible(is_in_ear)
