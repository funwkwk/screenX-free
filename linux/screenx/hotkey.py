# SPDX-License-Identifier: GPL-3.0-only
"""Ctrl+Shift+S.

X11: a passive key grab on the root window (Xlib through ctypes, on its own connection).
Wayland: apps cannot grab keys, so the desktop itself has to run `screenx --snip`; on GNOME screenX
can add that custom shortcut for the user, elsewhere it is added by hand in the keyboard settings.
"""
from __future__ import annotations

import ast
import ctypes
import ctypes.util
import os
import subprocess

from PyQt6.QtCore import QObject, QSocketNotifier

from .common import is_wayland, launcher, which

SHORTCUT_TEXT = "Ctrl+Shift+S"

_SHIFT, _LOCK, _CONTROL, _MOD2 = 1, 2, 4, 16
_KEY_PRESS = 2
_XK_S = 0x73


class X11HotKey(QObject):
    """Calls `callback` when Ctrl+Shift+S is pressed anywhere. `ok` is False when another app owns it."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.ok = False
        self._x = None
        self._dpy = None
        self._notifier = None
        self._failed = False
        if is_wayland() or not os.environ.get("DISPLAY"):
            return
        try:
            self._setup()
        except Exception:
            self.ok = False

    def _setup(self) -> None:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        x = ctypes.CDLL(name)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSetErrorHandler.argtypes = [ctypes.c_void_p]

        dpy = x.XOpenDisplay(None)
        if not dpy:
            return
        self._x, self._dpy = x, dpy
        root = x.XDefaultRootWindow(dpy)
        keycode = x.XKeysymToKeycode(dpy, _XK_S)
        if not keycode:
            return

        def on_error(_display, _event):  # BadAccess: another client already grabbed the key
            self._failed = True
            return 0

        self._handler = handler_type(on_error)  # keep a reference: Xlib calls into it
        previous = x.XSetErrorHandler(ctypes.cast(self._handler, ctypes.c_void_p))
        base = _CONTROL | _SHIFT
        for extra in (0, _LOCK, _MOD2, _LOCK | _MOD2):  # also with Caps Lock / Num Lock on
            x.XGrabKey(dpy, keycode, base | extra, root, 1, 1, 1)
        x.XSync(dpy, 0)
        x.XSetErrorHandler(previous)
        self.ok = not self._failed
        self._event = ctypes.create_string_buffer(256)  # XEvent is 192 bytes on 64-bit
        self._notifier = QSocketNotifier(x.XConnectionNumber(dpy), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._drain)

    def _drain(self, *_args) -> None:
        fired = False
        while self._x.XPending(self._dpy):
            self._x.XNextEvent(self._dpy, self._event)
            if ctypes.c_int.from_buffer(self._event).value == _KEY_PRESS:
                fired = True
        if fired:
            self.callback()


# ---------------------------------------------------------------- GNOME custom shortcut (Wayland)

_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
_KEY_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/screenx/"


def can_add_gnome_shortcut() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return bool(which("gsettings")) and ("gnome" in desktop or "unity" in desktop)


def _gsettings(*args: str) -> str | None:
    try:
        res = subprocess.run(["gsettings", *args], timeout=5, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def gnome_shortcut_installed() -> bool:
    current = _gsettings("get", _SCHEMA, "custom-keybindings") or ""
    return _KEY_PATH in current


def add_gnome_shortcut() -> bool:
    """Adds "screenX: Ctrl+Shift+S -> run.sh --snip" to GNOME's custom shortcuts."""
    current = _gsettings("get", _SCHEMA, "custom-keybindings")
    if current is None:
        return False
    try:
        paths = list(ast.literal_eval(current.replace("@as", "").strip()))
    except (ValueError, SyntaxError):
        return False
    if _KEY_PATH not in paths:
        paths.append(_KEY_PATH)
        if _gsettings("set", _SCHEMA, "custom-keybindings", str(paths)) is None:
            return False
    item = f"{_SCHEMA}.custom-keybinding:{_KEY_PATH}"
    command = "'" + launcher().replace("'", "'\\''") + "' --snip"
    results = [_gsettings("set", item, "name", "screenX"),
               _gsettings("set", item, "command", command),
               _gsettings("set", item, "binding", "<Control><Shift>s")]
    return all(r is not None for r in results)
