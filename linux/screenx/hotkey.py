# SPDX-License-Identifier: GPL-3.0-only
"""The global shortcuts: "New snip" (Ctrl+Shift+S unless changed) and "Record screen" (none unless set).

They are stored as readable text in the settings ("Ctrl+Shift+S"; "" = no shortcut): the modifiers in the
order Ctrl, Alt, Shift, Super, then one key named the way Qt's QKeySequence names it ("S", "F5", "Print").

X11: passive key grabs on the root window (Xlib through ctypes, on its own connection).
Wayland: apps cannot grab keys, so the desktop itself has to run `screenx --snip` / `screenx --record`;
on GNOME screenX writes those custom shortcuts itself, elsewhere they are added by hand.
"""
from __future__ import annotations

import ast
import ctypes
import ctypes.util
import os
import re
import subprocess

from PyQt6.QtCore import QObject, QSocketNotifier

from .common import is_wayland, launcher, which
from .prefs import Prefs

# name -> (settings key, default, command-line option, title)
SHORTCUTS = {
    "snip": ("snipShortcut", "Ctrl+Shift+S", "--snip", "New snip"),
    "record": ("recordShortcut", "", "--record", "Record screen"),
}

MODIFIERS = ("Ctrl", "Alt", "Shift", "Super")
_MOD_ALIASES = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "super": "Super",
                "meta": "Super", "win": "Super", "cmd": "Super"}
_X_MASKS = {"Shift": 1, "Ctrl": 4, "Alt": 8, "Super": 64}  # ShiftMask, ControlMask, Mod1Mask, Mod4Mask
_GNOME_MODS = {"Ctrl": "<Control>", "Alt": "<Alt>", "Shift": "<Shift>", "Super": "<Super>"}

# Qt key names -> X keysym names (GNOME accelerators use the same names)
_KEYSYMS = {
    "Print": "Print", "Space": "space", "Return": "Return", "Enter": "Return", "Tab": "Tab",
    "Backspace": "BackSpace", "Ins": "Insert", "Del": "Delete", "Home": "Home", "End": "End", "PgUp": "Prior",
    "PgDown": "Next", "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down", "Pause": "Pause",
    "ScrollLock": "Scroll_Lock", "Menu": "Menu",
    "/": "slash", "\\": "backslash", ",": "comma", ".": "period", ";": "semicolon", "'": "apostrophe",
    "[": "bracketleft", "]": "bracketright", "-": "minus", "=": "equal", "`": "grave", "+": "plus",
    "*": "asterisk",
}
_KEY_NAMES = {k.lower(): k for k in _KEYSYMS}
# With Shift held a US keyboard reports "!" for the 1 key and so on: the shortcut names the key itself
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
            "~": "`"}


# ---------------------------------------------------------------- shortcut text

def parse_shortcut(text: str | None) -> tuple[tuple, str] | None:
    """"ctrl+shift+s" -> (("Ctrl", "Shift"), "S"). None when it is empty or not a usable shortcut: it needs
    at least one modifier and one other key (Print alone is allowed too)."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text[:-2].split("+") + ["+"] if text.endswith("++") else text.split("+")
    *mods, key = [p.strip() for p in parts]
    found = set()
    for m in mods:
        name = _MOD_ALIASES.get(m.lower())
        if name is None:
            return None
        found.add(name)
    if len(key) == 1:
        key = key.upper()
        if "Shift" in found and key in _SHIFTED:
            key = _SHIFTED[key]
        if not (key.isascii() and (key.isalnum() or key in _KEYSYMS)):
            return None
    elif re.fullmatch(r"[Ff]([1-9]|[12][0-9]|3[0-5])", key):
        key = key.upper()
    elif key.lower() in _KEY_NAMES:
        key = _KEY_NAMES[key.lower()]
    else:
        return None
    if not found and key != "Print":
        return None
    return tuple(m for m in MODIFIERS if m in found), key


def normalize(text: str | None) -> str | None:
    """The stored form ("Ctrl+Shift+S"), or None when `text` is not a usable shortcut."""
    parsed = parse_shortcut(text)
    return "+".join([*parsed[0], parsed[1]]) if parsed else None


def keysym_name(key: str) -> str:
    if key in _KEYSYMS:
        return _KEYSYMS[key]
    return key.lower() if len(key) == 1 else key


def x11_binding(text: str) -> tuple[str, int] | None:
    """"Ctrl+Shift+S" -> ("s", ControlMask | ShiftMask)."""
    parsed = parse_shortcut(text)
    if parsed is None:
        return None
    mask = 0
    for m in parsed[0]:
        mask |= _X_MASKS[m]
    return keysym_name(parsed[1]), mask


def gnome_accel(text: str) -> str | None:
    """"Ctrl+Shift+S" -> "<Control><Shift>s" (GNOME's accelerator syntax)."""
    parsed = parse_shortcut(text)
    if parsed is None:
        return None
    return "".join(_GNOME_MODS[m] for m in parsed[0]) + keysym_name(parsed[1])


def from_qt(key: int, modifiers) -> str | None:
    """A key press in the shortcut recorder -> "Ctrl+Shift+S". None for a lone modifier or an unusable key."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence
    if key in MODIFIER_KEYS or key in (0, Qt.Key.Key_unknown):
        return None
    if key == Qt.Key.Key_Backtab:
        key = Qt.Key.Key_Tab
    name = QKeySequence(int(key)).toString(QKeySequence.SequenceFormat.PortableText)
    if not name:
        return None
    return normalize("+".join([*modifier_names(modifiers), name]))


def modifier_names(modifiers) -> list:
    from PyQt6.QtCore import Qt
    m = Qt.KeyboardModifier
    pairs = ((m.ControlModifier, "Ctrl"), (m.AltModifier, "Alt"), (m.ShiftModifier, "Shift"), (m.MetaModifier, "Super"))
    return [name for flag, name in pairs if modifiers & flag]


def _modifier_keys() -> set:
    from PyQt6.QtCore import Qt
    k = Qt.Key
    return {int(x) for x in (k.Key_Shift, k.Key_Control, k.Key_Alt, k.Key_AltGr, k.Key_Meta, k.Key_Super_L,
                             k.Key_Super_R, k.Key_Hyper_L, k.Key_Hyper_R, k.Key_CapsLock, k.Key_NumLock)}


MODIFIER_KEYS = _modifier_keys()


def shortcut(name: str) -> str:
    """The current shortcut for "snip" or "record" ("" = none). A missing or damaged value means the default."""
    key, default, _option, _title = SHORTCUTS[name]
    value = Prefs.get(key)
    if not value:
        return ""
    return normalize(value) or default


# ---------------------------------------------------------------- X11

_LOCK, _MOD2 = 2, 16
_IGNORED = (0, _LOCK, _MOD2, _LOCK | _MOD2)  # also fire with Caps Lock / Num Lock on
_RELEVANT = 1 | 4 | 8 | 64
_KEY_PRESS = 2


class _XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int), ("state", ctypes.c_uint),
                ("keycode", ctypes.c_uint), ("same_screen", ctypes.c_int)]


class X11HotKeys(QObject):
    """Global shortcuts on X11, each with its own callback. `available` is False on Wayland."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = None
        self._dpy = None
        self._root = 0
        self._notifier = None
        self._failed = False
        self._grabs: dict = {}  # name -> [keycode, mask, callback]
        self._suspended = False
        if is_wayland() or not os.environ.get("DISPLAY"):
            return
        try:
            self._setup()
        except Exception:
            self._x = self._dpy = None

    @property
    def available(self) -> bool:
        return self._dpy is not None

    def _setup(self) -> None:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        x = ctypes.CDLL(name)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        dpy = x.XOpenDisplay(None)
        if not dpy:
            return
        self._x, self._dpy = x, dpy
        self._root = x.XDefaultRootWindow(dpy)

        def on_error(_display, _event):  # BadAccess: another client already grabbed the key
            self._failed = True
            return 0

        handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self._handler = handler_type(on_error)  # keep a reference: Xlib calls into it
        self._event = ctypes.create_string_buffer(256)  # XEvent is 192 bytes on 64-bit
        self._notifier = QSocketNotifier(x.XConnectionNumber(dpy), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._drain)

    def _keycode(self, text: str) -> tuple[int, int] | None:
        binding = x11_binding(text)
        if binding is None:
            return None
        keysym = self._x.XStringToKeysym(binding[0].encode("ascii"))
        keycode = self._x.XKeysymToKeycode(self._dpy, keysym) if keysym else 0
        return (int(keycode), binding[1]) if keycode else None

    def _grab(self, keycode: int, mask: int) -> bool:
        x = self._x
        self._failed = False
        previous = x.XSetErrorHandler(ctypes.cast(self._handler, ctypes.c_void_p))
        for extra in _IGNORED:
            x.XGrabKey(self._dpy, keycode, mask | extra, self._root, 1, 1, 1)
        x.XSync(self._dpy, 0)
        x.XSetErrorHandler(previous)
        if self._failed:
            self._ungrab(keycode, mask)  # release the variants that did work
        return not self._failed

    def _ungrab(self, keycode: int, mask: int) -> None:
        x = self._x
        previous = x.XSetErrorHandler(ctypes.cast(self._handler, ctypes.c_void_p))
        for extra in _IGNORED:
            x.XUngrabKey(self._dpy, keycode, mask | extra, self._root)
        x.XSync(self._dpy, 0)
        x.XSetErrorHandler(previous)

    def register(self, name: str, text: str, callback) -> bool:
        """Grabs `text` for `name`, releasing what `name` had. "" only releases. False when the shortcut cannot
        be grabbed (usually: another app has it); nothing is grabbed for `name` then."""
        self.unregister(name)
        if not text:
            return True
        if not self.available:
            return False
        found = self._keycode(text)
        if found is None:
            return False
        if any((g[0], g[1]) == found for g in self._grabs.values()):
            return False
        if not self._suspended and not self._grab(*found):
            return False
        self._grabs[name] = [found[0], found[1], callback]
        return True

    def unregister(self, name: str) -> None:
        g = self._grabs.pop(name, None)
        if g is not None and self.available and not self._suspended:
            self._ungrab(g[0], g[1])

    def set_suspended(self, suspended: bool) -> None:
        """Lets go of every grab while the Settings shortcut recorder listens (so it can see the current keys)."""
        if not self.available or suspended == self._suspended:
            return
        self._suspended = suspended
        for name, g in list(self._grabs.items()):
            if suspended:
                self._ungrab(g[0], g[1])
            elif not self._grab(g[0], g[1]):
                del self._grabs[name]

    def _drain(self, *_args) -> None:
        fire = []
        while self._x.XPending(self._dpy):
            self._x.XNextEvent(self._dpy, self._event)
            ev = _XKeyEvent.from_buffer(self._event)
            if ev.type != _KEY_PRESS or self._suspended:
                continue
            state = ev.state & _RELEVANT
            for keycode, mask, callback in self._grabs.values():
                if keycode == ev.keycode and mask == state and callback not in fire:
                    fire.append(callback)
        for callback in fire:
            callback()


# ---------------------------------------------------------------- GNOME custom shortcuts (Wayland)

_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
_KEY_PATHS = {"snip": _BASE + "screenx/", "record": _BASE + "screenx-record/"}
_GNOME_NAMES = {"snip": "screenX", "record": "screenX: record screen"}


def can_add_gnome_shortcut() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return bool(which("gsettings")) and ("gnome" in desktop or "unity" in desktop)


def uses_gnome_shortcuts() -> bool:
    """True where screenX keeps its shortcuts in GNOME's keyboard settings instead of grabbing keys."""
    return is_wayland() and can_add_gnome_shortcut()


def _gsettings(*args: str) -> str | None:
    try:
        res = subprocess.run(["gsettings", *args], timeout=5, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def _custom_paths() -> list | None:
    current = _gsettings("get", _SCHEMA, "custom-keybindings")
    if current is None:
        return None
    try:
        return list(ast.literal_eval(current.replace("@as", "").strip()))
    except (ValueError, SyntaxError):
        return None


def _gvariant_string(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def gnome_shortcut_installed(name: str = "snip") -> bool:
    return _KEY_PATHS[name] in (_custom_paths() or [])


def command_for(name: str) -> str:
    return "'" + launcher().replace("'", "'\\''") + "' " + SHORTCUTS[name][2]


def set_gnome_shortcut(name: str, text: str) -> bool:
    """Adds, updates or (for "") removes the GNOME custom shortcut "screenX" -> `run.sh --snip` / `--record`."""
    paths = _custom_paths()
    if paths is None:
        return False
    path = _KEY_PATHS[name]
    item = f"{_SCHEMA}.custom-keybinding:{path}"
    accel = gnome_accel(text) if text else None
    if accel is None:
        if path in paths:
            paths.remove(path)
            if _gsettings("set", _SCHEMA, "custom-keybindings", str(paths)) is None:
                return False
            for key in ("name", "command", "binding"):
                _gsettings("reset", item, key)
        return not text  # an unusable shortcut is a failure, clearing is not
    if path not in paths:
        paths.append(path)
        if _gsettings("set", _SCHEMA, "custom-keybindings", str(paths)) is None:
            return False
    wanted = {"name": _GNOME_NAMES[name], "command": command_for(name), "binding": accel}
    ok = True
    for key, value in wanted.items():
        if _gvariant_string(_gsettings("get", item, key)) == value:
            continue  # unchanged: no needless dconf writes
        ok = _gsettings("set", item, key, value) is not None and ok
    return ok


def add_gnome_shortcuts() -> bool:
    """The Settings button: puts both shortcuts (the record one only when set) into GNOME."""
    return all([set_gnome_shortcut(name, shortcut(name)) for name in SHORTCUTS])


def sync_gnome_shortcuts() -> None:
    """At start-up: when the user added the shortcuts before, keep them matching the settings."""
    if uses_gnome_shortcuts() and gnome_shortcut_installed("snip"):
        add_gnome_shortcuts()
