import time

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from dms import audio_engine

from dms.ui.level_meter import LevelMeterWidget
from dms.ui.style_tokens import (
    DARK_TOKENS,
    DITHER_TOKENS,
    FASTGRAPH_95_DARK_TOKENS,
    FASTGRAPH_95_TOKENS,
    HACKERMAN_95_TOKENS,
    BRAND_TOKENS,
    LIGHT_TOKENS,
)


def test_level_meter_fraction_and_threshold_colors() -> None:
    assert LevelMeterWidget._fraction(-60.0) == 0.0
    assert LevelMeterWidget._fraction(-30.0) == 0.5
    assert LevelMeterWidget._fraction(0.0) == 1.0
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -24.0).name() == DARK_TOKENS.accent.lower()
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -9.0).name() == DARK_TOKENS.warning.lower()
    assert LevelMeterWidget._glow_color(DARK_TOKENS, -0.5).name() == DARK_TOKENS.danger.lower()
    blended = LevelMeterWidget._glow_color(DARK_TOKENS, -16.0).name()
    assert blended not in {DARK_TOKENS.accent.lower(), DARK_TOKENS.warning.lower()}


def test_level_meter_uses_mode_tokens_and_nested_recessed_rects(qapp) -> None:
    meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
    meter.resize(220, 32)
    outer, well, track = meter._paint_rects()
    assert outer.contains(well)
    assert well.contains(track)

    for mode, tokens in (
        ("dark", DARK_TOKENS),
        ("light", LIGHT_TOKENS),
        ("dither", DITHER_TOKENS),
        ("brand", BRAND_TOKENS),
    ):
        qapp.setProperty("fastgraphVisualMode", mode)
        assert meter._tokens() is tokens
        assert not meter._uses_classic_blocks()
        assert meter._uses_flat_dither() is (mode == "dither")
        assert meter._control_paint_path() == ("flat" if mode == "dither" else "modern")
        meter.set_level(-18.0)
        image = meter.grab().toImage()
        assert not image.isNull()


def test_level_meter_interpolates_samples_and_fades_smoothly(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
    meter.resize(220, 32)
    meter.show()
    qapp.processEvents()
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


def test_level_meter_clamps_level_and_renders_vertically(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    meter = LevelMeterWidget(orientation=Qt.Orientation.Vertical)
    meter.resize(32, 180)
    meter.set_level(-0.2)
    assert meter._level_db == -0.2
    meter._level_animation.setCurrentTime(meter._RISE_MS)
    assert "dBFS" in meter.toolTip()
    assert not meter.grab().toImage().isNull()


def test_fastgraph95_level_meter_uses_separate_progress_blocks(qapp) -> None:
    meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
    meter.resize(420, 32)
    _outer, _well, track = meter._paint_rects()
    blocks = meter._classic_block_rects(track.adjusted(1, 1, -1, -1))

    assert len(blocks) >= 20
    assert blocks[1].left() > blocks[0].right()
    for mode, tokens in (
        ("fastgraph95", FASTGRAPH_95_TOKENS),
        ("fastgraph95_dark", FASTGRAPH_95_DARK_TOKENS),
        ("hackerman95", HACKERMAN_95_TOKENS),
    ):
        qapp.setProperty("fastgraphVisualMode", mode)
        meter.set_level(-7.0)
        meter._level_animation.setCurrentTime(meter._RISE_MS)
        assert meter._uses_classic_blocks()
        assert not meter._uses_flat_dither()
        assert meter._control_paint_path() == "classic"
        assert meter._tokens() is tokens
        assert not meter.grab().toImage().isNull()


def _pump_until(qapp, predicate, timeout: float = 3.0) -> bool:
    """Spin the GUI event loop until ``predicate`` holds or time runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(predicate())


def test_level_monitor_callback_never_emits_from_the_audio_thread(qapp) -> None:
    """B10: the PortAudio callback stores a float; it must not touch Qt."""
    monitor = audio_engine.LevelMonitor()
    emitted: list[float] = []
    monitor.level_updated.connect(emitted.append)
    monitor._running = True
    monitor._channel = 0

    block = np.full((256, 1), 0.5, dtype=np.float32)
    monitor._callback(block, 256, None, None)

    assert emitted == []
    assert monitor.latest_dbfs() == pytest.approx(-6.02, abs=0.05)

    monitor._callback(np.zeros((256, 1), dtype=np.float32), 256, None, None)
    assert emitted == []
    assert monitor.latest_dbfs() == audio_engine.SILENCE_DBFS


def test_level_monitor_emits_the_latest_value_from_the_gui_timer(qapp) -> None:
    monitor = audio_engine.LevelMonitor()
    emitted: list[float] = []
    monitor.level_updated.connect(emitted.append)
    monitor._running = True
    monitor._channel = 0
    monitor._callback(np.full((256, 1), 0.25, dtype=np.float32), 256, None, None)

    monitor._start_emit_timer()
    try:
        assert monitor._emit_timer.interval() == audio_engine.LEVEL_EMIT_INTERVAL_MS
        assert _pump_until(qapp, lambda: bool(emitted))
        assert emitted[-1] == pytest.approx(monitor.latest_dbfs())
    finally:
        monitor._stop_emit_timer()

    count = len(emitted)
    _pump_until(qapp, lambda: False, timeout=0.2)
    assert len(emitted) == count


def test_dual_level_monitor_callback_stores_both_channels(qapp) -> None:
    monitor = audio_engine.DualLevelMonitor()
    emitted: list[tuple[float, float]] = []
    monitor.levels_updated.connect(lambda left, right: emitted.append((left, right)))
    monitor._running = True

    block = np.zeros((256, 2), dtype=np.float32)
    block[:, 0] = 0.5
    monitor._callback(block, 256, None, None)

    assert emitted == []
    left, right = monitor.latest_pair_dbfs()
    assert left == pytest.approx(-6.02, abs=0.05)
    assert right == audio_engine.SILENCE_DBFS

    monitor._start_emit_timer()
    try:
        assert _pump_until(qapp, lambda: bool(emitted))
        assert emitted[-1] == pytest.approx((left, right))
    finally:
        monitor._stop_emit_timer()


def test_level_monitors_reset_to_silence_on_stop(qapp) -> None:
    monitor = audio_engine.LevelMonitor()
    monitor._running = True
    monitor._callback(np.full((64, 1), 0.5, dtype=np.float32), 64, None, None)
    monitor.stop()
    assert monitor.latest_dbfs() == audio_engine.SILENCE_DBFS
    assert not monitor._emit_timer.isActive()

    dual = audio_engine.DualLevelMonitor()
    dual._running = True
    dual._callback(np.full((64, 2), 0.5, dtype=np.float32), 64, None, None)
    dual.stop()
    assert dual.latest_pair_dbfs() == (
        audio_engine.SILENCE_DBFS,
        audio_engine.SILENCE_DBFS,
    )
    assert not dual._emit_timer.isActive()
