from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from dms.ui.level_meter import LevelMeterWidget
from dms.ui.style_tokens import DARK_TOKENS, BRAND_TOKENS, LIGHT_TOKENS


_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_level_meter_fraction_and_threshold_colors() -> None:
    assert LevelMeterWidget._fraction(-60.0) == 0.0
    assert LevelMeterWidget._fraction(-30.0) == 0.5
    assert LevelMeterWidget._fraction(0.0) == 1.0
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -24.0).name() == DARK_TOKENS.accent.lower()
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -9.0).name() == DARK_TOKENS.warning.lower()
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -0.5).name() == DARK_TOKENS.danger.lower()
    blended = LevelMeterWidget._glow_color(DARK_TOKENS, -16.0).name()
    assert blended not in {DARK_TOKENS.accent.lower(), DARK_TOKENS.warning.lower()}


def test_level_meter_uses_mode_tokens_and_nested_recessed_rects() -> None:
    app = _app()
    meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
    meter.resize(220, 32)
    outer, well, track = meter._paint_rects()
    assert outer.contains(well)
    assert well.contains(track)

    for mode, tokens in (
        ("dark", DARK_TOKENS),
        ("light", LIGHT_TOKENS),
        ("brand", BRAND_TOKENS),
    ):
        app.setProperty("fastgraphVisualMode", mode)
        assert meter._tokens() is tokens
        meter.set_level(-18.0)
        image = meter.grab().toImage()
        assert not image.isNull()


def test_level_meter_interpolates_samples_and_fades_smoothly() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
    meter.resize(220, 32)
    meter.show()
    app.processEvents()
    meter.set_level(-6.0)
    meter._level_animation.setCurrentTime(meter._RISE_MS // 2)
    assert -60.0 < meter._display_db < -6.0
    meter._level_animation.setCurrentTime(meter._RISE_MS)
    assert abs(meter._display_db - (-6.0)) < 0.2

    meter.set_level(-60.0)
    meter._level_animation.setCurrentTime(40)
    assert -60.0 < meter._display_db < -6.0
    meter._level_animation.setCurrentTime(meter._FALL_MS)
    assert abs(meter._display_db - (-60.0)) < 0.2
    assert not hasattr(meter, "_peak_db")


def test_level_meter_clamps_level_and_renders_vertically() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    meter = LevelMeterWidget(orientation=Qt.Orientation.Vertical)
    meter.resize(32, 180)
    meter.set_level(-0.2)
    assert meter._level_db == -0.2
    meter._level_animation.setCurrentTime(meter._RISE_MS)
    assert "dBFS" in meter.toolTip()
    assert not meter.grab().toImage().isNull()
