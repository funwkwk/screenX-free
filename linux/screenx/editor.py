# SPDX-License-Identifier: GPL-3.0-only
"""The editor window: New / mode / delay on the left, markup tools in the middle, copy / save /
Lens / text / settings on the right, the snip centred on a dark canvas."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QImage, QKeySequence, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (QColorDialog, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                             QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QToolBar,
                             QToolButton, QVBoxLayout, QWidget)

from . import icons
from .canvas import SHAPES, Mark, draw_dim, extend_mark, new_mark, rect_from, worth_keeping
from .common import copy_image, copy_text, icon_path, save_image, toast
from .prefs import SNIP_DELAYS, SNIP_MODES, Prefs, unique_file


class EditorCanvas(QWidget):
    """The snip plus its mark-ups, drawn at the image's logical size."""

    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.image: QImage | None = None
        self.marks: list = []
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.current: Mark | None = None
        self.crop_start: QPointF | None = None
        self.crop_rect: QRectF | None = None
        self.erasing = False
        self.tool = "pen"
        self.pen_color = QColor("#E81123")
        self.highlighter_color = QColor("#FCE100")
        self.stroke = 4.0
        self.setCursor(Qt.CursorShape.CrossCursor)

    def logical_size(self) -> QSize:
        if self.image is None:
            return QSize(480, 280)
        d = self.image.devicePixelRatio() or 1.0
        return QSize(max(1, round(self.image.width() / d)), max(1, round(self.image.height() / d)))

    def load(self, image: QImage | None) -> None:
        self.image = image
        self.marks, self.undo_stack, self.redo_stack = [], [], []
        self.current, self.crop_rect, self.crop_start = None, None, None
        self.setFixedSize(self.logical_size())
        self.update()

    # ---- drawing

    def paintEvent(self, _event):
        if self.image is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(self.rect())
        p.drawImage(area, self.image, QRectF(self.image.rect()))
        for m in self.marks:
            m.draw(p)
        if self.current:
            self.current.draw(p)
        if self.crop_rect is not None:
            draw_dim(p, area, self.crop_rect, 140)
            p.setPen(QPen(QColor("white"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self.crop_rect)

    def flattened(self) -> QImage | None:
        """The snip with the mark-ups burnt in, at the original pixel size."""
        if self.image is None:
            return None
        if not self.marks:
            return self.image
        d = self.image.devicePixelRatio() or 1.0
        out = self.image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        out.setDevicePixelRatio(1)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(d, d)
        for m in self.marks:
            m.draw(p)
        p.end()
        out.setDevicePixelRatio(d)
        return out

    # ---- history

    def _push_undo(self) -> None:
        self.undo_stack.append((self.image, list(self.marks)))
        self.redo_stack = []

    def _restore(self, state) -> None:
        self.image, self.marks = state[0], list(state[1])
        self.setFixedSize(self.logical_size())
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self.image is not None and self.undo_stack:
            self.redo_stack.append((self.image, list(self.marks)))
            self._restore(self.undo_stack.pop())

    def redo(self) -> None:
        if self.image is not None and self.redo_stack:
            self.undo_stack.append((self.image, list(self.marks)))
            self._restore(self.redo_stack.pop())

    def erase_all(self) -> None:
        if self.marks:
            self._push_undo()
            self.marks = []
            self.update()
            self.changed.emit()

    def _erase_at(self, pt: QPointF) -> None:
        for i in range(len(self.marks) - 1, -1, -1):
            if self.marks[i].hit(pt):
                self._push_undo()
                del self.marks[i]
                self.update()
                self.changed.emit()
                return

    # ---- mouse

    def _point(self, e) -> QPointF:
        pos = e.position()
        return QPointF(min(max(pos.x(), 0.0), float(self.width())), min(max(pos.y(), 0.0), float(self.height())))

    def mousePressEvent(self, e):
        if self.image is None or e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self._point(e)
        if self.tool == "eraser":
            self.erasing = True
            self._erase_at(pt)
        elif self.tool == "crop":
            self.crop_start = pt
            self.crop_rect = QRectF(pt, pt)
        else:
            self.current = new_mark(self.tool, pt, self.pen_color, self.highlighter_color, self.stroke)
        self.update()

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        pt = self._point(e)
        if self.erasing:
            self._erase_at(pt)
        elif self.crop_start is not None:
            self.crop_rect = rect_from(self.crop_start, pt, QRectF(self.rect()))
        elif self.current is not None:
            extend_mark(self.current, pt, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.erasing = False
        if self.crop_start is not None:
            r, self.crop_start, self.crop_rect = self.crop_rect, None, None
            if r is not None and r.width() >= 8 and r.height() >= 8:
                self._apply_crop(r)
        elif self.current is not None:
            m, self.current = self.current, None
            if worth_keeping(m):
                self._push_undo()
                self.marks.append(m)
                self.changed.emit()
        self.update()

    def _apply_crop(self, r: QRectF) -> None:
        flat = self.flattened()
        if flat is None:
            return
        d = flat.devicePixelRatio() or 1.0
        px = QRect(round(r.left() * d), round(r.top() * d), round(r.width() * d), round(r.height() * d)).intersected(flat.rect())
        if px.isEmpty():
            return
        cropped = flat.copy(px)
        cropped.setDevicePixelRatio(d)
        self._push_undo()
        self.image, self.marks = cropped, []
        self.setFixedSize(self.logical_size())
        self.changed.emit()


class Editor(QMainWindow):
    all: list = []

    def __init__(self, app, image: QImage | None = None, saved_path: str | None = None):
        super().__init__()
        self.app = app
        self.saved_path = saved_path
        self.dirty = False
        self.setWindowTitle("screenX")
        if icon_path():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(icon_path()))
        self.canvas = EditorCanvas()
        self.canvas.changed.connect(self._changed)
        self.empty = QLabel("Press New to take a snip")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color: #BBBBBB; font-size: 15px;")
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { background: #1F1F1F; border: none; }")
        self.scroll.viewport().setStyleSheet("background: #1F1F1F;")
        self.setCentralWidget(self.scroll)
        self._build_toolbar()
        self.resize(980, 660)
        self.load(image, saved_path)
        Editor.all.append(self)

    # ================================================================ UI

    def _ink(self) -> str:
        return self.palette().color(QPalette.ColorRole.WindowText).name()

    def _action(self, bar: QToolBar, name: str, tip: str, handler, shortcut=None, checkable=False) -> QAction:
        a = QAction(icons.icon(name, self._ink()), tip, self)
        a.setToolTip(tip + (f" ({QKeySequence(shortcut).toString()})" if shortcut else ""))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(lambda _=False: handler())
        bar.addAction(a)
        return a

    def _build_toolbar(self) -> None:
        bar = QToolBar("Tools")
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        self.addToolBar(bar)

        new = QToolButton()
        new.setIcon(icons.icon("plus", self._ink()))
        new.setText("New")
        new.setToolTip("New snip (Ctrl+N)")
        new.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        new.setShortcut(QKeySequence("Ctrl+N"))
        new.clicked.connect(lambda _=False: self.new_snip())
        bar.addWidget(new)

        self.mode_box = QComboBox()
        for key, title in SNIP_MODES:
            self.mode_box.addItem(icons.icon(key, self._ink()), title, key)
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(Prefs.snip_mode())))
        self.mode_box.setToolTip("Snip mode")
        self.mode_box.currentIndexChanged.connect(lambda _i: Prefs.set("snipMode", self.mode_box.currentData()))
        bar.addWidget(self.mode_box)

        self.delay_box = QComboBox()
        for seconds, title in SNIP_DELAYS:
            self.delay_box.addItem(title, seconds)
        self.delay_box.setCurrentIndex(max(0, self.delay_box.findData(int(Prefs.get("snipDelay")))))
        self.delay_box.setToolTip("Delay before the snip")
        self.delay_box.currentIndexChanged.connect(lambda _i: Prefs.set("snipDelay", self.delay_box.currentData()))
        bar.addWidget(self.delay_box)
        self._action(bar, "record", "Record the screen", self.new_recording)
        bar.addSeparator()

        self.tools = QActionGroup(self)
        self.tool_actions = {}
        for name, tip in (("pen", "Pen"), ("highlighter", "Highlighter"), ("eraser", "Eraser")):
            a = self._action(bar, name, tip, lambda name=name: self.select_tool(name), checkable=True)
            self.tools.addAction(a)
            self.tool_actions[name] = a
        self.shape_button = QToolButton()
        self.shape_button.setIcon(icons.icon("shapes", self._ink()))
        self.shape_button.setToolTip("Shapes")
        self.shape_button.setCheckable(True)
        self.shape_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.shape_button)
        for key, title in SHAPES:
            menu.addAction(icons.icon(key, self._ink()), title, lambda key=key: self.select_tool(key))
        menu.addSeparator()
        menu.addAction("Erase all mark-ups", self.canvas.erase_all)
        self.shape_button.setMenu(menu)
        bar.addWidget(self.shape_button)
        a = self._action(bar, "crop", "Crop", lambda: self.select_tool("crop"), checkable=True)
        self.tools.addAction(a)
        self.tool_actions["crop"] = a
        bar.addSeparator()

        self.colour_button = QToolButton()
        self.colour_button.setToolTip("Colour")
        self.colour_button.clicked.connect(lambda _=False: self.pick_colour())
        bar.addWidget(self.colour_button)
        self.size_box = QSpinBox()
        self.size_box.setRange(1, 40)
        self.size_box.setValue(int(self.canvas.stroke))
        self.size_box.setSuffix(" px")
        self.size_box.setToolTip("Line size")
        self.size_box.valueChanged.connect(lambda v: setattr(self.canvas, "stroke", float(v)))
        bar.addWidget(self.size_box)
        bar.addSeparator()

        self.undo_action = self._action(bar, "undo", "Undo", self.canvas.undo, "Ctrl+Z")
        self.redo_action = self._action(bar, "redo", "Redo", self.canvas.redo, "Ctrl+Y")
        bar.addSeparator()
        self.image_actions = [
            self._action(bar, "copy", "Copy", self.copy_image, "Ctrl+C"),
            self._action(bar, "save", "Save as", self.save_as, "Ctrl+S"),
            self._action(bar, "lens", "Search with Google Lens", self.search_lens),
            self._action(bar, "text", "Extract text", self.extract_text),
        ]
        bar.addSeparator()
        self._action(bar, "settings", "Settings", self.app.open_settings)
        self.select_tool("pen")

    # ================================================================ image

    def has_image(self) -> bool:
        return self.canvas.image is not None

    def load(self, image: QImage | None, saved_path: str | None) -> None:
        self.saved_path = saved_path
        self.dirty = False
        self.canvas.load(image)
        # QScrollArea deletes the widget it replaces: take the old one out first so both survive
        if self.scroll.widget() is not None:
            self.scroll.takeWidget()
        self.scroll.setWidget(self.canvas if image is not None else self.empty)
        self.scroll.setWidgetResizable(image is None)
        self._refresh()

    def _changed(self) -> None:
        self.dirty = True
        self._refresh()
        if Prefs.get("autoCopyChanges"):
            flat = self.canvas.flattened()
            if flat is not None:
                copy_image(flat)

    def _refresh(self) -> None:
        has = self.has_image()
        self.undo_action.setEnabled(bool(self.canvas.undo_stack))
        self.redo_action.setEnabled(bool(self.canvas.redo_stack))
        for a in self.image_actions:
            a.setEnabled(has)
        colour = self.canvas.highlighter_color if self.canvas.tool == "highlighter" else self.canvas.pen_color
        self.colour_button.setIcon(icons.colour_dot(colour))
        name = self.saved_path.rsplit("/", 1)[-1] if self.saved_path else None
        self.setWindowTitle(f"{name} — screenX" if name else "screenX")

    # ================================================================ actions

    def select_tool(self, tool: str) -> None:
        self.canvas.tool = tool
        is_shape = tool in dict(SHAPES)
        self.shape_button.setChecked(is_shape)
        self.shape_button.setIcon(icons.icon(tool if is_shape else "shapes", self._ink()))
        if is_shape:
            checked = self.tools.checkedAction()
            if checked:
                self.tools.setExclusive(False)
                checked.setChecked(False)
                self.tools.setExclusive(True)
        elif tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        self._refresh()

    def pick_colour(self) -> None:
        highlighter = self.canvas.tool == "highlighter"
        current = self.canvas.highlighter_color if highlighter else self.canvas.pen_color
        c = QColorDialog.getColor(current, self, "Colour")
        if not c.isValid():
            return
        if highlighter:
            self.canvas.highlighter_color = c
        else:
            self.canvas.pen_color = c
        self._refresh()

    def new_snip(self) -> None:
        self.hide()
        self.app.snip(Prefs.snip_mode(), delay=int(Prefs.get("snipDelay")), editor=self)

    def new_recording(self) -> None:
        self.hide()
        self.app.start_recording()

    def copy_image(self) -> None:
        flat = self.canvas.flattened()
        if flat is not None:
            copy_image(flat)
            toast("Copied to clipboard")

    def save_as(self) -> bool:
        flat = self.canvas.flattened()
        if flat is None:
            return False
        suggested = self.saved_path or unique_file(Prefs.get("screenshotFolder"), "Screenshot", "png")
        path, _ = QFileDialog.getSaveFileName(self, "Save snip", suggested,
                                              "PNG image (*.png);;JPEG image (*.jpg);;BMP image (*.bmp)")
        if not path:
            return False
        if "." not in path.rsplit("/", 1)[-1]:
            path += ".png"
        if not save_image(flat, path):
            QMessageBox.warning(self, "screenX", "Could not save the image there.")
            return False
        self.saved_path = path
        self.dirty = False
        self._refresh()
        return True

    def search_lens(self) -> None:
        flat = self.canvas.flattened()
        if flat is not None:
            self.app.search_lens(flat)

    def extract_text(self) -> None:
        flat = self.canvas.flattened()
        if flat is not None:
            self.app.extract_text_from(flat)

    def bring_up(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    # ================================================================ closing

    def closeEvent(self, event):
        if Prefs.get("askToSaveEdited") and self.dirty and self.has_image():
            box = QMessageBox(self)
            box.setWindowTitle("screenX")
            box.setText("Save the changes to this snip?")
            box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                   | QMessageBox.StandardButton.Cancel)
            choice = box.exec()
            if choice == QMessageBox.StandardButton.Cancel or (choice == QMessageBox.StandardButton.Save and not self.save_as()):
                event.ignore()
                return
        if self in Editor.all:
            Editor.all.remove(self)
        super().closeEvent(event)


class TextResult(QDialog):
    """Window with the recognised text (already copied to the clipboard)."""

    current: "TextResult | None" = None

    def __init__(self, text: str):
        super().__init__()
        self.setWindowTitle("Extracted text — screenX")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        box.addWidget(self.edit)
        row = QHBoxLayout()
        row.addWidget(QLabel("Copied to the clipboard."))
        row.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda _=False: copy_text(self.edit.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(lambda _=False: self.close())
        row.addWidget(copy)
        row.addWidget(close)
        box.addLayout(row)
        self.resize(520, 360)

    @staticmethod
    def show_text(text: str) -> None:
        if TextResult.current is not None:
            try:
                TextResult.current.close()
            except RuntimeError:
                pass
        TextResult.current = TextResult(text)
        TextResult.current.destroyed.connect(lambda: setattr(TextResult, "current", None))
        TextResult.current.show()
        TextResult.current.raise_()
        TextResult.current.activateWindow()
