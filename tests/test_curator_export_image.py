from pathlib import Path

import numpy as np
import pytest
from helpers import variation_graph_state
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage

import dms.curator.export_image as export_image_module
from dms.curator.export_image import (
    ACCENT_COLOR,
    DITHER_FOOTER_HEIGHT,
    PLOT_INSET_BOTTOM,
    PLOT_INSET_LEFT,
    PLOT_INSET_RIGHT,
    PLOT_INSET_TOP,
    _curve_path,
    _draw_legend,
    _y_for_db,
    aligned_bounds,
    aspect_locked_rect,
    export_graph_image,
    fit_title,
)
from dms.curator.models import CurveData, GraphState, LayerState, PreferenceBounds
from dms.style_tokens import DITHER_TOKENS, THEME_DEFINITIONS
from dms.theme import DARK, DITHER, FASTGRAPH_95_DARK


class _PenRecorder:
    def __init__(self) -> None:
        self.widths: list[float] = []
        self.pens = []

    def setBrush(self, _brush) -> None:
        pass

    def save(self) -> None:
        pass

    def restore(self) -> None:
        pass

    def setRenderHint(self, _hint, _enabled=True) -> None:
        pass

    def setPen(self, pen) -> None:
        if hasattr(pen, "widthF"):
            self.widths.append(pen.widthF())
            self.pens.append(pen)

    def drawPath(self, _path) -> None:
        pass


def test_standard_export_curve_uses_only_solid_stroke(monkeypatch) -> None:
    painter = _PenRecorder()
    curve = CurveData(
        kind="variation",
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-2.0, -1.0]),
        p25_db=np.array([-1.0, 0.0]),
        median_db=np.array([0.0, 1.0]),
        p75_db=np.array([1.0, 2.0]),
        p90_db=np.array([2.0, 3.0]),
    )
    monkeypatch.setattr(export_image_module, "_fill_between", lambda *_args, **_kwargs: None)

    export_image_module._draw_curve_data(
        painter,
        QRectF(0.0, 0.0, 100.0, 100.0),
        curve,
        QColor("#CC3344"),
        -10.0,
        10.0,
        trace_index=1,
        trace_tokens=DITHER_TOKENS,
    )

    assert painter.widths == [pytest.approx(3.0)]
    assert painter.pens[0].style() == Qt.PenStyle.SolidLine
    assert painter.pens[0].dashPattern() == []


def test_export_title_shrinks_then_elides(qapp) -> None:
    short, short_font = fit_title("Short title", 1200)
    long, long_font = fit_title("Very long title " * 40, 500)

    assert short == "Short title"
    assert short_font.pointSize() == 42
    assert long_font.pointSize() == 24
    assert long.endswith("…")


def test_export_legend_expands_and_never_elides_layer_names(qapp) -> None:
    class _Layer:
        color = "#39FF14"

        def __init__(self, name: str) -> None:
            self.name = name

    class _Painter:
        def __init__(self) -> None:
            self.boxes = []
            self.text = []

        def setFont(self, *_args) -> None:
            pass

        def setPen(self, *_args) -> None:
            pass

        def setBrush(self, *_args) -> None:
            pass

        def drawRoundedRect(self, rect, *_args) -> None:
            self.boxes.append(QRectF(rect))

        def drawLine(self, *_args) -> None:
            pass

        def drawText(self, _rect, _flags, text) -> None:
            self.text.append(text)

    long_name = "Unknown Unknown COMP with a complete measurement description"
    painter = _Painter()
    _draw_legend(
        painter,
        QRectF(0, 0, 1800, 700),
        [(_Layer(long_name), None)],
        False,
    )

    assert painter.text == [long_name]
    assert painter.boxes[0].width() > 290


def test_export_graph_image_writes_16_by_9_png(make_curator, qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([source])
    output = tmp_path / "poster.png"

    export_graph_image(window.graph_state, output, size=(1920, 1080))

    image = QImage(str(output))
    assert output.stat().st_size > 0
    assert image.width() == 1920
    assert image.height() == 1080


def test_fastgraph95_dark_export_uses_classic_frame_and_safe_bottom_margin(
    make_curator, qapp, tmp_path: Path
) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator(theme=FASTGRAPH_95_DARK)
    window.import_files([source])
    output = tmp_path / "poster-dark.png"

    export_graph_image(
        window.graph_state,
        output,
        size=(1920, 1080),
        theme=FASTGRAPH_95_DARK,
    )

    image = QImage(str(output))
    assert image.pixelColor(1880, 40).name().upper() == "#315B85"
    assert image.pixelColor(10, 1068).name().upper() == "#3C3C3C"
    assert image.pixelColor(100, 1018).name().upper() != "#202020"
    window.close()


def test_existing_export_themes_keep_matching_classic_and_retro_flags() -> None:
    for definition in THEME_DEFINITIONS:
        if definition.key == DITHER:
            continue
        assert definition.tokens.classic_controls is definition.tokens.retro_graph


def test_dither_export_uses_tokens_and_excludes_gold_accent(
    make_curator, qapp, tmp_path: Path
) -> None:
    source = tmp_path / "dither-curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator(theme=DITHER)
    window.import_files([source])
    window.graph_state.export_text.title = "Dither Export"
    window.graph_state.export_text.fixture = "Fixture"
    output = tmp_path / "poster-dither.png"

    export_graph_image(
        window.graph_state,
        output,
        size=(1920, 1080),
        theme=DITHER,
    )

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.pixelColor(10, 1068).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(0, 0).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(1919, 117).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(100, 124).name().upper() == DITHER_TOKENS.background
    assert image.pixelColor(200, 200).name().upper() == DITHER_TOKENS.plot_bg
    assert image.pixelColor(1800, 1018).name().upper() == DITHER_TOKENS.text

    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    pixels = rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4)
    gold = np.array(QColor(ACCENT_COLOR).getRgb()[:3], dtype=np.uint8)
    assert not np.any(np.all(pixels[:, :, :3] == gold, axis=2))
    window.close()


def test_dither_export_masthead_is_filled_with_knocked_out_text(qapp, tmp_path: Path) -> None:
    state = GraphState()
    state.export_text.title = "Test Masthead"
    state.export_text.fixture = "711 Fixture"
    output = tmp_path / "masthead.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    block = DITHER_TOKENS.text
    knockout = QColor(DITHER_TOKENS.background).rgba()
    assert image.pixelColor(0, 0).name().upper() == block
    assert image.pixelColor(799, 0).name().upper() == block
    assert image.pixelColor(0, 117).name().upper() == block
    assert image.pixelColor(799, 117).name().upper() == block
    assert (
        sum(
            image.pixelColor(x, y).rgba() == knockout
            for y in range(20, 100)
            for x in range(30, 770)
        )
        > 500
    )
    assert image.pixelColor(100, 124).name().upper() == DITHER_TOKENS.background


def test_dither_export_footer_is_square_filled_and_uses_knockout_text(qapp, tmp_path: Path) -> None:
    state = GraphState()
    state.export_text.hrtf_note = "Test Fixture"
    state.export_text.notes = ""
    output = tmp_path / "footer.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    footer_top = image.height() - int(DITHER_FOOTER_HEIGHT)
    ink = QColor(DITHER_TOKENS.text).rgba()
    knockout = QColor(DITHER_TOKENS.background).rgba()

    assert all(image.pixelColor(x, footer_top).rgba() == ink for x in range(image.width()))
    assert all(image.pixelColor(x, image.height() - 1).rgba() == ink for x in range(image.width()))
    assert all(
        image.pixelColor(0, y).rgba() == ink
        and image.pixelColor(image.width() - 1, y).rgba() == ink
        for y in range(footer_top, image.height())
    )

    text_pixels = [
        (x, y)
        for y in range(footer_top + 1, image.height() - 1)
        for x in range(1, image.width() - 1)
        if image.pixelColor(x, y).rgba() == knockout
    ]
    assert text_pixels
    assert 40 <= min(x for x, _y in text_pixels) <= 45
    assert max(x for x, _y in text_pixels) < image.width() - 40
    assert min(y for _x, y in text_pixels) > footer_top
    assert max(y for _x, y in text_pixels) < image.height() - 1


def test_dither_export_bounds_density_varies_from_edge_to_center(qapp, tmp_path: Path) -> None:
    freqs = np.array([20.0, 20000.0])
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.bounds = PreferenceBounds(
        enabled=True,
        upper=CurveData(kind="fr", freqs=freqs, mag_db=np.array([5.0, 5.0])),
        lower=CurveData(kind="fr", freqs=freqs, mag_db=np.array([-5.0, -5.0])),
    )
    output = tmp_path / "bounds-dither.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    ink = QColor(DITHER_TOKENS.plot_grid).rgba()
    plot_rect = _poster_plot_rect(state, (800, 600))
    upper_y = round(_y_for_db(plot_rect, 5.0, state.y_min, state.y_max))
    lower_y = round(_y_for_db(plot_rect, -5.0, state.y_min, state.y_max))
    center_y = round(_y_for_db(plot_rect, 0.0, state.y_min, state.y_max))

    def coverage(y_start: int, y_stop: int) -> int:
        return sum(
            image.pixelColor(x, y).rgba() == ink
            for y in range(y_start, y_stop)
            for x in range(140, 640)
        )

    near_edges = coverage(upper_y + 2, upper_y + 10) + coverage(lower_y - 10, lower_y - 2)
    center = 2 * coverage(center_y - 4, center_y + 4)
    assert near_edges > center * 2


def test_export_uses_gold_accent_constant() -> None:
    assert ACCENT_COLOR == "#FCBE11"


def _poster_plot_rect(state: GraphState, size: tuple[int, int]) -> QRectF:
    """The data rectangle ``_draw_poster`` uses for the given state and size."""
    width, height = size
    frame = QRectF(72, 150, width - 144, height - 280).adjusted(
        PLOT_INSET_LEFT,
        PLOT_INSET_TOP,
        -PLOT_INSET_RIGHT,
        -PLOT_INSET_BOTTOM,
    )
    if not state.aspect_locked_25db:
        return frame
    return aspect_locked_rect(frame, state.y_min, state.y_max)


def test_out_of_band_points_are_dropped_instead_of_clamped() -> None:
    rect = QRectF(0.0, 0.0, 300.0, 100.0)
    freqs = np.array([5.0, 20.0, 1000.0, 20000.0, 48000.0])
    mags = np.array([-40.0, 0.0, 1.0, 0.0, 40.0])

    path = _curve_path(rect, freqs, mags, -10.0, 10.0)

    assert path.elementCount() == 3
    xs = [path.elementAt(index).x for index in range(path.elementCount())]
    assert xs[0] == pytest.approx(rect.left())
    assert xs[-1] == pytest.approx(rect.right())
    # The clamped version drew a vertical spike from the 5 Hz point at x = left.
    ys = [path.elementAt(index).y for index in range(path.elementCount())]
    assert all(rect.top() <= y <= rect.bottom() for y in ys)


def test_out_of_band_points_do_not_spike_the_exported_png(qapp, tmp_path: Path) -> None:
    freqs = np.array([5.0, 20.0, 1000.0, 20000.0, 40000.0])
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.layers.append(
        LayerState(
            curve=CurveData(
                kind="fr",
                freqs=freqs,
                mag_db=np.array([-9.0, 0.0, 0.0, 0.0, 9.0]),
            ),
            source_path=Path("spike.txt"),
            name="Spike",
            color="#ff0000",
        )
    )
    state.show_layer_names = False
    output = tmp_path / "spike.png"

    export_graph_image(state, output, size=(800, 600), theme=DARK)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    plot_rect = _poster_plot_rect(state, (800, 600))
    top = int(plot_rect.top()) + 1
    bottom = int(plot_rect.bottom()) - 1

    def trace_pixels(x: int) -> int:
        return sum(
            1
            for y in range(top, bottom)
            if image.pixelColor(x, y).red() > 150 and image.pixelColor(x, y).green() < 80
        )

    left = int(round(plot_rect.left()))
    right = int(round(plot_rect.right())) - 1
    assert trace_pixels(left) > 0
    assert trace_pixels(right) > 0
    # Clamping the 5 Hz and 40 kHz points onto the edges drew a tall vertical
    # spike there; only the pen width should be lit now.
    assert trace_pixels(left) <= 6
    assert trace_pixels(right) <= 6


def test_poster_honours_the_25db_per_decade_lock(qapp, tmp_path: Path) -> None:
    locked = variation_graph_state(y_min=-10.0, y_max=10.0, aspect_locked_25db=True)
    free = variation_graph_state(y_min=-10.0, y_max=10.0, aspect_locked_25db=False)
    locked.show_layer_names = False
    free.show_layer_names = False
    locked_path = tmp_path / "locked.png"
    free_path = tmp_path / "free.png"

    export_graph_image(locked, locked_path, size=(800, 600), theme=DARK)
    export_graph_image(free, free_path, size=(800, 600), theme=DARK)

    assert locked_path.read_bytes() != free_path.read_bytes()
    locked_rect = _poster_plot_rect(locked, (800, 600))
    free_rect = _poster_plot_rect(free, (800, 600))
    assert locked_rect.height() < free_rect.height()
    # 25 dB must span exactly one decade of width.
    db_per_pixel = (locked.y_max - locked.y_min) / locked_rect.height()
    decades_per_pixel = 3.0 / locked_rect.width()
    assert db_per_pixel / decades_per_pixel == pytest.approx(25.0, rel=1e-6)


def test_aspect_lock_widens_instead_of_cropping_a_tall_db_range() -> None:
    rect = QRectF(0.0, 0.0, 900.0, 300.0)

    locked = aspect_locked_rect(rect, -60.0, 60.0)

    assert locked.height() == pytest.approx(rect.height())
    assert locked.width() < rect.width()
    assert locked.center().x() == pytest.approx(rect.center().x())


def test_bounds_renderers_interpolate_the_lower_bound_onto_the_upper_grid() -> None:
    upper = CurveData(
        kind="fr",
        freqs=np.array([20.0, 200.0, 2000.0, 20000.0]),
        mag_db=np.array([5.0, 5.0, 5.0, 5.0]),
    )
    lower = CurveData(
        kind="fr",
        freqs=np.array([20.0, 20000.0]),
        mag_db=np.array([-5.0, -1.0]),
    )

    freqs, upper_values, lower_values = aligned_bounds(upper, lower)

    assert np.array_equal(freqs, upper.freqs)
    assert np.array_equal(upper_values, upper.mag_db)
    assert lower_values.shape == freqs.shape
    assert np.allclose(lower_values, np.interp(upper.freqs, lower.freqs, lower.mag_db))
    assert lower_values[0] == pytest.approx(-5.0)
    assert lower_values[-1] == pytest.approx(-1.0)


def test_mismatched_bounds_grids_no_longer_zip_by_index(qapp, tmp_path: Path) -> None:
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.show_layer_names = False
    state.bounds = PreferenceBounds(
        enabled=True,
        upper=CurveData(
            kind="fr",
            freqs=np.array([20.0, 200.0, 2000.0, 20000.0]),
            mag_db=np.array([5.0, 5.0, 5.0, 5.0]),
        ),
        lower=CurveData(
            kind="fr",
            freqs=np.array([20.0, 20000.0]),
            mag_db=np.array([-5.0, -5.0]),
        ),
    )
    output = tmp_path / "bounds.png"

    export_graph_image(state, output, size=(800, 600), theme=DARK)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    plot_rect = _poster_plot_rect(state, (800, 600))
    right = int(round(plot_rect.right())) - 3
    inside = int(round(_y_for_db(plot_rect, -4.0, state.y_min, state.y_max)))
    outside = int(round(_y_for_db(plot_rect, -8.0, state.y_min, state.y_max)))
    background = image.pixelColor(right, outside)
    band = image.pixelColor(right, inside)
    assert band != background
