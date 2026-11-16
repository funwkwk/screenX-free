# SPDX-License-Identifier: GPL-3.0-only
"""The shutter sound played when a snip is taken ("Play a sound when taking a snip").

Qt Multimedia's QSoundEffect when PyQt6 has it (loaded once, plays without blocking); otherwise the first of
pw-play, paplay and aplay, started detached so it never holds up the capture and leaves no zombie behind.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QProcess, QUrl

from .common import PACKAGE_DIR, which
from .prefs import Prefs

quiet = False  # the self-test turns the sound off

_effect = None
_qt_failed = False


def shutter_path() -> str | None:
    """shutter.wav next to the package (copied there by install.sh), else the repository's Assets folder."""
    for candidate in (os.path.join(PACKAGE_DIR, "shutter.wav"),
                      os.path.join(os.path.dirname(os.path.dirname(PACKAGE_DIR)), "Assets", "shutter.wav")):
        if os.path.isfile(candidate):
            return candidate
    return None


def player_command(path: str) -> list | None:
    for cmd in (["pw-play", path], ["paplay", path], ["aplay", "-q", path]):
        if which(cmd[0]):
            return cmd
    return None


def preload() -> None:
    """Loads the sound ahead of time so the first snip does not wait for it (called once after start-up)."""
    global _effect, _qt_failed
    if _effect is not None or _qt_failed:
        return
    path = shutter_path()
    if path is None:
        return
    try:
        from PyQt6.QtMultimedia import QSoundEffect
        effect = QSoundEffect()
        effect.setSource(QUrl.fromLocalFile(path))
        effect.setVolume(0.8)
        _effect = effect
    except Exception:  # PyQt6 without Qt Multimedia (separate package on some distributions)
        _qt_failed = True


def play_shutter() -> None:
    if quiet or not Prefs.get("shutterSound"):
        return
    path = shutter_path()
    if path is None:
        return
    preload()
    if _effect is not None:
        from PyQt6.QtMultimedia import QSoundEffect
        status = _effect.status()
        if status in (QSoundEffect.Status.Ready, QSoundEffect.Status.Loading):
            _effect.play()  # asynchronous; while still loading it plays as soon as it is loaded
            return
    cmd = player_command(path)
    if cmd is not None:
        QProcess.startDetached(cmd[0], cmd[1:])
