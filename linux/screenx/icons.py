# SPDX-License-Identifier: GPL-3.0-only
"""Line icons drawn with QPainter on a 20 x 20 grid, so the bars look the same on every desktop theme."""
from __future__ import annotations

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _poly(p: QPainter, *pts, closed: bool = False) -> None:
    poly = QPolygonF([QPointF(x, y) for x, y in pts])
    if closed:
        p.drawPolygon(poly)
    else:
        p.drawPolyline(poly)


def _line(p: QPainter, x1, y1, x2, y2) -> None:
    p.drawLine(QLineF(x1, y1, x2, y2))


def _filled(p: QPainter, color: QColor):
    p.setBrush(color)


def _rectangle(p, c):
    p.drawRoundedRect(QRectF(3, 5, 14, 10), 1.5, 1.5)


def _window(p, c):
    p.drawRoundedRect(QRectF(3, 4, 14, 12), 1.5, 1.5)
    _line(p, 3, 8, 17, 8)


def _fullscreen(p, c):
    _poly(p, (3, 7), (3, 3), (7, 3))
    _poly(p, (13, 3), (17, 3), (17, 7))
    _poly(p, (17, 13), (17, 17), (13, 17))
    _poly(p, (7, 17), (3, 17), (3, 13))


def _freeform(p, c):
    path = QPainterPath(QPointF(4, 11))
    path.cubicTo(3, 4, 11, 2, 13, 6)
    path.cubicTo(18, 6, 18, 14, 13, 15)
    path.cubicTo(9, 19, 4, 16, 4, 11)
    p.drawPath(path)


def _snip(p, c):
    p.drawRoundedRect(QRectF(3, 6, 14, 10), 2, 2)
    p.drawEllipse(QPointF(10, 11), 3, 3)
    _poly(p, (7, 6), (8, 4), (12, 4), (13, 6))


def _record(p, c):
    p.drawRoundedRect(QRectF(2, 6, 11, 8), 2, 2)
    _poly(p, (13, 9.5), (18, 7), (18, 13), (13, 10.5), closed=True)


def _markup(p, c):
    _line(p, 4.5, 14.5, 13, 6)
    _line(p, 13, 6, 15, 4)
    path = QPainterPath(QPointF(10, 16))
    path.quadTo(12, 13.5, 14, 16)
    path.quadTo(16, 18, 18, 15.5)
    p.drawPath(path)


def _pen(p, c):
    _poly(p, (4, 16), (5, 12), (13, 4), (16, 7), (8, 15), closed=True)


def _highlighter(p, c):
    pen = QPen(p.pen())
    pen.setWidthF(5)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.save()
    p.setPen(pen)
    _line(p, 7, 13, 13, 5)
    p.restore()
    _line(p, 4, 17, 16, 17)


def _eraser(p, c):
    _poly(p, (3, 12.5), (9, 5), (16, 9.5), (11, 16), (6.5, 16), closed=True)
    _line(p, 6, 8.8, 13.3, 13.2)
    _line(p, 11, 16, 17, 16)


def _shapes(p, c):
    p.drawRoundedRect(QRectF(3, 3, 9, 9), 1, 1)
    p.drawEllipse(QPointF(13, 13), 4.5, 4.5)


def _ellipse(p, c):
    p.drawEllipse(QRectF(3, 5, 14, 10))


def _line_icon(p, c):
    _line(p, 4, 16, 16, 4)


def _arrow(p, c):
    _line(p, 4, 16, 16, 4)
    _poly(p, (9, 4), (16, 4), (16, 11))


def _undo(p, c, flip: bool = False):
    def x(v):
        return 20 - v if flip else v
    path = QPainterPath(QPointF(x(4), 8))
    path.lineTo(x(12.5), 8)
    path.cubicTo(x(18.5), 8, x(18.5), 16, x(12.5), 16)
    path.lineTo(x(8), 16)
    p.drawPath(path)
    _poly(p, (x(7.5), 4.5), (x(4), 8), (x(7.5), 11.5))


def _redo(p, c):
    _undo(p, c, flip=True)


def _trash(p, c):
    _line(p, 4, 6, 16, 6)
    _poly(p, (8, 6), (8, 4), (12, 4), (12, 6))
    _poly(p, (5.5, 6), (6.5, 17), (13.5, 17), (14.5, 6))
    _line(p, 8.5, 9, 8.5, 14)
    _line(p, 11.5, 9, 11.5, 14)


def _close(p, c):
    _line(p, 5, 5, 15, 15)
    _line(p, 15, 5, 5, 15)


def _check(p, c):
    _poly(p, (4, 10.5), (8.5, 15), (16, 5.5))


def _lens(p, c):
    p.drawEllipse(QPointF(9, 9), 5, 5)
    _line(p, 12.8, 12.8, 17, 17)


def _picker(p, c):
    _poly(p, (4, 16), (4, 13.5), (10.5, 7), (13, 9.5), (6.5, 16), closed=True)
    _line(p, 9.5, 5, 15, 10.5)
    pen = QPen(p.pen())
    pen.setWidthF(3.2)
    p.save()
    p.setPen(pen)
    _line(p, 13, 7, 15.5, 4.5)
    p.restore()


def _text(p, c):
    _line(p, 5, 5, 15, 5)
    _line(p, 10, 5, 10, 15)
    _line(p, 8, 15, 12, 15)
    _line(p, 5, 5, 5, 7)
    _line(p, 15, 5, 15, 7)


def _copy(p, c):
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 1.5, 1.5)
    _poly(p, (13, 4), (4, 4), (4, 13))


def _save(p, c):
    _line(p, 10, 3, 10, 12)
    _poly(p, (6, 8.5), (10, 12.5), (14, 8.5))
    _poly(p, (4, 13), (4, 17), (16, 17), (16, 13))


def _mic(p, c):
    p.drawRoundedRect(QRectF(7.5, 3, 5, 9), 2.5, 2.5)
    path = QPainterPath(QPointF(5, 10))
    path.cubicTo(5, 16, 15, 16, 15, 10)
    p.drawPath(path)
    _line(p, 10, 14.5, 10, 17)


def _mic_off(p, c):
    _mic(p, c)
    _line(p, 4, 4, 16, 16)


def _speaker_body(p):
    _poly(p, (3, 8), (6, 8), (10, 4.5), (10, 15.5), (6, 12), (3, 12), closed=True)


def _speaker(p, c):
    _speaker_body(p)
    path = QPainterPath(QPointF(12.5, 7.5))
    path.quadTo(14.3, 10, 12.5, 12.5)
    p.drawPath(path)
    path = QPainterPath(QPointF(14.5, 5))
    path.quadTo(18.3, 10, 14.5, 15)
    p.drawPath(path)


def _speaker_off(p, c):
    _speaker(p, c)
    _line(p, 4, 4, 16, 16)  # crossed out like "mic-off"


def _pause(p, c):
    _filled(p, c)
    p.drawRoundedRect(QRectF(5.5, 5, 3, 10), 1, 1)
    p.drawRoundedRect(QRectF(11.5, 5, 3, 10), 1, 1)


def _play(p, c):
    _filled(p, c)
    _poly(p, (6.5, 4.5), (15.5, 10), (6.5, 15.5), closed=True)


def _stop(p, c):
    _filled(p, c)
    p.drawRoundedRect(QRectF(5.5, 5.5, 9, 9), 1.5, 1.5)


def _dot(p, c):
    _filled(p, c)
    p.drawEllipse(QPointF(10, 10), 5, 5)


def _settings(p, c):
    _line(p, 3, 6, 17, 6)
    _line(p, 3, 14, 17, 14)
    _filled(p, c)
    p.drawEllipse(QPointF(7, 6), 2, 2)
    p.drawEllipse(QPointF(13, 14), 2, 2)


def _crop(p, c):
    _poly(p, (6, 2), (6, 14), (18, 14))
    _poly(p, (2, 6), (14, 6), (14, 18))


def _plus(p, c):
    _line(p, 10, 4, 10, 16)
    _line(p, 4, 10, 16, 10)


def _chevron(p, c):
    _poly(p, (6, 8), (10, 12), (14, 8))


def _folder(p, c):
    _poly(p, (3, 6), (8, 6), (10, 8), (17, 8), (17, 16), (3, 16), closed=True)


_DRAW = {
    "rectangle": _rectangle, "window": _window, "fullscreen": _fullscreen, "freeform": _freeform,
    "snip": _snip, "record": _record, "markup": _markup, "pen": _pen, "highlighter": _highlighter,
    "eraser": _eraser, "shapes": _shapes, "ellipse": _ellipse, "line": _line_icon, "arrow": _arrow,
    "undo": _undo, "redo": _redo, "trash": _trash, "close": _close, "check": _check, "lens": _lens,
    "picker": _picker, "text": _text, "copy": _copy, "save": _save, "mic": _mic, "mic-off": _mic_off,
    "speaker": _speaker, "speaker-off": _speaker_off,
    "pause": _pause, "play": _play, "stop": _stop, "dot": _dot, "settings": _settings, "crop": _crop,
    "plus": _plus, "chevron": _chevron, "folder": _folder,
}


def pixmap(name: str, color="#FFFFFF", size: int = 20) -> QPixmap:
    scale = 3  # drawn large so it stays sharp on high-DPI screens
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    c = QColor(color)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * scale / 20.0, size * scale / 20.0)
    p.setPen(QPen(c, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    _DRAW[name](p, c)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color="#FFFFFF", size: int = 20) -> QIcon:
    return QIcon(pixmap(name, color, size))


def colour_dot(color, size: int = 20) -> QIcon:
    """Filled colour dot with a light ring, for the colour button."""
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * scale / 20.0, size * scale / 20.0)
    p.setPen(QPen(QColor(255, 255, 255, 220), 1.4))
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(10, 10), 6.5, 6.5)
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


def stroke_icon(width: float, color="#FFFFFF") -> QIcon:
    """Horizontal line `width` thick, for the size menu."""
    scale = 3
    pm = QPixmap(40 * scale, 20 * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    p.setPen(QPen(QColor(color), min(width, 14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QLineF(8, 10, 32, 10))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)
