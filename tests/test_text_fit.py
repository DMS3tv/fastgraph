from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QFont, QFontDatabase

from dms.curator.text_fit import fit_text


def test_long_single_line_text_shrinks_before_warning(qapp) -> None:
    target = QRectF(0.0, 0.0, 600.0, 120.0)
    font = QFont("Arial")
    font.setWeight(QFont.Weight.ExtraBold)
    font.setItalic(True)
    fitted = fit_text(
        "A VERY LONG HEADPHONE NAME WITH MANY CONFIGURATION DETAILS",
        font,
        target,
        maximum_pixel_size=90,
        minimum_readable_pixel_size=18,
        field_name="Main title",
    )

    assert fitted.fits
    assert fitted.font.pixelSize() < 90
    assert fitted.bounds.width() <= target.width()
    assert fitted.bounds.height() <= target.height()


def test_two_line_text_checks_height_and_width(qapp) -> None:
    target = QRectF(0.0, 0.0, 360.0, 100.0)
    fitted = fit_text(
        "Frequency Response With Headphone Transfer Function Variation",
        QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont),
        target,
        maximum_pixel_size=38,
        minimum_readable_pixel_size=14,
        line_limit=2,
        field_name="Variation legend",
    )

    assert fitted.fits
    assert fitted.bounds.width() <= target.width()
    assert fitted.bounds.height() <= target.height()
