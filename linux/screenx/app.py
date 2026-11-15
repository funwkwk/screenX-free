# SPDX-License-Identifier: GPL-3.0-only
"""screenX for Linux: a tray app. One instance runs in the background; starting screenX again
(for example `screenx --snip` from a desktop keyboard shortcut) hands the command to it."""
from __future__ import annotations

import os
import signal
import sys

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QGuiApplication, QIcon, QImage
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__
from .capture import GRAB_HELP, OCR_HELP, grab, recognize_text, screen_under_pointer, search_lens, window_rects
from .common import copy_image, copy_text, icon_path, is_wayland, open_folder, save_image, toast, with_border
from .editor import Editor, TextResult
from .hotkey import SHORTCUT_TEXT, X11HotKey
from .overlay import Overlay
from .prefs import Prefs, unique_file
from .recorder import Recorder

USAGE = """screenX %s for Linux

Usage: screenx [option]
  (no option)     open the screenX window
  --snip          new snip with the default mode
  --fullscreen    snip the whole screen
  --record        record the screen
  --color         colour picker
  --text          text extractor
  --settings      open Settings
  --background    start in the tray without a window
  --selftest DIR  render the UI into DIR and check the core functions
The options also work while screenX is already running.""" % __version__


class App(QObject):
    def __init__(self, qapp: QApplication, server_name: str):
        super().__init__()
        self.qapp = qapp
        self.settings_window = None
        self.pending = False
        self.icon = QIcon(icon_path()) if icon_path() else QIcon.fromTheme("applets-screenshooter")
        qapp.setWindowIcon(self.icon)

        self.server = QLocalServer(self)
        QLocalServer.removeServer(server_name)  # a socket left behind by a crash
        self.server.newConnection.connect(self._connection)
        self.server.listen(server_name)

        self.hotkey = X11HotKey(lambda: self.snip(Prefs.snip_mode(), delay=int(Prefs.get("snipDelay"))), self)
        self.hotkey_ok = self.hotkey.ok
        self.tray = None
        self._build_tray()

    # ================================================================ tray

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        menu = QMenu()
        menu.addAction("Open screenX", self.open_editor)
        menu.addAction("New Snip", lambda: self.snip(Prefs.snip_mode(), delay=int(Prefs.get("snipDelay"))))
        menu.addAction("Window Snip", lambda: self.snip("window"))
        menu.addAction("Full Screen Snip", lambda: self.snip("fullscreen"))
        menu.addAction("Record Screen", self.start_recording)
        menu.addAction("Colour Picker", self.pick_colour)
        menu.addAction("Text Extractor", self.extract_text)
        menu.addSeparator()
        menu.addAction("Open Screenshots Folder", lambda: open_folder(Prefs.get("screenshotFolder")))
        menu.addAction("Open Recordings Folder", lambda: open_folder(Prefs.get("videoFolder")))
        menu.addSeparator()
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        menu.addAction("Quit screenX", self.quit)
        self.tray_menu = menu
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("screenX")
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self.tray.show()

    def _tray_clicked(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.snip(Prefs.snip_mode(), delay=int(Prefs.get("snipDelay")))

    # ================================================================ commands

    def _connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return

        def read():
            args = bytes(sock.readAll()).decode("utf-8", "replace").split()
            sock.disconnectFromServer()
            self.handle(args)

        sock.readyRead.connect(read)

    def handle(self, args: list, first: bool = False) -> None:
        if "--snip" in args:
            self.snip(Prefs.snip_mode(), delay=int(Prefs.get("snipDelay")))
        elif "--fullscreen" in args:
            self.snip("fullscreen")
        elif "--record" in args:
            self.start_recording()
        elif "--color" in args or "--colour" in args:
            self.pick_colour()
        elif "--text" in args:
            self.extract_text()
        elif "--settings" in args:
            self.open_settings()
        elif "--background" not in args:
            self.open_editor()
        if first and (not args or "--background" in args):
            self._welcome()

    def _welcome(self) -> None:
        where = "the tray icon" if self.tray is not None else "run screenx again to open the window"
        if is_wayland():
            toast(f"screenX is running. Use {where}; a {SHORTCUT_TEXT} shortcut can be added in Settings.", seconds=5)
        elif self.hotkey_ok:
            toast(f"screenX is running. Press {SHORTCUT_TEXT} to snip, or use {where}.", seconds=5)
        else:
            toast(f"screenX is running. {SHORTCUT_TEXT} is taken by another app; use {where}.", seconds=5)

    # ================================================================ snipping

    def snip(self, mode: str, delay: int = 0, editor: Editor | None = None, text: bool = False,
             color: bool = False) -> None:
        """Opens the snipping overlay. The finished snip is copied and auto-saved; it goes to `editor` when
        the snip was started from an editor window, otherwise a notification offers to open it for editing."""
        if Recorder.current is not None or Overlay.active is not None or self.pending:
            return
        self.pending = True
        # give menus and the editor window time to leave the screen before it is frozen
        wait = delay * 1000 if delay > 0 else 280
        QTimer.singleShot(wait, lambda: self._snip_now(mode, editor, text, color))

    def _snip_now(self, mode: str, editor, text: bool, color: bool) -> None:
        self.pending = False
        screen = screen_under_pointer()
        shot = grab(screen)
        if shot is None:
            toast(GRAB_HELP, seconds=7)
            if editor is not None:
                editor.bring_up()
            return
        if mode == "fullscreen" and not text and not color:
            self._captured(with_border(shot) if Prefs.get("addBorder") else shot, editor)
            return
        Overlay(screen, shot, mode=mode, text=text, color=color,
                window_source=lambda: window_rects(screen),
                on_capture=lambda img: self._captured(img, editor),
                on_cancel=(lambda: editor.bring_up()) if editor is not None else None,
                on_record=lambda: self.start_recording(back_to_snip=True),
                on_text=self.extract_text_from,
                on_lens=self.search_lens).open()

    def _captured(self, image: QImage, editor) -> None:
        copy_image(image)
        path = None
        if Prefs.get("autoSave"):
            target = unique_file(Prefs.get("screenshotFolder"), "Screenshot", "png")
            if save_image(image, target):
                path = target
        if editor is not None:
            if Prefs.get("multipleWindows") and editor.has_image():
                editor.bring_up()
                Editor(self, image, path).bring_up()
            else:
                editor.load(image, path)
                editor.bring_up()
            return
        where = f" and saved to {os.path.basename(os.path.dirname(path))}" if path else ""
        toast(f"Snip copied{where}. Click here to edit it.", on_click=lambda: self.open_in_editor(image, path),
              seconds=4.5)

    def open_in_editor(self, image: QImage, path: str | None) -> None:
        """"Multiple windows" off: the snip replaces the one in the open editor (unless that one has unsaved
        edits the user wants to be asked about). On: every snip gets its own window."""
        last = Editor.all[-1] if Editor.all else None
        if not Prefs.get("multipleWindows") and last is not None and not (Prefs.get("askToSaveEdited") and last.dirty):
            last.load(image, path)
            last.bring_up()
        else:
            Editor(self, image, path).bring_up()

    def open_editor(self) -> None:
        if Editor.all:
            Editor.all[-1].bring_up()
        else:
            Editor(self).bring_up()

    # ================================================================ other tools

    def start_recording(self, back_to_snip: bool = False) -> None:
        if Overlay.active is not None or self.pending:
            return
        QTimer.singleShot(250, lambda: Recorder.start(self, back_to_snip=back_to_snip))

    def pick_colour(self) -> None:
        self.snip("rectangle", color=True)

    def extract_text(self) -> None:
        self.snip("rectangle", text=True)

    def extract_text_from(self, image: QImage) -> None:
        toast("Reading the text…", seconds=20)

        def run():
            text = recognize_text(image)
            if text is None:
                toast(OCR_HELP, seconds=7)
            elif not text:
                toast("No text was found in that area.")
            else:
                copy_text(text)
                toast("Text copied to clipboard")
                TextResult.show_text(text)

        QTimer.singleShot(60, run)  # let the message paint before Tesseract blocks

    def search_lens(self, image: QImage) -> None:
        search_lens(image)

    def open_settings(self) -> None:
        from .settings import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def quit(self) -> None:
        if Recorder.current is not None:
            Recorder.current.stop_and_save()
        if self.tray is not None:
            self.tray.hide()
        self.server.close()
        self.qapp.quit()


def main(argv: list) -> int:
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--version" in argv:
        print("screenX", __version__)
        return 0
    QGuiApplication.setApplicationName("screenX")
    QGuiApplication.setDesktopFileName("screenx")
    qapp = QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C in a terminal ends screenX

    if "--selftest" in argv:
        i = argv.index("--selftest")
        if i + 1 >= len(argv):
            print("usage: screenx --selftest <output-folder>")
            return 2
        from .selftest import run
        return run(qapp, argv[i + 1])

    name = "screenX-%d" % os.getuid()
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(400):  # already running: pass the command on
        sock.write((" ".join(argv) or "--open").encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return 0

    app = App(qapp, name)
    app.handle(argv, first=True)
    return qapp.exec()
