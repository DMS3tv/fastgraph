"""Custom controls for the Measure tab's queue bar."""

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QWidget,
)
from PyQt6.QtWidgets import (
    QPushButton as NativePushButton,
)


class _ResponsiveQueueBar(QWidget):
    compact_changed = pyqtSignal(bool)
    _BASE_COMPACT_WIDTH = 1250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact: bool | None = None
        self._additional_compact_width = 0

    @property
    def compact_breakpoint(self) -> int:
        return self._BASE_COMPACT_WIDTH + self._additional_compact_width

    def set_additional_compact_width(self, width: int) -> None:
        width = max(0, int(width))
        if width == self._additional_compact_width:
            return
        self._additional_compact_width = width
        self._update_compact_state(self.width())

    def _update_compact_state(self, width: int) -> None:
        compact = width < self.compact_breakpoint
        if compact != self._compact:
            self._compact = compact
            self.compact_changed.emit(compact)

    def resizeEvent(self, event) -> None:
        self._update_compact_state(event.size().width())
        super().resizeEvent(event)


class _MeasureSubmodeControl(QWidget):
    """Two joined buttons that select the Measure tab submode."""

    balance_toggled = pyqtSignal(bool)
    minimum_width_changed = pyqtSignal(int)
    _TEXT_WIDTH_HEADROOM = 4
    _WIDTH_STATES = (
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Off,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_HasFocus,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_MouseOver,
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_HasFocus,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_MouseOver,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width_refresh_pending = False
        self._minimum_width = 0
        self.setObjectName("measure_submode_control")
        self.setProperty("layoutRole", "transparent")
        self.setAccessibleName("Measure mode")
        self.setToolTip("Select Frequency Response or Channel Balance.")
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.frequency_button = self._make_segment(
            "Frequency Response",
            "first",
            "Frequency Response measure mode",
            "Use Frequency Response mode.",
        )
        self.balance_button = self._make_segment(
            "Channel Balance",
            "last",
            "Channel Balance measure mode",
            "Use Channel Balance mode.",
        )

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.addButton(self.frequency_button, 0)
        self._button_group.addButton(self.balance_button, 1)
        layout.addWidget(self.frequency_button)
        layout.addWidget(self.balance_button)

        self.frequency_button.setChecked(True)
        self.balance_button.toggled.connect(self.balance_toggled)
        self.refresh_segment_widths()

    def _make_segment(
        self,
        text: str,
        position: str,
        accessible_name: str,
        tooltip: str,
    ) -> NativePushButton:
        button = NativePushButton(text, self)
        button.setCheckable(True)
        button.setProperty("measureSegment", True)
        button.setProperty("segmentPosition", position)
        button.setAccessibleName(accessible_name)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        button.installEventFilter(self)
        return button

    @property
    def minimum_control_width(self) -> int:
        return self._minimum_width

    @classmethod
    def _required_width_for_state(
        cls,
        button: NativePushButton,
        state: QStyle.StateFlag,
    ) -> int:
        metrics = QFontMetrics(button.font())
        text_size = QSize(
            metrics.horizontalAdvance(button.text()),
            metrics.height(),
        )
        option = QStyleOptionButton()
        option.initFrom(button)
        option.text = button.text()
        option.state = state
        return (
            button.style()
            .sizeFromContents(
                QStyle.ContentsType.CT_PushButton,
                option,
                text_size,
                button,
            )
            .width()
        )

    def refresh_segment_widths(self) -> None:
        self._width_refresh_pending = False
        widths = []
        for button in (self.frequency_button, self.balance_button):
            required_width = max(
                self._required_width_for_state(button, state) for state in self._WIDTH_STATES
            )
            width = required_width + self._TEXT_WIDTH_HEADROOM
            button.setFixedWidth(width)
            widths.append(width)
        minimum_width = sum(widths)
        self.setFixedWidth(minimum_width)
        if minimum_width != self._minimum_width:
            self._minimum_width = minimum_width
            self.minimum_width_changed.emit(minimum_width)

    def _schedule_width_refresh(self) -> None:
        if self._width_refresh_pending:
            return
        self._width_refresh_pending = True
        QTimer.singleShot(0, self.refresh_segment_widths)

    def eventFilter(self, watched, event) -> bool:
        if bool(watched.property("measureSegment")) and event.type() in {
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.FontChange,
            QEvent.Type.Polish,
            QEvent.Type.StyleChange,
        }:
            self._schedule_width_refresh()
        return super().eventFilter(watched, event)
