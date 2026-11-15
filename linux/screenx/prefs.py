# SPDX-License-Identifier: GPL-3.0-only
"""User settings, stored as JSON in ~/.config/screenX/settings.json (same keys as the macOS build)."""
from __future__ import annotations

import json
import os
import time

from PyQt6.QtCore import QStandardPaths

SNIP_MODES = [("rectangle", "Rectangle"), ("window", "Window"), ("fullscreen", "Full screen"), ("freeform", "Freeform")]
SNIP_DELAYS = [(0, "No delay"), (3, "3 seconds"), (5, "5 seconds"), (10, "10 seconds")]


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "screenX")


def _user_dir(location: QStandardPaths.StandardLocation, fallback: str) -> str:
    path = QStandardPaths.writableLocation(location)
    return path or os.path.join(os.path.expanduser("~"), fallback)


def _defaults() -> dict:
    return {
        "autoSave": True,
        "autoCopyChanges": True,
        "askToSaveEdited": False,
        "multipleWindows": False,
        "addBorder": False,
        "borderColor": "#E81123",
        "borderSize": 2,
        "recordMic": False,
        "recordCursor": True,
        "recordFps": 30,
        "screenshotFolder": os.path.join(_user_dir(QStandardPaths.StandardLocation.PicturesLocation, "Pictures"), "screenX"),
        "videoFolder": os.path.join(_user_dir(QStandardPaths.StandardLocation.MoviesLocation, "Videos"), "screenX"),
        "snipDelay": 0,
        "snipMode": "rectangle",
        "quickMarkup": True,
    }


class Prefs:
    """Tiny key/value store. `Prefs.get("autoSave")`, `Prefs.set("autoSave", False)`."""

    _values: dict | None = None
    path: str | None = None  # overridden by the self-test so it never touches the real settings

    @classmethod
    def file(cls) -> str:
        return cls.path or os.path.join(config_dir(), "settings.json")

    @classmethod
    def _load(cls) -> dict:
        if cls._values is None:
            values = _defaults()
            try:
                with open(cls.file(), "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    for key, default in values.items():
                        # ignore values of the wrong type (a hand-edited or damaged file)
                        if key in stored and isinstance(stored[key], type(default)):
                            values[key] = stored[key]
            except (OSError, ValueError):
                pass
            cls._values = values
        return cls._values

    @classmethod
    def get(cls, key: str):
        return cls._load()[key]

    @classmethod
    def set(cls, key: str, value) -> None:
        values = cls._load()
        if values.get(key) == value:
            return
        values[key] = value
        try:
            os.makedirs(os.path.dirname(cls.file()), exist_ok=True)
            tmp = cls.file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(values, f, indent=2)
            os.replace(tmp, cls.file())
        except OSError:
            pass  # read-only home: the setting still applies until screenX exits

    @classmethod
    def reset_cache(cls) -> None:
        cls._values = None

    # ---- typed helpers

    @classmethod
    def snip_mode(cls) -> str:
        mode = cls.get("snipMode")
        return mode if mode in dict(SNIP_MODES) else "rectangle"

    @classmethod
    def fps(cls) -> int:
        return max(5, min(60, int(cls.get("recordFps"))))

    @classmethod
    def border_size(cls) -> int:
        return max(1, min(40, int(cls.get("borderSize"))))


def unique_file(folder: str, prefix: str, ext: str) -> str:
    """"Screenshot 2026-01-31 101530.png" style unique file inside a folder (created when missing)."""
    os.makedirs(folder, exist_ok=True)
    base = f"{prefix} {time.strftime('%Y-%m-%d %H%M%S')}"
    path = os.path.join(folder, f"{base}.{ext}")
    i = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({i}).{ext}")
        i += 1
    return path
