import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

import dms.ui.curator_graph_widget as graph_widget_module
from dms.curator.models import CurveData, GraphState, LayerState
from dms.ui.curator_graph_widget import GraphWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _layer(name: str, offset: float = 0.0) -> LayerState:
    curve = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0, 10000.0]),
        mag_db=np.array([-1.0, 0.0, 1.0]),
    )
    return LayerState(curve=curve, source_path=Path(f"{name}.txt"), name=name, vertical_offset_db=offset)


def _counting_visible_display_layers(monkeypatch, calls: list[int]):
    original = graph_widget_module.visible_display_layers

    def wrapper(layers, smoothing_fraction=48):
        calls.append(1)
        return original(layers, smoothing_fraction)

    monkeypatch.setattr(graph_widget_module, "visible_display_layers", wrapper)


def test_wipe_frames_do_not_recompute_visible_display_layers(qapp, monkeypatch) -> None:
    calls: list[int] = []
    _counting_visible_display_layers(monkeypatch, calls)

    widget = GraphWidget()
    state = GraphState(layers=[_layer("a"), _layer("b")])

    widget.redraw(state)
    assert len(calls) == 1

    widget.start_data_wipe(entering_layer_ids={state.layers[0].id})
    widget._wipe_animation.stop()  # drive frames manually instead of via the timer
    assert len(calls) == 1  # start_data_wipe itself must not recompute

    for progress in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        widget.wipeProgress = progress

    assert len(calls) == 1, "each animation frame must reuse the cached visible layers"


def test_redraw_recomputes_cache_once_per_state_change(qapp, monkeypatch) -> None:
    calls: list[int] = []
    _counting_visible_display_layers(monkeypatch, calls)

    widget = GraphWidget()
    state = GraphState(layers=[_layer("a")])

    widget.redraw(state)
    assert len(calls) == 1

    new_state = GraphState(layers=[_layer("a"), _layer("b")])
    widget.redraw(new_state)
    assert len(calls) == 2


def test_snapshot_visible_layer_uses_cache(qapp, monkeypatch) -> None:
    calls: list[int] = []
    _counting_visible_display_layers(monkeypatch, calls)

    widget = GraphWidget()
    layer = _layer("a")
    state = GraphState(layers=[layer])
    widget.redraw(state)
    assert len(calls) == 1

    snapshot = widget.snapshot_visible_layer(layer.id)

    assert snapshot is not None
    assert snapshot.layer.id == layer.id
    assert len(calls) == 1, "snapshotting a visible layer must not recompute visible_display_layers"
