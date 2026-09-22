from PyQt6.QtCore import QPoint

from dms.ui.toggle_switch import ToggleSwitch


def test_custom_switch_uses_full_painted_hitbox(qapp) -> None:
    switch = ToggleSwitch("")
    switch.resize(54, 30)
    assert switch.hitButton(QPoint(2, 2)) is True
    assert switch.hitButton(QPoint(51, 27)) is True


def test_custom_switch_reserves_space_for_painted_track(qapp) -> None:
    switch = ToggleSwitch("")
    assert switch.sizeHint().width() >= 54
    assert switch.minimumSizeHint().width() >= 54
