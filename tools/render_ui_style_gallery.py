"""Render a compact FastGraph component gallery for visual review."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dms.theme import application_stylesheet, brand_application_stylesheet
from dms.ui.modern_button import ModernButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox
from dms.ui.style_tokens import mode_tokens
from dms.ui.theme_surface import DitherSurface


def _button(
    label: str,
    role: str,
    *,
    hover: bool = False,
    flash: bool = False,
) -> ModernButton:
    button = ModernButton(label)
    button.setRole(role)
    if hover:
        button._set_hover_progress(1.0)
    if flash:
        button._set_hover_progress(1.0)
        button._set_press_progress(1.0)
    return button


def build_gallery(mode: str) -> QWidget:
    tokens = mode_tokens(mode)
    root = QWidget()
    root.setWindowTitle(f"FastGraph UI Style - {mode.title()}")
    root.resize(960, 820)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)

    title = QLabel(f"FastGraph UI Style - {mode.title()}")
    title.setProperty("typographyRole", "screen")
    layout.addWidget(title)

    header = DitherSurface()
    header.setFixedHeight(34)
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(8, 2, 8, 2)
    header_layout.addWidget(QLabel("Header surface"))
    header_layout.addStretch(1)
    header_layout.addWidget(_button("Inputs", "primary"))
    layout.addWidget(header)

    tabs = QTabWidget()
    for name in ("Measure", "R&&D", "Curator", "Automation", "Settings"):
        page = QWidget()
        page.setProperty("surfaceLevel", "viewport")
        tabs.addTab(page, name)
    tabs.setCurrentIndex(2)
    tabs.setFixedHeight(76)
    layout.addWidget(tabs)

    surfaces = QHBoxLayout()
    for level in ("viewport", "panel", "raised"):
        surface = QWidget()
        surface.setProperty("surfaceLevel", level)
        surface_layout = QVBoxLayout(surface)
        label = QLabel(level.title())
        label.setProperty("typographyRole", "section")
        surface_layout.addWidget(label)
        surface_layout.addWidget(QLabel(f"{level} surface"))
        surfaces.addWidget(surface)
    layout.addLayout(surfaces)

    controls = QGroupBox("Fields and Controls")
    controls_layout = QHBoxLayout(controls)
    field = QLineEdit("Headphone metadata")
    field.setProperty("typographyRole", "technical")
    controls_layout.addWidget(field)
    combo = QComboBox()
    combo.addItems(["1/48 smoothing", "1/24 smoothing", "1/12 smoothing"])
    controls_layout.addWidget(combo)
    integer = ModernSpinBox()
    integer.setRange(1, 100)
    integer.setValue(5)
    controls_layout.addWidget(integer)
    decimal = ModernDoubleSpinBox()
    decimal.setRange(-20.0, 20.0)
    decimal.setValue(-6.0)
    decimal.setSuffix(" dB")
    controls_layout.addWidget(decimal)
    layout.addWidget(controls)

    section_header = QToolButton()
    section_header.setObjectName("section_toggle")
    section_header.setText("Devices")
    section_header.setCheckable(True)
    section_header.setChecked(True)
    section_header.setArrowType(Qt.ArrowType.DownArrow)
    section_header.setToolButtonStyle(
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )
    section_header.setMinimumWidth(320)
    layout.addWidget(section_header)

    buttons = QGroupBox("Button Roles")
    button_layout = QVBoxLayout(buttons)
    first_row = QHBoxLayout()
    first_row.addWidget(_button("Rest", "default"))
    first_row.addWidget(_button("Hover fill", "default", hover=True))
    first_row.addWidget(_button("Click flash", "default", flash=True))
    start = _button("Start Queue", "primary")
    start.setObjectName("btn_start")
    first_row.addWidget(start)
    button_layout.addLayout(first_row)
    second_row = QHBoxLayout()
    second_row.addWidget(_button("Positive", "positive"))
    second_row.addWidget(_button("Warning", "warning"))
    second_row.addWidget(_button("Danger", "danger"))
    button_layout.addLayout(second_row)
    third_row = QHBoxLayout()
    third_row.addWidget(_button("Ghost", "ghost"))
    disabled = _button("Disabled", "default")
    disabled.setEnabled(False)
    third_row.addWidget(disabled)
    third_row.addStretch(2)
    button_layout.addLayout(third_row)
    layout.addWidget(buttons)

    scroll = QScrollArea()
    scroll.setWidgetResizable(False)
    scroll.setFixedHeight(74)
    scroll_content = QWidget()
    scroll_content.resize(1200, 120)
    scroll_content.setProperty("surfaceLevel", "raised")
    scroll.setWidget(scroll_content)
    layout.addWidget(scroll)

    status = QLabel(
        f"Accent {tokens.accent}  |  Button radius {tokens.geometry.radius_button}px"
    )
    status.setProperty("typographyRole", "caption")
    status.setAlignment(Qt.AlignmentFlag.AlignRight)
    layout.addWidget(status)
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    for mode in (
        "dark",
        "light",
        "fastgraph95",
        "fastgraph95_dark",
        "hackerman95",
        "brand",
    ):
        app.setProperty("fastgraphVisualMode", mode)
        app.setStyleSheet(
            brand_application_stylesheet()
            if mode == "brand"
            else application_stylesheet(mode)
        )
        gallery = build_gallery(mode)
        gallery.show()
        app.processEvents()
        gallery.grab().save(str(args.output_dir / f"ui-style-{mode}.png"))
        gallery.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
