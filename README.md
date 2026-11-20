# screenX

by [funwkwk](https://funwkwk.my.id) · Licence: [GPL-3.0](LICENSE)

screenX is a free and open-source screen capture tool for **Windows, macOS and Linux**. It works like the
Windows Snipping Tool and adds screen recording with sound, a colour picker, a text extractor (OCR),
in-place markup and Google Lens image search.

| Platform | Status | Written in | Folder |
|---|---|---|---|
| Windows 10 (2004+) / 11 | Stable | C# · .NET 10 · WPF | repository root |
| macOS 13 Ventura+ (Apple silicon and Intel) | Preview | Swift · AppKit / SwiftUI | [`macos/`](macos) |
| Linux (X11 and Wayland) | Preview | Python 3 · PyQt6 | [`linux/`](linux/README.md) |

The three versions are separate native apps. They share the same features, the same icons and the
same shutter sound (`Assets/`), but no code.

## Contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Build and run](#build-and-run)
- [Usage (Windows)](#usage-windows)
- [Data and privacy](#data-and-privacy)
- [Contributing](#contributing)
- [Third-party software](#third-party-software)
- [License](#license)

## Features

- **Snips**: rectangle, window, full screen (all monitors) and freeform (transparent outside the shape),
  with an optional delay
- **Quick markup** right after a snip: pen, highlighter, eraser, shapes (rectangle, ellipse, line, arrow),
  colour emoji, undo / redo; the selection stays resizable from its corners and edges
- **Editor** for an existing snip: draw, copy, save as PNG / JPG / BMP and extract text
- **Screen recording** of a region to MP4 (H.264), with optional system audio and microphone,
  a 3-2-1 countdown, pause / resume and a choice of frame rate
- **Colour picker** with a magnifier (HEX, RGB, HSL)
- **Text extractor (OCR)** that copies the text in any part of the screen
- **Google Lens** image search and the system share sheet
- Snips are copied to the clipboard and auto-saved (`Pictures\screenX`, recordings in `Videos\screenX`)
- Global shortcuts you can change (default **Ctrl + Shift + S**), tray / menu bar icon, start with the system

## Tech stack

### Windows (repository root)

| Part | Technology |
|---|---|
| Language and runtime | C# with nullable reference types, [.NET 10](https://dotnet.microsoft.com/) (`net10.0-windows10.0.19041.0`) |
| User interface | WPF (XAML windows plus windows built in code); Windows Forms only for the tray icon |
| Screen capture | Win32 GDI (`BitBlt`) through P/Invoke (`Native.cs`) |
| Global shortcuts | Win32 `RegisterHotKey` (optionally also on the Print Screen key) |
| Text extractor | Built-in Windows OCR (`Windows.Media.Ocr`, WinRT) using the OCR languages installed on the PC |
| Colour emoji | Direct2D / DirectWrite through [Vortice.Direct2D1](https://github.com/amerkoleci/Vortice.Windows) |
| Recording | Frames captured with GDI and piped into an external [FFmpeg](https://ffmpeg.org/) process (H.264 MP4) |
| Recording sound | [NAudio](https://github.com/naudio/NAudio) (WASAPI loopback for system audio, microphone input), muxed with FFmpeg |
| Share | Windows share sheet (`DataTransferManager`) |
| Installer | No third-party tool: the self-contained single-file `screenX.exe` doubles as its own per-user setup program (`Installer.cs`, built by `build-installer.ps1`) |
| Settings | JSON file in `%AppData%\screenX\settings.json` |

### macOS ([`macos/`](macos))

| Part | Technology |
|---|---|
| Language and build | Swift 5.9 Swift Package (`Package.swift`), universal binary (arm64 + x86_64) |
| User interface | AppKit, with SwiftUI for Settings |
| Screen capture and recording | ScreenCaptureKit and AVFoundation |
| Text extractor | Apple Vision framework |
| Global shortcuts | Carbon `RegisterEventHotKey` |
| Start at login | ServiceManagement |
| Packaging | `macos/build.sh` builds `screenX.app` and a `.dmg`; built by GitHub Actions (the app is not notarised) |

### Linux ([`linux/`](linux/README.md))

| Part | Technology |
|---|---|
| Language and UI | Python 3 and PyQt6 (Qt 6) |
| Screen capture | Qt screen grab on X11; on Wayland the XDG desktop portal (D-Bus via `python3-gi`) or `grim` |
| Recording | FFmpeg on X11; GStreamer + PipeWire (or `wf-recorder`) on Wayland; sound via PulseAudio / PipeWire |
| Text extractor | [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) |
| Install | `linux/install.sh` (apt, dnf or pacman) into `~/.local/share/screenX` |

See [linux/README.md](linux/README.md) for the full list of packages and the Wayland notes.

### Continuous integration

GitHub Actions (`.github/workflows/`), started by hand for now from **Actions → Run workflow**:

- `macos.yml` builds the macOS app on `macos-latest` and publishes the `.dmg` as the `macos-latest` pre-release
- `linux.yml` runs the Linux self-test on a virtual X11 screen (Xvfb)

## Project structure

```text
screenX-free/
├─ App.xaml(.cs)            Windows app entry point, command-line options, single-instance handling
├─ OverlayWindow.xaml(.cs)  Snip / record / colour picker / text extractor overlay and markup bar
├─ EditorWindow.xaml(.cs)   Editor for an existing snip
├─ ScreenCapture.cs         GDI screen capture
├─ Recording.cs             Screen recording (FFmpeg) and audio (NAudio)
├─ OcrService.cs            Windows OCR
├─ EmojiRenderer.cs         Colour emoji through Direct2D
├─ GoogleLens.cs            Google Lens search
├─ ShareHelper.cs           Windows share sheet
├─ Hotkeys.cs, Native.cs    Global shortcuts and Win32 interop
├─ Settings.cs, SettingsWindow.cs, Tray.cs, Toast.cs, TextResultWindow.cs, Util.cs
├─ Installer.cs             Built-in per-user installer / uninstaller
├─ SelfTest.cs              Automated self-test (--selftest)
├─ screenX.csproj, app.manifest, build-installer.ps1
├─ Assets/                  Icons and shutter sound shared by all platforms
├─ macos/                   macOS version (Swift)
├─ linux/                   Linux version (Python + PyQt6)
└─ .github/workflows/       CI for macOS and Linux
```

## Build and run

### Windows

Requirements: Windows 10 version 2004 or later and the [.NET 10 SDK](https://dotnet.microsoft.com/download).

```bash
dotnet build -c Release
```

```bash
bin/Release/net10.0-windows10.0.19041.0/screenX.exe
```

Screen recording needs FFmpeg, either on `PATH` or as `ffmpeg.exe` next to `screenX.exe`:

```bash
winget install Gyan.FFmpeg
```

Build the single-file installer (`dist\screenX-Setup-<version>.exe`, no .NET needed on the target PC):

```bash
powershell -ExecutionPolicy Bypass -File build-installer.ps1
```

Run the self-test (capture, OCR, colours, window renders and a 2-second recording; writes images and a
log to the folder):

```bash
bin/Release/net10.0-windows10.0.19041.0/screenX.exe --selftest selftest-output
```

### macOS

On a Mac with the Xcode command-line tools:

```bash
bash macos/build.sh
```

This creates `macos/build/screenX.app` and a `.dmg`. Ready-made `.dmg` files come from the
`macos-latest` pre-release on GitHub. The app is not notarised, so the first time right-click screenX and
choose **Open**. screenX asks for the **Screen Recording** permission (and Microphone, if you record it).

### Linux

```bash
bash linux/install.sh
```

```bash
screenx
```

Try it without installing (needs `python3-pyqt6`):

```bash
bash linux/run.sh
```

## Usage (Windows)

Open screenX with **Ctrl + Shift + S** (change it in Settings). Windows keeps its own **Win + Shift + S**,
so both can be used side by side. Left-click the tray icon for a new snip, right-click it for the menu.

| Mode | How | Result |
|---|---|---|
| Rectangle | Drag an area | Markup bar (when quick markup is on), otherwise copied and saved |
| Window | Hover a window and click | The whole window is captured |
| Full screen | Pick it from the snip mode menu | All monitors are captured |
| Freeform | Draw any shape | PNG, transparent outside the shape |
| Record | Drag an area, press **Start**, wait for the countdown | MP4 in `Videos\screenX`, file copied to the clipboard |
| Colour picker | Click a pixel (arrows = 1 px, Shift + arrows = 10 px, Enter = pick) | HEX copied; HEX, RGB and HSL from the notification |
| Text extractor | Drag over text | Text copied to the clipboard |

Markup bar shortcuts: **Ctrl + Enter** capture, **Ctrl + S** save to, **Ctrl + C** copy only,
**Ctrl + Z / Ctrl + Y** undo / redo, **Esc** discard. Hold **Shift** while drawing a shape for a square or circle.

Command line (also works while screenX is already running, so the options can be bound to keys):

```text
screenX.exe --snip          new snip
screenX.exe --fullscreen    full screen snip
screenX.exe --record        screen recording
screenX.exe --color         colour picker
screenX.exe --text          text extractor
screenX.exe --background    start in the tray without a window
screenX.exe --uninstall     remove an installed copy
```

## Data and privacy

screenX has no accounts, no telemetry and no analytics. Everything stays on your PC, except when you
choose **Google Lens** (your snip is sent to Google in your browser) or **Share**.

- Settings: `%AppData%\screenX\settings.json`
- Google Lens attempts are logged to `%AppData%\screenX\lens.log` (window titles only)
- Installed copy: `%LocalAppData%\Programs\screenX` (per user, no administrator rights)

## Contributing

Issues and pull requests are welcome.

- Keep each platform native: Windows changes in the root, macOS in `macos/`, Linux in `linux/`.
- Run the Windows self-test (`--selftest`) before sending a Windows change; the Linux self-test and the
  macOS build can be started from the Actions tab.
- Do not commit build output (`bin/`, `obj/`, `dist/`, `macos/build/`); `.gitignore` already excludes it.

## Third-party software

None of these are copied into this repository. NuGet packages are downloaded when you build; the other
programs are used from the copy installed on the PC.

| Software | Used for | Licence |
|---|---|---|
| [NAudio](https://github.com/naudio/NAudio) | Recording sound on Windows | MIT |
| [Vortice.Direct2D1](https://github.com/amerkoleci/Vortice.Windows) | Colour emoji on Windows | MIT |
| [FFmpeg](https://ffmpeg.org/) | Screen recording (Windows, Linux X11) | LGPL or GPL, depending on the build |
| [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) / [Qt](https://www.qt.io/) | Linux user interface | GPL v3 / LGPL v3 |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Linux text extractor | Apache 2.0 |

An installer that bundles `ffmpeg.exe` must follow the licence of that FFmpeg build.

## License

Copyright (c) 2026 [funwkwk](https://funwkwk.my.id).

screenX is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License, version 3](LICENSE), as published by the Free Software Foundation.
It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the licence for the details.
