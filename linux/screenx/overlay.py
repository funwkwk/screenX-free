# SPDX-License-Identifier: GPL-3.0-only
"""The snipping overlay: a full-screen window showing the frozen screenshot, the top toolbar, the
selection with its handles and the quick-markup bar. Coordinates are widget (logical) points."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (QColorDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QMenu, QToolButton,
                             QWidget, QWidgetAction)

from . import icons
from .canvas import (PALETTE, SHAPES, SIZES, Mark, draw_dim, draw_handles, draw_pill, edge_cursor, edge_hit,
                     extend_mark, new_mark, rect_from, resize_rect, worth_keeping)
from .common import colour_formats, copy_image, copy_text, save_image, toast, with_border
from .prefs import SNIP_MODES, Prefs, unique_file

ACCENT = QColor("#4CC2FF")

BAR_QSS = """
QFrame#bar { background: rgba(30,30,30,240); border: 1px solid rgba(255,255,255,45); border-radius: 12px; }
QToolButton { background: transparent; border: none; border-radius: 7px; padding: 5px; color: white; font-size: 13px; }
QToolButton:hover { background: rgba(255,255,255,38); }
QToolButton:checked { background: rgba(255,255,255,72); }
QToolButton:disabled { background: transparent; color: rgba(255,255,255,90); }
QToolButton::menu-indicator { image: none; }
"""

MENU_QSS = """
QMenu { background: #262626; color: white; border: 1px solid rgba(255,255,255,45); border-radius: 8px; padding: 5px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: rgba(255,255,255,45); }
QMenu::separator { height: 1px; background: rgba(255,255,255,40); margin: 4px 6px; }
"""


def bar_button(name: str, tip: str, handler=None, checkable: bool = False, text: str | None = None) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(name))
    b.setIconSize(QSize(20, 20))
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the overlay keeps the keyboard (Esc, Ctrl+Enter, arrows)
    b.setCursor(Qt.CursorShape.ArrowCursor)
    b.setCheckable(checkable)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if handler:
        b.clicked.connect(lambda _=False: handler())
    return b


def bar_separator() -> QFrame:
    s = QFrame()
    s.setFixedSize(1, 20)
    s.setStyleSheet("background: rgba(255,255,255,55);")
    return s


class Bar(QFrame):
    """Dark rounded strip of buttons floating over the overlay."""

    def __init__(self, parent: QWidget, widgets: list):
        super().__init__(parent)
        self.setObjectName("bar")
        self.setStyleSheet(BAR_QSS)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 5, 6, 5)
        row.setSpacing(2)
        for w in widgets:
            row.addWidget(w)
        self.adjustSize()

    def mousePressEvent(self, event):  # clicks between buttons must not start a selection underneath
        event.accept()


def colour_menu(parent: QWidget, on_pick) -> QMenu:
    """30 colour chips plus "More colours…"."""
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_QSS)
    holder = QWidget()
    grid = QGridLayout(holder)
    grid.setContentsMargins(6, 6, 6, 6)
    grid.setSpacing(5)
    for i, hex_ in enumerate(PALETTE):
        chip = QToolButton()
        chip.setFixedSize(22, 22)
        chip.setToolTip(hex_)
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chip.setStyleSheet(f"QToolButton {{ background: {hex_}; border: 1px solid rgba(255,255,255,90); border-radius: 11px; }}"
                           "QToolButton:hover { border: 2px solid white; }")
        chip.clicked.connect(lambda _=False, c=hex_: (menu.close(), on_pick(QColor(c))))
        grid.addWidget(chip, i // 6, i % 6)
    action = QWidgetAction(menu)
    action.setDefaultWidget(holder)
    menu.addAction(action)
    menu.addSeparator()

    def more():
        c = QColorDialog.getColor(QColor("#E81123"), parent, "Colour")
        if c.isValid():
            on_pick(c)

    menu.addAction("More colours…", more)
    return menu


def size_menu(parent: QWidget, current: float, on_pick) -> QMenu:
    menu = QMenu(parent)
    menu.setStyleSheet(MENU_QSS)
    for s in SIZES:
        a = menu.addAction(icons.stroke_icon(s), f"{s} px", lambda s=s: on_pick(float(s)))
        a.setCheckable(True)
        a.setChecked(abs(current - s) < 0.01)
    return menu


class Overlay(QWidget):
    active: "Overlay | None" = None

    def __init__(self, screen, shot: QImage, mode: str = "rectangle", text: bool = False, color: bool = False,
                 window_source=None, on_capture=None, on_cancel=None, on_record=None, on_text=None,
                 on_lens=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("screenX")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.target_screen = screen
        # raw pixels; self.k() maps widget points to them
        self.shot = shot.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.shot.setDevicePixelRatio(1)
        self.mode = mode if mode in dict(SNIP_MODES) and mode != "fullscreen" else "rectangle"
        self.text = text
        self.color = color
        self.window_source = window_source  # callable giving the window rectangles; asked only for window snips
        self.windows: list = []
        self.on_capture, self.on_cancel, self.on_record = on_capture, on_cancel, on_record
        self.on_text, self.on_lens = on_text, on_lens
        self.done = False

        self.phase = "pick"            # pick | markup
        self.sel: QRectF | None = None
        self.drag = None
        self.free: list = []           # freeform outline
        self.hover_win: QRectF | None = None
        self.cursor_pos = QPointF(-100, -100)

        self.marks: list = []
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.current: Mark | None = None
        self.tool = "pen"
        self.shape = "rectangle"
        self.pen_color = QColor("#E81123")
        self.highlighter_color = QColor("#FCE100")
        self.stroke = 4.0

        self._build_top_bar()
        self._build_markup_bar()
        self._refresh()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._load_windows()

    def _load_windows(self) -> None:
        if self.mode == "window" and not self.windows and self.window_source is not None:
            self.windows = list(self.window_source() or [])

    # ================================================================ window

    def open(self) -> None:
        Overlay.active = self
        if self.target_screen is not None:
            self.setScreen(self.target_screen)
            self.setGeometry(self.target_screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def k(self) -> float:
        """Screenshot pixels per widget point."""
        return self.shot.width() / max(1, self.width())

    def bounds(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_bars()

    def closeEvent(self, event):
        if Overlay.active is self:
            Overlay.active = None
        super().closeEvent(event)

    def cancel(self) -> None:
        cb = None if self.done else self.on_cancel
        self.done = True
        self.close()
        if cb:
            cb()

    def restart(self) -> None:
        """Back to the start: top toolbar only, ready to pick a new area."""
        self.phase, self.sel, self.drag, self.current = "pick", None, None, None
        self.free, self.marks, self.undo_stack, self.redo_stack = [], [], [], []
        self._refresh()
        self.update()

    # ================================================================ bars

    def _build_top_bar(self) -> None:
        self.b_snip = bar_button("snip", "Snip", lambda: self._set_special(None), checkable=True)
        self.b_record = bar_button("record", "Record the screen", self._record)
        self.b_mode = bar_button(self.mode, "Snip mode", self._show_mode_menu)
        self.b_quick = bar_button("markup", "Quick markup after a rectangle snip", self._toggle_quick, checkable=True)
        self.b_color = bar_button("picker", "Colour picker", lambda: self._set_special("color"), checkable=True)
        self.b_text = bar_button("text", "Text extractor", lambda: self._set_special("text"), checkable=True)
        b_close = bar_button("close", "Close (Esc)", self.cancel)
        self.top_bar = Bar(self, [self.b_snip, self.b_record, bar_separator(), self.b_mode, self.b_quick,
                                  bar_separator(), self.b_color, self.b_text, bar_separator(), b_close])

    def _build_markup_bar(self) -> None:
        self.b_capture = bar_button("check", "Capture (Ctrl+Enter). Click for more.", self._show_capture_menu, text="Capture")
        b_lens = bar_button("lens", "Search with Google Lens", self._lens)
        self.tool_buttons = {
            "pen": bar_button("pen", "Pen", lambda: self._pick_tool("pen"), checkable=True),
            "highlighter": bar_button("highlighter", "Highlighter", lambda: self._pick_tool("highlighter"), checkable=True),
            "eraser": bar_button("eraser", "Eraser (click again to erase all mark-ups)", lambda: self._pick_tool("eraser"), checkable=True),
            "shape": bar_button("shapes", "Shapes", self._show_shape_menu, checkable=True),
        }
        self.b_colour = bar_button("dot", "Colour", self._show_colour_menu)
        self.b_size = bar_button("line", "Size", self._show_size_menu)
        self.b_undo = bar_button("undo", "Undo (Ctrl+Z)", self.undo)
        self.b_redo = bar_button("redo", "Redo (Ctrl+Y)", self.redo)
        b_discard = bar_button("trash", "Discard (Esc)", self.restart)
        self.markup_bar = Bar(self, [self.b_capture, b_lens, bar_separator(), *self.tool_buttons.values(),
                                     bar_separator(), self.b_colour, self.b_size, bar_separator(),
                                     self.b_undo, self.b_redo, bar_separator(), b_discard])
        self.markup_bar.hide()

    def _refresh(self) -> None:
        """Syncs the buttons with the state."""
        special = "color" if self.color else "text" if self.text else None
        self.b_snip.setChecked(special is None)
        self.b_color.setChecked(special == "color")
        self.b_text.setChecked(special == "text")
        self.b_mode.setIcon(icons.icon(self.mode))
        self.b_mode.setToolTip("Snip mode: " + dict(SNIP_MODES)[self.mode])
        self.b_mode.setEnabled(special is None)
        self.b_quick.setEnabled(special is None)
        self.b_quick.setChecked(bool(Prefs.get("quickMarkup")))
        shape_tool = self.tool in dict(SHAPES)
        for name, b in self.tool_buttons.items():
            b.setChecked(self.tool == name or (name == "shape" and shape_tool))
        self.tool_buttons["shape"].setIcon(icons.icon(self.shape if shape_tool else "shapes"))
        colour = self.highlighter_color if self.tool == "highlighter" else self.pen_color
        self.b_colour.setIcon(icons.colour_dot(colour))
        self.b_undo.setEnabled(bool(self.undo_stack))
        self.b_redo.setEnabled(bool(self.redo_stack))
        self.markup_bar.setVisible(self.phase == "markup")
        self._place_bars()

    def _place_bars(self) -> None:
        if self.width() <= 0:
            return
        self.top_bar.adjustSize()
        self.top_bar.move((self.width() - self.top_bar.width()) // 2, 18)
        if self.phase != "markup" or self.sel is None:
            return
        # under the selection, or above / inside it when there is no room
        self.markup_bar.adjustSize()
        bw, bh = self.markup_bar.width(), self.markup_bar.height()
        x = int(min(max(self.sel.center().x() - bw / 2, 8), self.width() - bw - 8))
        y = int(self.sel.bottom() + 12)
        if y + bh > self.height() - 8:
            y = int(self.sel.top() - bh - 12)
            if y < self.top_bar.geometry().bottom() + 8:
                y = int(self.sel.bottom() - bh - 12)
        self.markup_bar.move(x, y)

    def _popup(self, menu: QMenu, button: QWidget) -> None:
        menu.setStyleSheet(MENU_QSS)
        menu.popup(button.mapToGlobal(QPoint(0, button.height() + 6)))

    def _set_special(self, special: str | None) -> None:
        self.color = special == "color"
        self.text = special == "text"
        self.restart()

    def _toggle_quick(self) -> None:
        Prefs.set("quickMarkup", not Prefs.get("quickMarkup"))
        self._refresh()

    def _show_mode_menu(self) -> None:
        menu = QMenu(self)
        for key, title in SNIP_MODES:
            a = menu.addAction(icons.icon(key), title, lambda key=key: self.pick_mode(key))
            a.setCheckable(True)
            a.setChecked(key == self.mode)
        self._popup(menu, self.b_mode)

    def pick_mode(self, mode: str) -> None:
        if mode == "fullscreen":
            self.sel = self.bounds()
            self._capture()
            return
        self.mode = mode
        Prefs.set("snipMode", mode)
        self.hover_win = None
        self._load_windows()
        self.restart()

    def _record(self) -> None:
        cb = self.on_record
        self.done = True
        self.close()
        if cb:
            cb()

    def _pick_tool(self, tool: str) -> None:
        if tool == "eraser" and self.tool == "eraser" and self.marks:
            menu = QMenu(self)
            menu.addAction("Erase all mark-ups", self.erase_all)
            self._popup(menu, self.tool_buttons["eraser"])
        self.tool = tool
        self._refresh()

    def _show_shape_menu(self) -> None:
        menu = QMenu(self)
        for key, title in SHAPES:
            menu.addAction(icons.icon(key), title, lambda key=key: self._pick_shape(key))
        self._popup(menu, self.tool_buttons["shape"])
        self._refresh()

    def _pick_shape(self, shape: str) -> None:
        self.shape = self.tool = shape
        self._refresh()

    def _ensure_drawing_tool(self) -> None:
        if self.tool == "eraser":
            self.tool = "pen"

    def _show_colour_menu(self) -> None:
        self._popup(colour_menu(self, self._pick_colour_for_tool), self.b_colour)

    def _pick_colour_for_tool(self, c: QColor) -> None:
        self._ensure_drawing_tool()
        if self.tool == "highlighter":
            self.highlighter_color = c
        else:
            self.pen_color = c
        self._refresh()

    def _show_size_menu(self) -> None:
        self._popup(size_menu(self, self.stroke, self._pick_size), self.b_size)

    def _pick_size(self, s: float) -> None:
        self._ensure_drawing_tool()
        self.stroke = s
        self._refresh()

    def _show_capture_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(icons.icon("check"), "Capture\tCtrl+Enter", self._capture)
        menu.addAction(icons.icon("save"), "Save to…\tCtrl+S", self._save_to)
        menu.addAction(icons.icon("copy"), "Copy only\tCtrl+C", self._copy_only)
        self._popup(menu, self.b_capture)

    # ================================================================ history

    def _push_undo(self) -> None:
        self.undo_stack.append(list(self.marks))
        self.redo_stack = []

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(list(self.marks))
            self.marks = self.undo_stack.pop()
            self._refresh()
            self.update()

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(list(self.marks))
            self.marks = self.redo_stack.pop()
            self._refresh()
            self.update()

    def erase_all(self) -> None:
        if self.marks:
            self._push_undo()
            self.marks = []
            self._refresh()
            self.update()

    def _erase_at(self, pos: QPointF) -> None:
        for i in range(len(self.marks) - 1, -1, -1):
            if self.marks[i].hit(pos):
                self._push_undo()
                del self.marks[i]
                self._refresh()
                return

    # ================================================================ painting

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.bounds()
        p.drawImage(area, self.shot, QRectF(self.shot.rect()))

        if self.color:
            self._draw_magnifier(p)
            return

        freeform = self.mode == "freeform" and not self.text
        if freeform and self.free:
            draw_dim(p, area, None, 90)
            p.setPen(QPen(QColor("white"), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF(self.free))
        elif self.sel is not None:
            draw_dim(p, area, self.sel)
            if self.marks or self.current:
                p.save()
                p.setClipRect(self.sel)
                for m in self.marks:
                    m.draw(p)
                if self.current:
                    self.current.draw(p)
                p.restore()
            if self.phase == "markup":
                draw_handles(p, self.sel)
            else:
                p.setPen(QPen(QColor("white"), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(self.sel)
            k = self.k()
            label = f"{round(self.sel.width() * k)} × {round(self.sel.height() * k)}"
            y = self.sel.top() - 28 if self.sel.top() > 34 else self.sel.top() + 8
            draw_pill(p, label, origin=QPointF(self.sel.left() + (0 if self.sel.top() > 34 else 8), y))
        elif self.mode == "window" and self.windows and not self.text and self.hover_win is not None:
            draw_dim(p, area, self.hover_win)
            p.setPen(QPen(ACCENT, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.hover_win)
        else:
            draw_dim(p, area, None)
            draw_pill(p, self._hint(), centre=QPointF(area.center().x(), area.center().y()), size=14)

    def _hint(self) -> str:
        if self.text:
            return "Drag over the text to copy it. Esc to cancel."
        if self.mode == "freeform":
            return "Draw a shape around what you want to snip. Esc to cancel."
        if self.mode == "window":
            if self.windows:
                return "Click a window to snip it. Esc to cancel."
            return "Window snips are not available on this desktop: drag an area instead. Esc to cancel."
        return "Drag to snip an area. Esc to cancel."

    def _pixel(self, pos: QPointF) -> tuple[int, int]:
        k = self.k()
        return (min(max(int(pos.x() * k), 0), self.shot.width() - 1),
                min(max(int(pos.y() * k), 0), self.shot.height() - 1))

    def _draw_magnifier(self, p: QPainter) -> None:
        pos = self.cursor_pos
        if pos.x() < 0:
            draw_pill(p, "Click a pixel to copy its colour. Arrows move by 1 px, Shift+arrows by 10. Esc to cancel.",
                      centre=QPointF(self.width() / 2, self.height() / 2), size=14)
            return
        px, py = self._pixel(pos)
        n, cell = 13, 9
        size = n * cell
        x = pos.x() + 22 if pos.x() + 22 + size < self.width() else pos.x() - 22 - size
        y = pos.y() + 22 if pos.y() + 22 + size + 34 < self.height() else pos.y() - 22 - size - 30
        box = QRectF(x, y, size, size)
        p.fillRect(box, QColor("black"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        src = QRect(px - n // 2, py - n // 2, n, n)
        valid = src.intersected(self.shot.rect())
        target = QRectF(box.left() + (valid.left() - src.left()) * cell, box.top() + (valid.top() - src.top()) * cell,
                        valid.width() * cell, valid.height() * cell)
        p.drawImage(target, self.shot, QRectF(valid))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        mid = QRectF(box.left() + (n // 2) * cell, box.top() + (n // 2) * cell, cell, cell)
        p.setPen(QPen(QColor("black"), 1))
        p.drawRect(mid.adjusted(-1, -1, 1, 1))
        p.setPen(QPen(QColor("white"), 1))
        p.drawRect(mid)
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawRect(box)
        hex_ = colour_formats(self.shot.pixelColor(px, py))[0]
        draw_pill(p, hex_, centre=QPointF(box.center().x(), box.bottom() + 18))

    # ================================================================ mouse

    def _clamp(self, pos: QPointF) -> QPointF:
        return QPointF(min(max(pos.x(), 0.0), float(self.width())), min(max(pos.y(), 0.0), float(self.height())))

    def _clamp_to_sel(self, pos: QPointF) -> QPointF:
        s = self.sel
        return QPointF(min(max(pos.x(), s.left()), s.right()), min(max(pos.y(), s.top()), s.bottom()))

    def _window_at(self, pos: QPointF) -> QRectF | None:
        for r in self.windows:  # front to back
            if r.contains(pos):
                return r
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._clamp(e.position())
        self.cursor_pos = pos
        if self.color:
            self._pick_colour(pos)
            return
        if self.phase == "markup" and self.sel is not None:
            edges = edge_hit(pos, self.sel)
            if edges:
                self.drag = ("resize", edges)
            elif self.sel.contains(pos):
                if self.tool == "eraser":
                    self._erase_at(pos)
                    self.drag = ("erase",)
                else:
                    self.current = new_mark(self.tool, pos, self.pen_color, self.highlighter_color, self.stroke)
                    self.drag = ("mark",) if self.current else None
            self.update()
            return
        if self.text or self.mode == "rectangle" or (self.mode == "window" and not self.windows):
            self.sel = None
            self.drag = ("new", pos)
        elif self.mode == "window":
            r = self._window_at(pos)
            if r is not None:
                self.sel = QRectF(r)
                self._picked()
        elif self.mode == "freeform":
            self.free = [pos]
            self.drag = ("free",)
        self.update()

    def mouseMoveEvent(self, e):
        pos = self._clamp(e.position())
        self.cursor_pos = pos
        if self.color:
            self.update()
            return
        d = self.drag
        if d is None:
            if self.phase == "markup" and self.sel is not None:
                edges = edge_hit(pos, self.sel)
                self.setCursor(edge_cursor(edges) if edges else
                               Qt.CursorShape.CrossCursor if self.sel.contains(pos) else Qt.CursorShape.ArrowCursor)
            elif self.mode == "window" and self.windows and not self.text:
                r = self._window_at(pos)
                if r != self.hover_win:
                    self.hover_win = r
                    self.update()
            return
        kind = d[0]
        if kind == "new":
            self.sel = rect_from(d[1], pos, self.bounds())
        elif kind == "free":
            self.free.append(pos)
        elif kind == "resize":
            self.sel = resize_rect(self.sel, d[1], pos, self.bounds())
            self._place_bars()
        elif kind == "mark" and self.current is not None:
            square = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            extend_mark(self.current, self._clamp_to_sel(pos), square)
        elif kind == "erase":
            self._erase_at(pos)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.drag is None:
            return
        kind = self.drag[0]
        self.drag = None
        if kind == "new":
            if self.sel is not None and self.sel.width() >= 8 and self.sel.height() >= 8:
                self._picked()
            else:
                self.sel = None
        elif kind == "free":
            box = QPolygonF(self.free).boundingRect()
            if len(self.free) >= 3 and box.width() >= 8 and box.height() >= 8:
                self.sel = box
                self._capture()
            else:
                self.free = []
        elif kind == "mark" and self.current is not None:
            m, self.current = self.current, None
            if worth_keeping(m):
                self._push_undo()
                self.marks.append(m)
                self._refresh()
        elif kind == "resize":
            self._place_bars()
        self.update()

    def _picked(self) -> None:
        """The user finished choosing an area."""
        if self.text:
            self._extract_text()
        elif self.mode == "rectangle" and Prefs.get("quickMarkup"):
            self.phase = "markup"
            self._refresh()
            self.update()
        else:
            self._capture()

    # ================================================================ keyboard

    def keyPressEvent(self, e):
        key = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key == Qt.Key.Key_Escape:
            if self.phase == "markup":
                self.restart()
            else:
                self.cancel()
        elif self.color:
            if key in arrows:
                step = (10 if shift else 1) / self.k()
                dx, dy = arrows[key]
                base = self.cursor_pos if self.cursor_pos.x() >= 0 else QPointF(self.width() / 2, self.height() / 2)
                self.cursor_pos = self._clamp(QPointF(base.x() + dx * step, base.y() + dy * step))
                self.update()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.cursor_pos.x() >= 0:
                self._pick_colour(self.cursor_pos)
        elif self.phase == "markup":
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._capture()
            elif ctrl and key == Qt.Key.Key_S:
                self._save_to()
            elif ctrl and key == Qt.Key.Key_C:
                self._copy_only()
            elif ctrl and (key == Qt.Key.Key_Y or (shift and key == Qt.Key.Key_Z)):
                self.redo()
            elif ctrl and key == Qt.Key.Key_Z:
                self.undo()
            elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.restart()
            elif key in arrows:
                dx, dy = arrows[key]
                self.nudge(dx * (10 if shift else 1), dy * (10 if shift else 1))
        else:
            super().keyPressEvent(e)

    def nudge(self, dx: float, dy: float) -> None:
        """Moves the area (and what is drawn on it) with the arrow keys."""
        if self.sel is None:
            return
        dx = min(max(dx, -self.sel.left()), self.width() - self.sel.right())
        dy = min(max(dy, -self.sel.top()), self.height() - self.sel.bottom())
        if not dx and not dy:
            return
        self.sel.translate(dx, dy)
        self.marks = [m.moved(dx, dy) for m in self.marks]
        self.undo_stack = [[m.moved(dx, dy) for m in state] for state in self.undo_stack]
        self.redo_stack = [[m.moved(dx, dy) for m in state] for state in self.redo_stack]
        self._place_bars()
        self.update()

    # ================================================================ output

    def output(self) -> QImage | None:
        """The snip with the mark-ups burnt in (and the freeform shape cut out)."""
        if self.sel is None:
            return None
        k = self.k()
        px = QRect(round(self.sel.left() * k), round(self.sel.top() * k),
                   round(self.sel.width() * k), round(self.sel.height() * k)).intersected(self.shot.rect())
        if px.isEmpty():
            return None
        img = self.shot.copy(px)
        if self.marks:
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.scale(k, k)
            p.translate(-px.left() / k, -px.top() / k)
            for m in self.marks:
                m.draw(p)
            p.end()
        if self.mode == "freeform" and not self.text and len(self.free) >= 3:
            cut = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
            cut.fill(Qt.GlobalColor.transparent)
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(q.x() * k - px.left(), q.y() * k - px.top()) for q in self.free]))
            path.closeSubpath()
            p = QPainter(cut)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setClipPath(path)
            p.drawImage(0, 0, img)
            p.end()
            img = cut
        img.setDevicePixelRatio(k)
        return img

    def _final(self) -> QImage | None:
        img = self.output()
        if img is not None and Prefs.get("addBorder") and self.mode != "freeform":
            img = with_border(img)
        return img

    def _capture(self) -> None:
        img = self._final()
        if img is None:
            self.restart()
            return
        cb = self.on_capture
        self.done = True
        if cb:
            cb(img)  # copies while the overlay still has the focus (Wayland wants that), then closes
        self.close()

    def _copy_only(self) -> None:
        img = self._final()
        if img is None:
            return
        copy_image(img)
        self.done = True
        self.close()
        toast("Snip copied to clipboard")

    def _save_to(self) -> None:
        img = self._final()
        if img is None:
            return
        suggested = unique_file(Prefs.get("screenshotFolder"), "Screenshot", "png")
        path, _ = QFileDialog.getSaveFileName(self, "Save snip", suggested, "PNG image (*.png);;JPEG image (*.jpg);;BMP image (*.bmp)")
        self.activateWindow()
        if not path:
            return
        if "." not in path.rsplit("/", 1)[-1]:
            path += ".png"
        if save_image(img, path):
            copy_image(img)
            self.done = True
            self.close()
            toast("Snip saved and copied")
        else:
            toast("Could not save the snip there.")

    def _lens(self) -> None:
        img = self._final()
        if img is None:
            return
        cb = self.on_lens
        self.done = True
        self.close()
        if cb:
            cb(img)

    def _extract_text(self) -> None:
        img = self.output()
        cb = self.on_text
        self.done = True
        self.close()
        if img is not None and cb:
            cb(img)

    def _pick_colour(self, pos: QPointF) -> None:
        hex_, rgb, hsl = colour_formats(self.shot.pixelColor(*self._pixel(pos)))
        copy_text(hex_)
        self.done = True
        self.close()
        toast(f"Colour {hex_} copied to clipboard",
              actions=[(hex_, lambda: copy_text(hex_)), (rgb, lambda: copy_text(rgb)), (hsl, lambda: copy_text(hsl))],
              seconds=7)
