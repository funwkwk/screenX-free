# SPDX-License-Identifier: GPL-3.0-only
"""Shared helpers: session detection, clipboard, image saving, the toast notification."""
from __future__ import annotations

import os
import shutil
import subprocess

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QImage, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .prefs import Prefs

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def is_wayland() -> bool:
    """True in a Wayland session, where screen grabs, hotkeys and window placement need other routes."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def which(tool: str) -> str | None:
    return shutil.which(tool)


def launcher() -> str:
    """The script that starts screenX (used for the autostart entry and the desktop shortcut)."""
    return os.path.join(os.path.dirname(PACKAGE_DIR), "run.sh")


def icon_path() -> str | None:
    for candidate in (os.path.join(PACKAGE_DIR, "icon.png"),
                      os.path.join(os.path.dirname(os.path.dirname(PACKAGE_DIR)), "Assets", "screenX.png")):
        if os.path.isfile(candidate):
            return candidate
    return None


def tray_icon_paths() -> list[str]:
    """The per-size tray icons (frame + X on a blue circle), installed copy first, then the source tree."""
    for folder in (os.path.join(PACKAGE_DIR, "tray"),
                   os.path.join(os.path.dirname(os.path.dirname(PACKAGE_DIR)), "Assets", "tray")):
        if os.path.isdir(folder):
            files = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith(".png")]
            if files:
                return files
    return []


def open_path(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_folder(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    open_path(path)


# ---------------------------------------------------------------- images

def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(data)


def flatten_on_white(image: QImage) -> QImage:
    """Opaque copy (freeform snips are transparent outside the shape; JPEG has no alpha)."""
    out = QImage(image.size(), QImage.Format.Format_RGB32)
    out.setDevicePixelRatio(image.devicePixelRatio())
    out.fill(QColor("white"))
    p = QPainter(out)
    p.drawImage(0, 0, image)
    p.end()
    return out


def save_image(image: QImage, path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".bmp"):
        return flatten_on_white(image).save(path, None, 92)
    return image.save(path)


def with_border(image: QImage) -> QImage:
    """The "Add border to each screenshot" setting. The thickness is in pixels."""
    out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    out.setDevicePixelRatio(1)
    t = Prefs.border_size()
    p = QPainter(out)
    pen = QPen(QColor(Prefs.get("borderColor")), t)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.drawRect(QRectF(t / 2, t / 2, out.width() - t, out.height() - t))
    p.end()
    out.setDevicePixelRatio(image.devicePixelRatio())
    return out


# ---------------------------------------------------------------- clipboard

def _wl_copy(data: bytes, mime: str) -> None:
    """Wayland only lets the focused window set the clipboard; wl-copy (wl-clipboard) has no such limit."""
    if not (is_wayland() and which("wl-copy")):
        return
    try:
        p = subprocess.Popen(["wl-copy", "-t", mime], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.stdin.write(data)
        p.stdin.close()
    except OSError:
        pass


def copy_image(image: QImage) -> None:
    QGuiApplication.clipboard().setImage(image)
    _wl_copy(png_bytes(image), "image/png")


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)
    _wl_copy(text.encode("utf-8"), "text/plain;charset=utf-8")


def copy_file(path: str) -> None:
    """Puts a file on the clipboard so it can be pasted into a file manager or a chat."""
    url = QUrl.fromLocalFile(path)
    mime = QMimeData()
    mime.setUrls([url])
    mime.setData("x-special/gnome-copied-files", ("copy\n" + url.toString()).encode("utf-8"))
    QGuiApplication.clipboard().setMimeData(mime)
    _wl_copy((url.toString() + "\n").encode("utf-8"), "text/uri-list")


# ---------------------------------------------------------------- colours

def colour_formats(c: QColor) -> tuple[str, str, str]:
    hex_ = "#%02X%02X%02X" % (c.red(), c.green(), c.blue())
    rgb = f"rgb({c.red()}, {c.green()}, {c.blue()})"
    h, s, l, _ = c.getHslF()
    hsl = f"hsl({round(max(h, 0) * 360)}, {round(s * 100)}%, {round(l * 100)}%)"
    return hex_, rgb, hsl


# ---------------------------------------------------------------- toast

class Toast(QWidget):
    """Small message near the bottom of the screen that goes away by itself.

    `on_click` makes the whole message a button ("click to edit"); `actions` adds small buttons under it.
    """

    current: "Toast | None" = None
    quiet = False  # the self-test turns messages off

    def __init__(self, text: str, on_click=None, actions=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 12, 18, 12)
        box.setSpacing(8)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: white; font-size: 13px; background: transparent;")
        label.setMaximumWidth(440)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(label)
        if actions:
            row = QHBoxLayout()
            row.setSpacing(6)
            for title, handler in actions:
                b = QPushButton(title)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet("QPushButton { color: white; background: rgba(255,255,255,40); border: none;"
                                " border-radius: 6px; padding: 4px 10px; font-size: 12px; }"
                                "QPushButton:hover { background: rgba(255,255,255,80); }")
                b.clicked.connect(lambda _=False, h=handler: h())
                row.addWidget(b)
            box.addLayout(row)
        self.adjustSize()
        self.setFixedWidth(max(self.width(), 200))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(33, 33, 33, 245))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)

    def mousePressEvent(self, _event):
        action = self.on_click
        if action:
            self.close()
            action()

    def closeEvent(self, event):
        if Toast.current is self:
            Toast.current = None
        super().closeEvent(event)

    @staticmethod
    def show_message(text: str, on_click=None, actions=None, seconds: float = 3.2) -> None:
        if Toast.quiet:
            return
        if Toast.current is not None:
            Toast.current.close()
        t = Toast(text, on_click, actions)
        Toast.current = t
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            f = screen.availableGeometry()
            t.move(f.center().x() - t.width() // 2, f.bottom() - t.height() - 80)
        t.show()
        QTimer.singleShot(int(seconds * 1000), lambda: Toast.current is t and t.close())


def toast(text: str, on_click=None, actions=None, seconds: float = 3.2) -> None:
    Toast.show_message(text, on_click, actions, seconds)
