# SPDX-License-Identifier: GPL-3.0-only
"""The Settings window. Every change is saved at once."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import __version__
from .common import is_wayland, launcher, which
from .hotkey import SHORTCUT_TEXT, add_gnome_shortcut, can_add_gnome_shortcut, gnome_shortcut_installed
from .prefs import SNIP_DELAYS, SNIP_MODES, Prefs
from .recorder import backend_kind

AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                         "autostart", "screenx.desktop")


def autostart_enabled() -> bool:
    return os.path.isfile(AUTOSTART)


def set_autostart(on: bool) -> None:
    try:
        if not on:
            if os.path.exists(AUTOSTART):
                os.remove(AUTOSTART)
            return
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=screenX\nComment=Screen capture tool\n"
                    f"Exec=\"{launcher()}\" --background\nIcon=screenx\nTerminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n")
    except OSError:
        pass


class SettingsWindow(QDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("screenX Settings")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        box = QVBoxLayout(page)
        box.setSpacing(14)
        box.addWidget(self._snipping())
        box.addWidget(self._recording())
        box.addWidget(self._shortcuts())
        box.addWidget(self._tools())
        box.addWidget(self._about())
        box.addStretch(1)
        self.resize(620, 680)

    # ---- small builders

    def _check(self, title: str, key: str) -> QCheckBox:
        c = QCheckBox(title)
        c.setChecked(bool(Prefs.get(key)))
        c.toggled.connect(lambda on: Prefs.set(key, bool(on)))
        return c

    def _combo(self, items, key: str) -> QComboBox:
        c = QComboBox()
        for value, title in items:
            c.addItem(title, value)
        c.setCurrentIndex(max(0, c.findData(Prefs.get(key))))
        c.currentIndexChanged.connect(lambda _i: Prefs.set(key, c.currentData()))
        return c

    def _folder(self, key: str) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        line = QLineEdit(Prefs.get(key))
        line.setReadOnly(True)
        pick = QPushButton("Choose…")

        def choose():
            path = QFileDialog.getExistingDirectory(self, "Choose a folder", Prefs.get(key))
            if path:
                Prefs.set(key, path)
                line.setText(path)

        pick.clicked.connect(lambda _=False: choose())
        row.addWidget(line, 1)
        row.addWidget(pick)
        return w

    # ---- sections

    def _snipping(self) -> QGroupBox:
        g = QGroupBox("Snipping")
        f = QFormLayout(g)
        f.addRow("Default snip mode", self._combo([(k, t) for k, t in SNIP_MODES], "snipMode"))
        f.addRow("Snip delay", self._combo(SNIP_DELAYS, "snipDelay"))
        f.addRow(self._check("Quick markup after a rectangle snip", "quickMarkup"))
        f.addRow(self._check("Automatically copy changes", "autoCopyChanges"))
        f.addRow(self._check("Automatically save original screenshots", "autoSave"))
        f.addRow(self._check("Ask to save edited screenshots", "askToSaveEdited"))
        f.addRow(self._check("Multiple windows", "multipleWindows"))
        f.addRow(self._check("Add border to each screenshot", "addBorder"))

        border = QWidget()
        row = QHBoxLayout(border)
        row.setContentsMargins(0, 0, 0, 0)
        self.border_colour = QPushButton()
        self.border_colour.setFixedWidth(64)
        self.border_colour.clicked.connect(lambda _=False: self._pick_border())
        self._paint_border_button()
        size = QSpinBox()
        size.setRange(1, 40)
        size.setSuffix(" px")
        size.setValue(Prefs.border_size())
        size.valueChanged.connect(lambda v: Prefs.set("borderSize", int(v)))
        row.addWidget(QLabel("Colour"))
        row.addWidget(self.border_colour)
        row.addSpacing(16)
        row.addWidget(QLabel("Thickness"))
        row.addWidget(size)
        row.addStretch(1)
        f.addRow("Border", border)
        f.addRow("Screenshots folder", self._folder("screenshotFolder"))
        return g

    def _paint_border_button(self) -> None:
        self.border_colour.setStyleSheet(f"background: {Prefs.get('borderColor')}; border: 1px solid #888; border-radius: 4px; min-height: 20px;")

    def _pick_border(self) -> None:
        c = QColorDialog.getColor(QColor(Prefs.get("borderColor")), self, "Border colour")
        if c.isValid():
            Prefs.set("borderColor", c.name().upper())
            self._paint_border_button()

    def _recording(self) -> QGroupBox:
        g = QGroupBox("Screen recording")
        f = QFormLayout(g)
        f.addRow(self._check("Include microphone input by default when a screen recording starts", "recordMic"))
        f.addRow(self._check("Show the mouse pointer in recordings", "recordCursor"))
        f.addRow("Frame rate", self._combo([(n, f"{n} fps") for n in (15, 24, 30, 60)], "recordFps"))
        f.addRow("Recordings folder", self._folder("videoFolder"))
        return g

    def _shortcuts(self) -> QGroupBox:
        g = QGroupBox("Shortcuts")
        f = QFormLayout(g)
        self.shortcut_label = QLabel()
        self.shortcut_label.setWordWrap(True)
        f.addRow("New snip", self.shortcut_label)
        if is_wayland() and can_add_gnome_shortcut():
            self.gnome_button = QPushButton(f"Add {SHORTCUT_TEXT} to the GNOME keyboard shortcuts")
            self.gnome_button.clicked.connect(lambda _=False: self._add_gnome())
            f.addRow(self.gnome_button)
        self._refresh_shortcut()
        auto = QCheckBox("Start screenX when you log in")
        auto.setChecked(autostart_enabled())
        auto.toggled.connect(lambda on: set_autostart(bool(on)))
        f.addRow(auto)
        return g

    def _refresh_shortcut(self) -> None:
        if not is_wayland():
            ok = self.app.hotkey_ok
            self.shortcut_label.setText(SHORTCUT_TEXT if ok else f"{SHORTCUT_TEXT} is used by another app")
            self.shortcut_label.setStyleSheet("" if ok else "color: #D97706;")
        elif can_add_gnome_shortcut() and gnome_shortcut_installed():
            self.shortcut_label.setText(f"{SHORTCUT_TEXT} (GNOME custom shortcut)")
        else:
            self.shortcut_label.setText(
                "Wayland does not let apps listen for keys. Add a custom shortcut in your desktop's keyboard "
                f"settings that runs:\n{launcher()} --snip")
            self.shortcut_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def _add_gnome(self) -> None:
        ok = add_gnome_shortcut()
        self.gnome_button.setText("Shortcut added" if ok else "Could not add the shortcut")
        self.gnome_button.setEnabled(not ok)
        self._refresh_shortcut()

    def _tools(self) -> QGroupBox:
        g = QGroupBox("Helper programs")
        f = QFormLayout(g)
        recorder = backend_kind()
        rows = [
            ("Screen recording", recorder is not None,
             {"ffmpeg": "ffmpeg", "wf-recorder": "wf-recorder", "portal": "desktop portal + GStreamer"}.get(recorder, ""),
             "install ffmpeg" if not is_wayland() else "install gstreamer1.0-pipewire and the GStreamer plugins"),
            ("Text extractor", bool(which("tesseract")), "Tesseract", "install tesseract-ocr"),
        ]
        if is_wayland():
            rows.append(("Clipboard", bool(which("wl-copy")), "wl-clipboard", "install wl-clipboard"))
            rows.append(("Joining paused recordings", bool(which("ffmpeg")), "ffmpeg", "install ffmpeg"))
        else:
            rows.append(("Window snips", bool(which("xprop") and which("xwininfo")), "xprop, xwininfo", "install x11-utils"))
        for title, ok, found, hint in rows:
            label = QLabel(f"✓ {found}" if ok else f"✗ not found: {hint}")
            label.setStyleSheet("color: #15803D;" if ok else "color: #D97706;")
            f.addRow(title, label)
        return g

    def _about(self) -> QGroupBox:
        g = QGroupBox("About this app")
        f = QVBoxLayout(g)
        text = QLabel(f"screenX {__version__} for Linux<br>by <a href=\"https://funwkwk.my.id\">funwkwk</a><br>"
                      "Free software under the GNU General Public License v3.0.")
        text.setOpenExternalLinks(True)
        f.addWidget(text)
        row = QHBoxLayout()
        quit_button = QPushButton("Quit screenX")
        quit_button.clicked.connect(lambda _=False: self.app.quit())
        row.addWidget(quit_button)
        row.addStretch(1)
        f.addLayout(row)
        return g
