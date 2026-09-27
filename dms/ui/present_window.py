"""A standalone Curator view for live presentations.

The Curator graph with a trimmed side panel, an annotation overlay (draw,
tape, crosshair, laser), keyboard layer control, a 1920x1080 preset and a
clipboard copy. Nothing here is saved.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from dms.curator.models import LayerState
from dms.settings_manager import SettingsManager
from dms.style_tokens import tokens_for
from dms.theme import ThemeController, theme_trace_palette
from dms.ui.annotation_overlay import AnnotationOverlay
from dms.ui.curator_widget import ACCENT_COLOR, CuratorWidget
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox
from dms.ui.toggle_switch import ToggleSwitch

PRESET_SIZE = QSize(1920, 1080)
MODE_KEYS = {"D": "draw", "T": "tape", "L": "laser", "Esc": "pointer"}
LEGEND = (
    "D draw   T tape   L laser   Esc pointer\n"
    "⌘Z undo   ⇧⌘Z clear   [ ] pen   C crosshair\n"
    "1–9 layer   H solo   B bounds   N names\n"
    "F panel   S scale   ⌘0 1080p   ⌘C copy   ⌘O add\n"
    "Shift-drag line   Alt-click free tape"
)
_EDITORS = (
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox,
    ModernSpinBox,
    ModernDoubleSpinBox,
    QKeySequenceEdit,
)


class PresentWindow(QMainWindow):
    def __init__(
        self,
        settings: SettingsManager,
        theme_controller: ThemeController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme_controller = theme_controller
        self._solo_restore: dict[str, bool] | None = None
        self.curator = CuratorWidget(
            theme=theme_controller.theme,
            brand_mode=theme_controller.brand_mode,
            present=True,
            parent=self,
        )
        self.setCentralWidget(self.curator)
        self.overlay = AnnotationOverlay(self.curator._graph, curves=self.curator.display_curves)
        self._build_tools()
        self._legend = QLabel(LEGEND)
        self._legend.setProperty("tone", "muted")
        self.curator.panel_layout().addWidget(self._legend)
        self._configure_shortcuts()
        theme_controller.theme_changed.connect(self._on_theme_changed)
        theme_controller.brand_mode_changed.connect(self._on_theme_changed)
        self._on_theme_changed()
        self.setWindowTitle("FastGraph Present")
        self.resize(1600, 1000)

    @property
    def graph_frame(self) -> QWidget:
        return self.curator._graph_frame

    def _build_tools(self) -> None:
        box = QGroupBox("Present")
        grid = QGridLayout(box)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(5)
        self._mode_buttons: dict[str, QPushButton] = {}
        for column, mode in enumerate(("pointer", "draw", "tape", "laser")):
            button = QPushButton(mode.title())
            button.clicked.connect(lambda _checked=False, m=mode: self.set_mode(m))
            grid.addWidget(button, 0, column)
            self._mode_buttons[mode] = button

        self._swatch_row = QHBoxLayout()
        self._swatch_row.setSpacing(4)
        grid.addLayout(self._swatch_row, 1, 0, 1, 4)

        self._pen_label = QLabel()
        thinner = QPushButton("Pen −")
        thinner.clicked.connect(lambda: self.change_pen_width(-1))
        thicker = QPushButton("Pen +")
        thicker.clicked.connect(lambda: self.change_pen_width(1))
        self._crosshair_toggle = ToggleSwitch("Crosshair")
        self._crosshair_toggle.toggled.connect(self._set_crosshair)
        for column, widget in enumerate((thinner, self._pen_label, thicker)):
            grid.addWidget(widget, 2, column)
        grid.addWidget(self._crosshair_toggle, 2, 3)

        actions = (
            ("Undo", self.overlay.undo),
            ("Clear", self.overlay.clear),
            ("Copy PNG", self.copy_png),
            ("1080p", self.preset_1080p),
            ("Hide Panel", self.toggle_panel),
        )
        for index, (text, slot) in enumerate(actions):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, s=slot: s())
            grid.addWidget(button, 3 + index // 4, index % 4)
        self._scale_toggle = ToggleSwitch("Scale 1.5×")
        self._scale_toggle.toggled.connect(self._set_font_scale)
        grid.addWidget(self._scale_toggle, 4, 1, 1, 3)
        self.curator.panel_layout().insertWidget(0, box)
        self.set_mode("pointer")
        self.change_pen_width(0)

    def _fill_swatches(self) -> None:
        while self._swatch_row.count():
            item = self._swatch_row.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        palette = theme_trace_palette(
            self._theme_controller.theme, brand_mode=self._theme_controller.brand_mode
        )
        for color in dict.fromkeys([ACCENT_COLOR, *palette]):
            swatch = QPushButton()
            swatch.setFixedSize(22, 22)
            swatch.setToolTip(color)
            swatch.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            swatch.clicked.connect(lambda _checked=False, c=color: self.set_pen_color(c))
            self._swatch_row.addWidget(swatch)
        custom = QPushButton("…")
        custom.setToolTip("Custom colour")
        custom.clicked.connect(self._choose_custom_color)
        self._swatch_row.addWidget(custom)
        self._swatch_row.addStretch(1)

    def _configure_shortcuts(self) -> None:
        bindings = {key: (lambda m=mode: self.set_mode(m)) for key, mode in MODE_KEYS.items()}
        bindings.update(
            {
                "Ctrl+Z": self.overlay.undo,
                "Ctrl+Shift+Z": self.overlay.clear,
                "[": lambda: self.change_pen_width(-1),
                "]": lambda: self.change_pen_width(1),
                "C": self._crosshair_toggle.toggle,
                "H": self.toggle_solo,
                "B": self._toggle_bounds,
                "N": self.curator._show_names_enabled.toggle,
                "F": self.toggle_panel,
                "S": self._scale_toggle.toggle,
                "Ctrl+0": self.preset_1080p,
                "Ctrl+C": self.copy_png,
                "Ctrl+O": self.curator._choose_import_files,
            }
        )
        for number in range(1, 10):
            bindings[str(number)] = lambda n=number: self.toggle_layer(n)
        self._shortcuts = []
        for key, action in bindings.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda a=action: self._run_shortcut(a))
            self._shortcuts.append(shortcut)

    @staticmethod
    def _run_shortcut(action) -> None:
        if not isinstance(QApplication.focusWidget(), _EDITORS):
            action()

    # Actions ------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self.overlay.set_mode(mode)
        for name, button in self._mode_buttons.items():
            button.setRole("primary" if name == mode else "default")

    def set_pen_color(self, color: str) -> None:
        self.overlay.pen_color = color

    def _choose_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.overlay.pen_color), self, "Drawing Colour")
        if color.isValid():
            self.set_pen_color(color.name())

    def change_pen_width(self, delta: int) -> None:
        self.overlay.set_pen_width(self.overlay.pen_width + delta)
        self._pen_label.setText(f"Width {self.overlay.pen_width}")
        self._pen_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _set_crosshair(self, enabled: bool) -> None:
        self.overlay.crosshair = bool(enabled)
        self.overlay.update()

    def _set_font_scale(self, enabled: bool) -> None:
        scale = 1.5 if enabled else 1.0
        self.curator._graph.set_font_scale(scale)
        self.overlay.set_font_scale(scale)

    def toggle_layer(self, number: int) -> None:
        layers = self.curator.graph_state.layers
        if number <= len(layers):
            self.curator.set_layer_number_visible(number, not layers[number - 1].visible)

    def toggle_solo(self) -> None:
        layers = self.curator.graph_state.layers
        if self._solo_restore is not None:
            for layer in layers:
                layer.visible = self._solo_restore.get(layer.id, layer.visible)
            self._solo_restore = None
        else:
            selected = self.curator._selected_layer()
            if selected is None:
                return
            self._solo_restore = {layer.id: layer.visible for layer in layers}
            for layer in layers:
                layer.visible = layer is selected
        self.curator._sync_ui()
        self.curator._redraw()

    def _toggle_bounds(self) -> None:
        self.curator.set_bounds_enabled(not self.curator.graph_state.bounds.enabled)

    def toggle_panel(self) -> None:
        scroll = self.curator._controls_scroll
        scroll.setVisible(not scroll.isVisible())

    def preset_1080p(self) -> None:
        """Hide the panel and size the window so the graph frame is exactly 1920x1080."""
        self.curator._controls_scroll.hide()
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        for _attempt in range(3):
            QApplication.sendPostedEvents()
            delta = PRESET_SIZE - self.graph_frame.size()
            if delta.isNull():
                return
            self.resize(self.size() + delta)

    def copy_png(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setPixmap(self.graph_frame.grab())
        self.statusBar().showMessage("Graph copied to the clipboard.", 3000)

    def add_layers(self, layers: list[LayerState]) -> None:
        """Seed copies of ``layers`` with their colour, offset, visibility and HRTF."""
        for layer in layers:
            copy = self.curator.add_curve(
                layer.curve,
                layer.name,
                source_path=layer.source_path,
                hrtf=layer.hrtf,
                normalize=False,
                animate=False,
            )
            copy.color = layer.color
            copy.vertical_offset_db = layer.vertical_offset_db
            copy.visible = layer.visible
        self.curator._sync_ui()
        self.curator._redraw()

    def _on_theme_changed(self, *_args) -> None:
        controller = self._theme_controller
        self.curator.apply_theme(controller.theme, brand_mode=controller.brand_mode)
        typography = tokens_for(controller.theme, brand_mode=controller.brand_mode).typography
        self._legend.setStyleSheet(
            f"font-family: '{typography.technical_family}', monospace; "
            f"font-size: {typography.caption_px}px;"
        )
        self._fill_swatches()
        self.overlay.update()
