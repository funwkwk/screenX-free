# screenX for Linux

The Linux version of screenX, written in Python with PyQt6. It has the same tools as the Windows and
macOS versions: rectangle, window, full-screen and freeform snips, quick markup, the editor, screen
recording, the colour picker, the text extractor and Google Lens search.

## Install

```bash
bash linux/install.sh
```

This installs the system packages screenX uses (it asks for your password for that step), copies screenX
to `~/.local/share/screenX` and adds it to the applications menu. Then start **screenX** from the menu, or:

```bash
screenx
```

To try it without installing (PyQt6 must already be there, package `python3-pyqt6`):

```bash
bash linux/run.sh
```

Remove it again with `bash linux/uninstall.sh`.

## What it needs

| For | Package (Debian / Ubuntu name) |
|---|---|
| The app itself | `python3-pyqt6` |
| Screen recording on X11, joining paused recordings | `ffmpeg` |
| Text extractor | `tesseract-ocr` (add `tesseract-ocr-ind` and so on for other languages) |
| Window snips on X11 | `x11-utils` |
| Wayland: screen grabs | `xdg-desktop-portal` and `python3-gi` (or `grim` on Sway / Hyprland) |
| Wayland: clipboard | `wl-clipboard` |
| Wayland: screen recording | `gstreamer1.0-pipewire`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-ugly` (or `wf-recorder` on Sway / Hyprland) |
| Recording sound (system audio, microphone) | PulseAudio or PipeWire with `pipewire-pulse`, and `pulseaudio-utils` (for `pactl`) |
| Shutter sound | `python3-pyqt6.qtmultimedia`, or any of `pw-play` / `paplay` / `aplay` |

Everything except PyQt6 is optional: a tool that misses its helper says which package to install.
**Settings → Helper programs** shows what was found.

## Usage

Run `screenx` once; it stays in the tray. Left-click the tray icon for a new snip, right-click for the menu.

```text
screenx                open the screenX window
screenx --snip         new snip with the default mode
screenx --fullscreen   snip the whole screen
screenx --record       record the screen
screenx --color        colour picker
screenx --text         text extractor
screenx --settings     open Settings
screenx --background   start in the tray without a window
```

The options also work while screenX is already running, so they can be bound to keys.

### Shortcuts

**Settings → Shortcuts** has two: *New snip* (Ctrl+Shift+S unless you change it) and *Record screen* (none
until you set one; pressing it again while recording stops and saves). Click a field and press the keys: a
shortcut needs Ctrl, Alt, Shift or Super plus another key (Print also works alone); Esc cancels, *Reset* /
*Clear* goes back to the default.

- **X11**: the shortcuts work by themselves while screenX is running. When another app already uses a
  combination, Settings says so and keeps the previous one.
- **Wayland**: apps are not allowed to listen for keys. On GNOME, press *Add these shortcuts to the GNOME
  keyboard shortcuts* once; after that, changing a shortcut in screenX updates GNOME too. On other desktops
  add custom shortcuts in the keyboard settings that run `screenx --snip` and `screenx --record`
  (Settings shows the exact command).

### Sound

- A shutter sound plays when a snip is taken (Settings → *Play a sound when taking a snip*).
- Recordings can include **system audio** (what is playing) and the **microphone**: the speaker and
  microphone buttons on the recording bar, with their defaults in Settings. With both on they are mixed into
  one track. On Sway / Hyprland (wf-recorder, which takes one sound source) screenX mixes them through a
  temporary PulseAudio null sink called `screenX-recording` that is removed when the recording ends.

### Keys in the overlay

| Key | Action |
|---|---|
| Esc | Back to the start, or close |
| Enter / Ctrl+Enter | Capture (copy and save) |
| Ctrl+S / Ctrl+C | Save to… / Copy only |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Arrows (Shift = 10 px) | Move the area; in the colour picker, move by one pixel |
| Shift while drawing | Square or circle |

Snips are copied to the clipboard and saved to `~/Pictures/screenX`; recordings go to `~/Videos/screenX`.
Settings are stored in `~/.config/screenX/settings.json`.

## Differences from the Windows version

- **Wayland** limits what any app can do. The desktop may ask for permission before a screen grab; window
  snips fall back to dragging an area (apps cannot see where other windows are); notifications appear where
  the desktop puts them; a recording captures the screen you pick in the desktop's own dialog, cropped to
  the area you drew, without the red frame (the countdown runs before the desktop's dialog).
- Full-screen snips and the overlay cover the screen the pointer is on.
- No emoji stamps and no share sheet yet.
- When a recording is paused and resumed, the parts are joined with ffmpeg.

## Self-test

```bash
bash linux/run.sh --selftest /tmp/screenx-selftest
```

Renders the overlay, editor, settings and recording windows over a made-up screen into PNG files and checks
the core functions; the log ends with `DONE`. Under X11 with ffmpeg it also makes a 2-second test recording,
and with Tesseract it checks the text extractor. It never grabs your real screen. GitHub Actions runs it on
every push that touches `linux/` (`.github/workflows/linux.yml`).

## Source layout

| File | What it holds |
|---|---|
| `screenx/app.py` | Tray icon, single instance, command line |
| `screenx/overlay.py` | Snipping overlay, toolbars, quick markup, colour picker |
| `screenx/editor.py` | Editor window and the extracted-text window |
| `screenx/recorder.py` | Recording area, control bar, ffmpeg / wf-recorder / portal back ends |
| `screenx/capture.py` | Screen grabs, window list, Tesseract, Google Lens |
| `screenx/canvas.py` | Mark-ups and the resizable selection |
| `screenx/hotkey.py` | Global shortcuts: key grabs on X11, GNOME custom shortcuts on Wayland |
| `screenx/sound.py` | The shutter sound |
| `screenx/settings.py`, `prefs.py` | Settings window and storage |
| `screenx/icons.py`, `common.py` | Icons, clipboard, notifications |
