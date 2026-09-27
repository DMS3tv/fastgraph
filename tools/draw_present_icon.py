"""Draw the FastGraph Present icon: the FastGraph tile with a whiteboard, traces, a marked-up annotation and a pen."""

import math
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication

app = QApplication([])
N = 1024
img = QImage(N, N, QImage.Format.Format_ARGB32_Premultiplied)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)


def rrect(x, y, w, h, r):
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


# Tile: matches the FastGraph icon (dark, rounded, subtle rim)
tile = rrect(44, 44, N - 88, N - 88, 210)
g = QLinearGradient(0, 44, 0, N - 44)
g.setColorAt(0, QColor("#1d2027"))
g.setColorAt(1, QColor("#0e1014"))
p.fillPath(tile, QBrush(g))
p.setPen(QPen(QColor(255, 255, 255, 34), 6))
p.setBrush(Qt.BrushStyle.NoBrush)
p.drawPath(rrect(47, 47, N - 94, N - 94, 207))

# Whiteboard: off-white board with a soft shadow and a thin aluminium frame
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QColor(0, 0, 0, 110))
p.drawPath(rrect(150, 214, 724, 560, 30))
frame = rrect(138, 198, 748, 584, 34)
fg = QLinearGradient(0, 198, 0, 782)
fg.setColorAt(0, QColor("#c9ccd2"))
fg.setColorAt(1, QColor("#8f949c"))
p.fillPath(frame, QBrush(fg))
board = rrect(160, 220, 704, 540, 22)
bg = QLinearGradient(160, 220, 864, 760)
bg.setColorAt(0, QColor("#f7f6f1"))
bg.setColorAt(1, QColor("#e6e5df"))
p.fillPath(board, QBrush(bg))
# faint grid like the app's plot
p.setPen(QPen(QColor(0, 0, 0, 22), 3))
for x in range(232, 864, 88):
    p.drawLine(x, 232, x, 748)
for y in range(292, 748, 78):
    p.drawLine(172, y, 852, y)


def trace(points, color, width):
    path = QPainterPath(QPointF(*points[0]))
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        cx = (x0 + x1) / 2
        path.cubicTo(QPointF(cx, y0), QPointF(cx, y1), QPointF(x1, y1))
    glow = QColor(color)
    glow.setAlpha(70)
    p.setPen(
        QPen(
            glow,
            width * 3.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(
        QPen(
            QColor(color),
            width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    p.drawPath(path)


# Two traces in the FastGraph palette
trace(
    [
        (196, 560),
        (300, 540),
        (400, 470),
        (500, 500),
        (590, 420),
        (690, 300),
        (790, 340),
        (836, 300),
    ],
    "#00e5ff",
    16,
)
trace(
    [
        (196, 640),
        (300, 600),
        (400, 560),
        (500, 590),
        (590, 520),
        (690, 420),
        (790, 470),
        (836, 430),
    ],
    "#ff7a3d",
    16,
)

# Marker annotations in the presenter yellow: a hand-drawn circle around the peak and a tape bracket
marker = QColor("#FCBE11")
p.setPen(QPen(marker, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
p.setBrush(Qt.BrushStyle.NoBrush)
circle = QPainterPath(QPointF(650, 262))
pts = [
    (
        690 + 84 * math.cos(t) * (1 + 0.06 * math.sin(3 * t)),
        320 + 66 * math.sin(t) * (1 + 0.05 * math.cos(2 * t)),
    )
    for t in [i * 2 * math.pi / 40 for i in range(43)]
]
circle = QPainterPath(QPointF(*pts[0]))
for q in pts[1:]:
    circle.lineTo(QPointF(*q))
p.drawPath(circle)
p.setPen(QPen(marker, 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
p.drawLine(QPointF(500, 500), QPointF(500, 590))
p.drawLine(QPointF(478, 500), QPointF(522, 500))
p.drawLine(QPointF(478, 590), QPointF(522, 590))

# Pen: a marker at ~35 degrees resting across the lower-right corner of the board
p.save()
p.translate(700, 720)
p.rotate(-38)
body = rrect(-250, -34, 380, 68, 28)
pb = QLinearGradient(0, -34, 0, 34)
pb.setColorAt(0, QColor("#3a3d46"))
pb.setColorAt(1, QColor("#15171c"))
p.setPen(QPen(QColor(0, 0, 0, 120), 4))
p.fillPath(body, QBrush(pb))
p.drawPath(body)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(marker)
p.drawPath(rrect(60, -34, 80, 68, 10))  # cap band
tip = QPainterPath(QPointF(130, -22))
tip.lineTo(QPointF(200, -6))
tip.lineTo(QPointF(200, 6))
tip.lineTo(QPointF(130, 22))
tip.closeSubpath()
p.setBrush(QColor("#d9d9d9"))
p.drawPath(tip)
p.setBrush(marker)
p.drawEllipse(QPointF(203, 0), 9, 9)
p.restore()

p.end()
out = sys.argv[1]
img.save(out)
print("saved", out)
