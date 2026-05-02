from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QDialogButtonBox, QVBoxLayout, QGroupBox,
    QLabel, QScrollArea, QWidget,
)
from PyQt6.QtCore import Qt
from dms.session import SessionData
from dms.settings_manager import SettingsManager


class SessionDialog(QDialog):
    def __init__(self, settings: SettingsManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DMS Fastgraph — New Session")
        self.setMinimumWidth(480)
        self._settings = settings
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

        self._rig = line("e.g. B&K 5128")
        self._brand = line("e.g. Sennheiser")
        self._model = line("e.g. HD 800 S")
        self._model_number = line("optional")
        self._asset_tag = line("optional internal tag")
        self._firmware = line("optional")

        self._eq = QCheckBox("EQ applied")
        self._anc = QCheckBox("ANC / transparency active")
        self._anc_name = line("mode name if applicable")

        self._form_factor = QComboBox()
        self._form_factor.addItems(["over-ear", "on-ear", "in-ear"])

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

        form.addRow("Rig *", self._rig)
        form.addRow("Brand *", self._brand)
        form.addRow("Model *", self._model)
        form.addRow("Model Number", self._model_number)
        form.addRow("Asset Tag", self._asset_tag)
        form.addRow("Firmware", self._firmware)
        form.addRow("", self._eq)
        form.addRow("", self._anc)
        form.addRow("ANC Mode Name", self._anc_name)
        form.addRow("Form Factor", self._form_factor)
        form.addRow("Acoustic Type", self._open_back)
        form.addRow("Pads / Tips Notes", self._pads)
        form.addRow("Connection", self._connection)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #ff6666;")
        outer.addWidget(self._status)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

        # Restore last values
        self._rig.setText(self._settings.get("last_rig") or "")
        self._brand.setText(self._settings.get("last_brand") or "")
        self._model.setText(self._settings.get("last_model") or "")

    def _validate_and_accept(self) -> None:
        missing = []
        if not self._rig.text().strip():
            missing.append("Rig")
        if not self._brand.text().strip():
            missing.append("Brand")
        if not self._model.text().strip():
            missing.append("Model")
        if missing:
            self._status.setText(f"Required: {', '.join(missing)}")
            return

        self._settings.update({
            "last_rig": self._rig.text().strip(),
            "last_brand": self._brand.text().strip(),
            "last_model": self._model.text().strip(),
        })
        self.accept()

    def session_data(self) -> SessionData:
        return SessionData(
            rig=self._rig.text().strip(),
            brand=self._brand.text().strip(),
            model=self._model.text().strip(),
            model_number=self._model_number.text().strip(),
            asset_tag=self._asset_tag.text().strip(),
            firmware=self._firmware.text().strip(),
            eq_applied=self._eq.isChecked(),
            anc_mode=self._anc.isChecked(),
            anc_mode_name=self._anc_name.text().strip(),
            form_factor=self._form_factor.currentText(),
            open_back=self._open_back.currentText() == "open back",
            pads_notes=self._pads.text().strip(),
            connection=self._connection.currentText(),
        )