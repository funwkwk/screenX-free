# SPDX-License-Identifier: GPL-3.0-only
"""Mark-ups (pen, highlighter, shapes) and the resizable-selection helpers shared by the overlay,
the editor and the recording area."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

PALETTE = [
    "#000000", "#404040", "#808080", "#BFBFBF", "#FFFFFF", "#7F1D1D",
    "#E81123", "#FF5A36", "#FF8C00", "#FFB900", "#FCE100", "#BAD80A",
    "#107C10", "#00B294", "#00B7C3", "#0099BC", "#0078D7", "#0063B1",
    "#4F6BED", "#8764B8", "#B146C2", "#E3008C", "#FF6FAE", "#8E562E",
    "#A0522D", "#C19A6B", "#F7C8A0", "#5D5A58", "#2D7D9A", "#00CC6A",
]
SIZES = [2, 4, 6, 8, 12]
SHAPES = [("rectangle", "Rectangle"), ("ellipse", "Ellipse"), ("line", "Line"), ("arrow", "Arrow")]
FREEHAND = ("pen", "highlighter")


@dataclass
class Mark:
    """One mark-up: a freehand stroke or a two-point shape. Coordinates are image points, top-left origin."""

    kind: str  # pen | highlighter | rectangle | ellipse | line | arrow
    points: list = field(default_factory=list)
    color: QColor = field(default_factory=lambda: QColor("#E81123"))
    width: float = 4.0

    def bounds(self) -> QRectF:
        if not self.points:
            return QRectF()
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def moved(self, dx: float, dy: float) -> "Mark":
        return Mark(self.kind, [QPointF(p.x() + dx, p.y() + dy) for p in self.points], QColor(self.color), self.width)

    def is_shape(self) -> bool:
        return self.kind not in FREEHAND

    def draw(self, p: QPainter) -> None:
        if not self.points:
            return
        a, b = self.points[0], self.points[-1]
        pen = QPen(self.color, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind in FREEHAND:
            path = QPainterPath(a)
            for pt in self.points[1:]:
                path.lineTo(pt)
            if len(self.points) == 1:
                path.lineTo(QPointF(a.x() + 0.1, a.y() + 0.1))
            if self.kind == "highlighter":
                c = QColor(self.color)
                c.setAlphaF(0.4)
                pen.setColor(c)
                pen.setCapStyle(Qt.PenCapStyle.SquareCap)
            p.setPen(pen)
            p.drawPath(path)
        elif self.kind == "rectangle":
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            p.setPen(pen)
            p.drawRect(self.bounds())
        elif self.kind == "ellipse":
            p.setPen(pen)
            p.drawEllipse(self.bounds())
        elif self.kind == "line":
            p.setPen(pen)
            p.drawLine(QLineF(a, b))
        elif self.kind == "arrow":
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            length = math.hypot(b.x() - a.x(), b.y() - a.y())
            head = min(length, max(12.0, self.width * 3.5))
            base = QPointF(b.x() - head * 0.8 * math.cos(ang), b.y() - head * 0.8 * math.sin(ang))
            p.setPen(pen)
            p.drawLine(QLineF(a, base))
            tip = QPolygonF([
                b,
                QPointF(b.x() - head * math.cos(ang - 0.42), b.y() - head * math.sin(ang - 0.42)),
                QPointF(b.x() - head * math.cos(ang + 0.42), b.y() - head * math.sin(ang + 0.42)),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color)
            p.drawPolygon(tip)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def hit(self, pt: QPointF) -> bool:
        """Rough hit test for the eraser."""
        slack = max(8.0, self.width)
        if self.kind in FREEHAND:
            return any(math.hypot(q.x() - pt.x(), q.y() - pt.y()) <= slack + 2 for q in self.points)
        return self.bounds().adjusted(-slack, -slack, slack, slack).contains(pt)


def new_mark(tool: str, pt: QPointF, pen_color: QColor, highlighter_color: QColor, stroke: float) -> Mark | None:
    if tool == "pen":
        return Mark("pen", [QPointF(pt)], QColor(pen_color), stroke)
    if tool == "highlighter":
        return Mark("highlighter", [QPointF(pt)], QColor(highlighter_color), max(stroke * 4, 14))
    if tool in dict(SHAPES):
        return Mark(tool, [QPointF(pt), QPointF(pt)], QColor(pen_color), stroke)
    return None


def extend_mark(mark: Mark, pt: QPointF, square: bool) -> None:
    """Mouse drag: add to a stroke or move the second point of a shape (Shift = square / circle)."""
    if mark.kind in FREEHAND:
        mark.points.append(QPointF(pt))
        return
    if square and mark.kind in ("rectangle", "ellipse"):
        a = mark.points[0]
        side = max(abs(pt.x() - a.x()), abs(pt.y() - a.y()))
        pt = QPointF(a.x() + (side if pt.x() >= a.x() else -side), a.y() + (side if pt.y() >= a.y() else -side))
    mark.points[1] = QPointF(pt)


def worth_keeping(mark: Mark) -> bool:
    b = mark.bounds()
    return not mark.is_shape() or b.width() >= 3 or b.height() >= 3


# ---------------------------------------------------------------- selection

HANDLE_SLACK = 8.0
MIN_SIZE = 16.0


def edge_hit(p: QPointF, r: QRectF, slack: float = HANDLE_SLACK):
    """Which edges of `r` are under `p`: (left, right, top, bottom), or None."""
    if not r.adjusted(-slack, -slack, slack, slack).contains(p):
        return None
    left = abs(p.x() - r.left()) <= slack
    right = abs(p.x() - r.right()) <= slack
    top = abs(p.y() - r.top()) <= slack
    bottom = abs(p.y() - r.bottom()) <= slack
    if left and right:  # tiny rectangle: take the nearer side
        left = abs(p.x() - r.left()) <= abs(p.x() - r.right())
        right = not left
    if top and bottom:
        top = abs(p.y() - r.top()) <= abs(p.y() - r.bottom())
        bottom = not top
    return (left, right, top, bottom) if (left or right or top or bottom) else None


def edge_cursor(edges) -> Qt.CursorShape:
    left, right, top, bottom = edges
    if (left and top) or (right and bottom):
        return Qt.CursorShape.SizeFDiagCursor
    if (right and top) or (left and bottom):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


def resize_rect(r: QRectF, edges, p: QPointF, limit: QRectF) -> QRectF:
    left, right, top, bottom = edges
    x1, y1, x2, y2 = r.left(), r.top(), r.right(), r.bottom()
    px = min(max(p.x(), limit.left()), limit.right())
    py = min(max(p.y(), limit.top()), limit.bottom())
    if left:
        x1 = min(px, x2 - MIN_SIZE)
    if right:
        x2 = max(px, x1 + MIN_SIZE)
    if top:
        y1 = min(py, y2 - MIN_SIZE)
    if bottom:
        y2 = max(py, y1 + MIN_SIZE)
    return QRectF(QPointF(x1, y1), QPointF(x2, y2))


def rect_from(a: QPointF, b: QPointF, limit: QRectF) -> QRectF:
    return QRectF(QPointF(min(a.x(), b.x()), min(a.y(), b.y())),
                  QPointF(max(a.x(), b.x()), max(a.y(), b.y()))).intersected(limit)


def draw_dim(p: QPainter, area: QRectF, hole: QRectF | None, alpha: int = 115) -> None:
    path = QPainterPath()
    path.addRect(area)
    if hole is not None and not hole.isEmpty():
        path.addRect(hole)
    path.setFillRule(Qt.FillRule.OddEvenFill)
    p.fillPath(path, QColor(0, 0, 0, alpha))


def draw_handles(p: QPainter, r: QRectF, color: QColor = QColor("white")) -> None:
    """Thin frame, four corner brackets and four edge bars."""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(color, 1.2))
    p.drawRect(r)
    pen = QPen(color, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    arm = min(16.0, r.width() / 3, r.height() / 3)
    for cx, cy, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                           (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
        p.drawPolyline(QPolygonF([QPointF(cx + dx * arm, cy), QPointF(cx, cy), QPointF(cx, cy + dy * arm)]))
    bar = min(14.0, r.width() / 6, r.height() / 6)
    if r.width() >= 90:
        p.drawLine(QLineF(r.center().x() - bar, r.top(), r.center().x() + bar, r.top()))
        p.drawLine(QLineF(r.center().x() - bar, r.bottom(), r.center().x() + bar, r.bottom()))
    if r.height() >= 90:
        p.drawLine(QLineF(r.left(), r.center().y() - bar, r.left(), r.center().y() + bar))
        p.drawLine(QLineF(r.right(), r.center().y() - bar, r.right(), r.center().y() + bar))


def draw_pill(p: QPainter, text: str, centre: QPointF | None = None, origin: QPointF | None = None,
              size: int = 12) -> None:
    """White text on a dark rounded label, centred on `centre` or with its top-left at `origin`."""
    font = p.font()
    font.setPixelSize(size)
    p.setFont(font)
    fm = p.fontMetrics()
    w = fm.horizontalAdvance(text) + 18
    h = fm.height() + 8
    if centre is not None:
        box = QRectF(centre.x() - w / 2, centre.y() - h / 2, w, h)
    else:
        o = origin or QPointF(0, 0)
        box = QRectF(o.x(), o.y(), w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(20, 20, 20, 215))
    p.drawRoundedRect(box, h / 2, h / 2)
    p.setPen(QColor("white"))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
