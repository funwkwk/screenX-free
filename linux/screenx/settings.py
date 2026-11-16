# SPDX-License-Identifier: GPL-3.0-only
"""The Settings window. Every change is saved at once."""
from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import __version__
from .common import is_wayland, launcher, which
from .hotkey import (MODIFIER_KEYS, SHORTCUTS, add_gnome_shortcuts, from_qt, gnome_shortcut_installed,
                     command_for, modifier_names, shortcut, uses_gnome_shortcuts)
from .prefs import RECORD_COUNTDOWNS, SNIP_DELAYS, SNIP_MODES, Prefs
from .recorder import audio_problem, backend_kind

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


class ShortcutField(QPushButton):
    """Click, then press a key combination. Esc cancels. `on_pick(text)` gets "Ctrl+Shift+S"."""

    def __init__(self, value: str, on_pick, on_listen=None):
        super().__init__()
        self.value = value
        self.on_pick, self.on_listen = on_pick, on_listen
        self.listening = False
        self.setMinimumWidth(190)
        self.setToolTip("Click, then press the keys. Esc cancels.")
        self.clicked.connect(lambda _=False: self.listen())
        self.show_value()

    def show_value(self) -> None:
        self.setText(self.value or "None")

    def listen(self) -> None:
        if self.listening:
            return
        self.listening = True
        if self.on_listen:
            self.on_listen(True)  # X11: let go of the global grabs so the current keys reach this field
        self.setText("Press a shortcut…")
        self.setFocus()
        self.grabKeyboard()

    def finish(self, picked: str | None = None) -> None:
        if not self.listening:
            return
        self.listening = False
        self.releaseKeyboard()
        if self.on_listen:
            self.on_listen(False)
        self.show_value()
        if picked:
            self.on_pick(picked)

    def event(self, e):
        # Tab and Shift+Tab would move the focus before keyPressEvent sees them
        if self.listening and e.type() == QEvent.Type.KeyPress:
            self.keyPressEvent(e)
            return True
        if self.listening and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self.listening:
            super().keyPressEvent(e)
            return
        e.accept()
        key, mods = e.key(), e.modifiers()
        if key == Qt.Key.Key_Escape and not modifier_names(mods):
            self.finish()
            return
        if key in MODIFIER_KEYS:
            self.setText("+".join(modifier_names(mods) + ["…"]))
            return
        text = from_qt(key, mods)
        if text is None:
            self.setText("Use Ctrl, Alt, Shift or Super with a key…")
            return
        self.finish(text)

    def keyReleaseEvent(self, e):
        if self.listening:
            names = modifier_names(e.modifiers())
            self.setText("+".join(names + ["…"]) if names else "Press a shortcut…")
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self.finish()
        super().focusOutEvent(e)


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
        f.addRow(self._check("Play a sound when taking a snip", "shutterSound"))
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
        problem = audio_problem()
        for title, key in (("Record system audio (what's playing) by default", "recordSystemAudio"),
                           ("Include microphone input by default when a screen recording starts", "recordMic")):
            c = self._check(title, key)
            if problem:
                c.setEnabled(False)
                c.setToolTip(problem)
            f.addRow(c)
        if problem:
            note = QLabel(problem)
            note.setWordWrap(True)
            note.setStyleSheet("color: #D97706;")
            f.addRow(note)
        f.addRow(self._check("Show the mouse pointer in recordings", "recordCursor"))
        self.countdown_combo = self._combo(RECORD_COUNTDOWNS, "recordCountdown")
        self.countdown_combo.setCurrentIndex(max(0, self.countdown_combo.findData(Prefs.countdown())))
        f.addRow("Countdown before recording", self.countdown_combo)
        f.addRow("Frame rate", self._combo([(n, f"{n} fps") for n in (15, 24, 30, 60)], "recordFps"))
        f.addRow("Recordings folder", self._folder("videoFolder"))
        return g

    def _shortcuts(self) -> QGroupBox:
        g = QGroupBox("Shortcuts")
        f = QFormLayout(g)
        self.shortcut_fields = {}
        hotkeys = getattr(self.app, "hotkeys", None)
        listen = (lambda on: hotkeys.set_suspended(on)) if hotkeys is not None and hotkeys.available else None
        for name, (_key, default, _option, title) in SHORTCUTS.items():
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            field = ShortcutField(shortcut(name), lambda text, n=name: self._set_shortcut(n, text), listen)
            self.shortcut_fields[name] = field
            line.addWidget(field)
            # "Reset" goes back to the default; the record shortcut's default is none, so there it reads "Clear"
            reset = QPushButton("Reset" if default else "Clear")
            reset.setToolTip(f"Back to {default}" if default else "No shortcut")
            reset.clicked.connect(lambda _=False, n=name, d=default: self._set_shortcut(n, d))
            line.addWidget(reset)
            line.addStretch(1)
            f.addRow(title, row)
        self.shortcut_label = QLabel()
        self.shortcut_label.setWordWrap(True)
        self.shortcut_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.shortcut_label)
        self.gnome_button = None
        if uses_gnome_shortcuts():
            self.gnome_button = QPushButton("Add these shortcuts to the GNOME keyboard shortcuts")
            self.gnome_button.clicked.connect(lambda _=False: self._add_gnome())
            f.addRow(self.gnome_button)
        self._refresh_shortcut()
        auto = QCheckBox("Start screenX when you log in")
        auto.setChecked(autostart_enabled())
        auto.toggled.connect(lambda on: set_autostart(bool(on)))
        f.addRow(auto)
        return g

    def _set_shortcut(self, name: str, text: str) -> None:
        message = self.app.apply_shortcut(name, text)
        for n, field in self.shortcut_fields.items():
            field.value = shortcut(n)
            field.show_value()
        self._refresh_shortcut(message)

    def _refresh_shortcut(self, message: str = "") -> None:
        warn = bool(message)
        lines = [message] if message else []
        hotkeys = getattr(self.app, "hotkeys", None)
        if not is_wayland():
            ok = getattr(self.app, "hotkey_ok", {})
            for name, (_k, _d, _o, title) in SHORTCUTS.items():
                text = shortcut(name)
                if text and not ok.get(name, True):
                    lines.append(f"{text} ({title}) is used by another app. Choose another one.")
                    warn = True
            if hotkeys is not None and not hotkeys.available:
                lines.append("The shortcuts need an X11 display.")
                warn = True
            elif not warn:
                lines.append("The shortcuts work in every app while screenX is running.")
        elif uses_gnome_shortcuts():
            if gnome_shortcut_installed("snip"):
                lines.append("These are in the GNOME keyboard shortcuts; changes here update them.")
                if self.gnome_button is not None:
                    self.gnome_button.setVisible(False)
            else:
                lines.append("Wayland does not let apps listen for keys, so the shortcuts go into GNOME's "
                             "keyboard settings.")
        else:
            lines.append("Wayland does not let apps listen for keys. Add custom shortcuts in your desktop's "
                         "keyboard settings:")
            for name, (_k, _d, _o, title) in SHORTCUTS.items():
                lines.append(f"{shortcut(name) or 'a key of your choice'}  →  {command_for(name)}")
        self.shortcut_label.setText("\n".join(lines))
        self.shortcut_label.setStyleSheet("color: #D97706;" if warn else "")

    def _add_gnome(self) -> None:
        ok = add_gnome_shortcuts()
        self.gnome_button.setText("Shortcuts added" if ok else "Could not add the shortcuts")
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
            ("Recording sound", bool(which("pactl")), "pactl (PulseAudio / PipeWire)",
             "install pulseaudio-utils (pipewire-pulse on PipeWire)"),
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
