# SPDX-License-Identifier: GPL-3.0-only
"""Screen recording.

    Ready:      [● Start] 00:00:00 [speaker] [mic] [✕]   (over the frozen screen, area adjustable)
    Countdown:  Starting in 3… [✕]                       (area fixed, big number in its middle; Esc cancels)
    Recording:  [❚❚] [■] 00:00:09 [trash]            (small floating bar; a red frame marks the area on X11)

X11 records with `ffmpeg -f x11grab`. Wayland has no such grab: wlroots desktops (Sway, Hyprland) use
wf-recorder, GNOME and KDE use the ScreenCast portal + PipeWire + GStreamer (the desktop asks which
screen to share). Pausing ends a segment; the segments are joined with ffmpeg when the recording stops.

Sound comes from PulseAudio / PipeWire (pipewire-pulse): the microphone is the default source, system audio
the monitor of the default sink. With both on they are mixed into one track (ffmpeg amix, GStreamer
audiomixer; wf-recorder takes a single device, so screenX loads a temporary null sink that both are looped
into and records its monitor, and unloads it when the recording ends).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time

from PyQt6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import icons
from .canvas import draw_dim, draw_handles, draw_pill, edge_cursor, edge_hit, rect_from, resize_rect
from .capture import GRAB_HELP, PORTAL, PORTAL_PATH, grab, portal_request, screen_under_pointer
from .common import copy_file, is_wayland, open_folder, toast, which
from .overlay import Bar, bar_button, bar_separator
from .prefs import Prefs, unique_file

RED = QColor("#E81123")


def even(v: int) -> int:
    return max(2, v - (v % 2))


def clock(seconds: float) -> str:
    s = int(seconds)
    return "%02d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)


# ====================================================================== backends

# ---------------------------------------------------------------------- sound

MIX_SINK = "screenx_mix"


def _pactl(*args: str) -> str | None:
    if not which("pactl"):
        return None
    try:
        res = subprocess.run(["pactl", *args], timeout=4, capture_output=True, text=True,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def audio_problem() -> str:
    """Why sound cannot be recorded here ("" when it can). Shown as the tooltip of the disabled sound buttons."""
    if which("pactl"):
        if _pactl("info") is not None:
            return ""
        return "No PulseAudio or PipeWire sound server is running, so sound cannot be recorded."
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if os.environ.get("PULSE_SERVER") or (runtime and os.path.exists(os.path.join(runtime, "pulse", "native"))):
        return ""  # a sound server is there; only pactl is missing (system audio uses @DEFAULT_MONITOR@)
    return "Recording sound needs PulseAudio or PipeWire (pipewire-pulse) and pactl (package pulseaudio-utils)."


def _default(kind: str) -> str | None:
    """The default sink or source name ("Sink" / "Source")."""
    name = _pactl(f"get-default-{kind.lower()}")  # pactl 15 and newer
    if name:
        return name
    for line in (_pactl("info") or "").splitlines():
        if line.startswith(f"Default {kind}:"):
            return line.split(":", 1)[1].strip() or None
    return None


def default_monitor() -> str:
    """The source that carries what is playing: the monitor of the default output."""
    sink = _default("Sink")
    return sink + ".monitor" if sink else "@DEFAULT_MONITOR@"


def _unload_mix() -> None:
    lines = (_pactl("list", "short", "modules") or "").splitlines()
    ids = [line.split()[0] for line in lines if MIX_SINK in line and line.split()]
    for module in sorted(ids, key=lambda i: -int(i) if i.isdigit() else 0):  # loopbacks before the sink
        _pactl("unload-module", module)


def load_mix(monitor: str) -> bool:
    """A temporary null sink with system audio and the microphone looped into it, for recorders that take a
    single audio device. Its monitor is `MIX_SINK.monitor`. Undo with `_unload_mix()`."""
    _unload_mix()  # left over from a crash
    mic = _default("Source")
    if not mic:
        return False
    if _pactl("load-module", "module-null-sink", f"sink_name={MIX_SINK}",
              "sink_properties=device.description=screenX-recording") is None:
        return False
    for source in (monitor, mic):
        if _pactl("load-module", "module-loopback", f"source={source}", f"sink={MIX_SINK}", "latency_msec=20") is None:
            _unload_mix()
            return False
    return True


def ffmpeg_command(display: str, x: int, y: int, w: int, h: int, fps: int, cursor: bool, mic: bool, path: str,
                   system: str = "") -> list:
    """`system` is the PulseAudio source for system audio ("" = none), `mic` adds the default source."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1" if cursor else "0",
           "-video_size", f"{w}x{h}", "-i", f"{display}+{x},{y}"]
    sources = ([system] if system else []) + (["default"] if mic else [])
    for source in sources:
        cmd += ["-thread_queue_size", "1024", "-f", "pulse", "-i", source]
    if len(sources) == 2:
        # amix halves each input; volume=2 brings them back to their own level
        cmd += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:dropout_transition=0,volume=2[a]",
                "-map", "0:v", "-map", "[a]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if sources:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    return cmd + ["-movflags", "+faststart", path]


def gst_audio(mic: bool, system: str) -> list:
    """gst-launch elements that feed the sound into the muxer named "mux"."""
    sources = ([["pulsesrc", f"device={system}"]] if system else []) + ([["pulsesrc"]] if mic else [])
    encode = ["audioconvert", "!", "audioresample", "!", "opusenc", "!", "queue", "!", "mux."]
    if not sources:
        return []
    if len(sources) == 1:
        return [*sources[0], "!", *encode]
    out = ["audiomixer", "name=amix", "!", *encode]
    for src in sources:
        out += [*src, "!", "audioconvert", "!", "audioresample", "!", "audio/x-raw,rate=48000,channels=2",
                "!", "queue", "!", "amix."]
    return out


class Backend:
    ext = "mp4"
    note = ""  # shown once the recording runs (for example: sound recorded only in part)

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.log = os.path.join(tempfile.gettempdir(), "screenX-record-%d.log" % os.getpid())

    def prepare(self) -> bool:
        return True

    def command(self, path: str) -> list:
        raise NotImplementedError

    def popen_options(self) -> dict:
        return {}

    def start(self, path: str) -> bool:
        try:
            with open(self.log, "w") as log:
                self.proc = subprocess.Popen(self.command(path), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=log, **self.popen_options())
            return True
        except (OSError, ValueError):
            self.proc = None
            return False

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ask_to_finish(self, p: subprocess.Popen) -> None:
        p.send_signal(signal.SIGINT)

    def stop(self) -> None:
        """Ends the segment and waits until the file is finished."""
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            try:
                self.ask_to_finish(p)
            except (OSError, ValueError):
                pass
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill()
        try:
            p.stdin.close()
        except (OSError, ValueError):
            pass

    def error_text(self) -> str:
        try:
            with open(self.log, "r", errors="replace") as f:
                lines = [line.strip() for line in f if line.strip()]
            return lines[-1][:200] if lines else ""
        except OSError:
            return ""

    def close(self) -> None:
        try:
            os.remove(self.log)
        except OSError:
            pass


class FfmpegX11(Backend):
    def __init__(self, rect: tuple, fps: int, cursor: bool, mic: bool, system: str = ""):
        super().__init__()
        self.rect, self.fps, self.cursor, self.mic, self.system = rect, fps, cursor, mic, system

    def command(self, path: str) -> list:
        x, y, w, h = self.rect
        return ffmpeg_command(os.environ.get("DISPLAY", ":0"), x, y, w, h, self.fps, self.cursor, self.mic, path,
                              self.system)

    def ask_to_finish(self, p: subprocess.Popen) -> None:
        p.stdin.write(b"q")  # ffmpeg finishes the file cleanly on "q"
        p.stdin.flush()


class WfRecorder(Backend):
    def __init__(self, rect: tuple, mic: bool, system: str = ""):
        super().__init__()
        self.rect, self.mic, self.system = rect, mic, system
        self.mixed = False
        self.device = system  # "" with the microphone alone: wf-recorder's -a takes the default source

    def prepare(self) -> bool:
        if self.mic and self.system:
            self.mixed = load_mix(self.system)
            # without the mix (no pactl, or the modules would not load) system audio alone is recorded
            self.device = MIX_SINK + ".monitor" if self.mixed else self.system
            if not self.mixed:
                self.note = "wf-recorder takes one sound source and the two could not be mixed: " \
                            "recording system audio without the microphone."
        return True

    def command(self, path: str) -> list:
        x, y, w, h = self.rect
        cmd = ["wf-recorder", "-g", f"{x},{y} {w}x{h}", "-f", path]
        if self.device:
            cmd.append(f"--audio={self.device}")
        elif self.mic:
            cmd.append("-a")
        return cmd

    def close(self) -> None:
        super().close()
        if self.mixed:
            _unload_mix()
            self.mixed = False


class PortalGst(Backend):
    """ScreenCast portal -> PipeWire -> gst-launch-1.0. `region` is (x, y, w, h) as fractions of the screen."""

    def __init__(self, region: tuple, fps: int, cursor: bool, mic: bool, system: str = ""):
        super().__init__()
        self.region, self.fps, self.cursor, self.mic, self.system = region, fps, cursor, mic, system
        self.bus = None
        self.session = None
        self.node = None
        self.size = None
        self.fd = None
        self.encoder, self.mux = ["x264enc", "speed-preset=veryfast", "tune=zerolatency", "!", "h264parse"], "mp4mux"
        for name, enc, mux, ext in (
                ("x264enc", ["x264enc", "speed-preset=veryfast", "tune=zerolatency", "!", "h264parse"], "mp4mux", "mp4"),
                ("openh264enc", ["openh264enc", "!", "h264parse"], "mp4mux", "mp4"),
                ("vp8enc", ["vp8enc", "deadline=1", "cpu-used=8", "target-bitrate=4000000"], "webmmux", "webm")):
            if self._has(name):
                self.encoder, self.mux, self.ext = enc, mux, ext
                break

    @staticmethod
    def _has(element: str) -> bool:
        if not which("gst-inspect-1.0"):
            return element == "x264enc"
        try:
            return subprocess.run(["gst-inspect-1.0", "--exists", element], timeout=10,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def prepare(self) -> bool:
        """Asks the desktop for a screen to share. Shows the desktop's own dialog."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return False
        cast = "org.freedesktop.portal.ScreenCast"
        v = GLib.Variant

        reply = portal_request(self.bus, cast, "CreateSession", lambda t: v("(a{sv})", ({
            "handle_token": v("s", t), "session_handle_token": v("s", t + "s")},)), timeout=30)
        if not reply or reply[0] != 0:
            return False
        self.session = reply[1].get("session_handle")
        if not self.session:
            return False
        reply = portal_request(self.bus, cast, "SelectSources", lambda t: v("(oa{sv})", (self.session, {
            "handle_token": v("s", t), "types": v("u", 1), "multiple": v("b", False),
            "cursor_mode": v("u", 2 if self.cursor else 1)})), timeout=30)
        if not reply or reply[0] != 0:
            return False
        reply = portal_request(self.bus, cast, "Start", lambda t: v("(osa{sv})", (self.session, "", {
            "handle_token": v("s", t)})), timeout=180)
        if not reply or reply[0] != 0:
            return False
        streams = reply[1].get("streams") or []
        if not streams:
            return False
        self.node = int(streams[0][0])
        size = streams[0][1].get("size") if len(streams[0]) > 1 else None
        self.size = (int(size[0]), int(size[1])) if size else None
        return True

    def _open_remote(self) -> int | None:
        from gi.repository import Gio, GLib
        try:
            reply, fds = self.bus.call_with_unix_fd_list_sync(
                PORTAL, PORTAL_PATH, "org.freedesktop.portal.ScreenCast", "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (self.session, {})), None, Gio.DBusCallFlags.NONE, -1, None, None)
            return fds.get(reply.unpack()[0])
        except Exception:
            return None

    def command(self, path: str) -> list:
        cmd = ["gst-launch-1.0", "-e", "pipewiresrc", f"fd={self.fd}", f"path={self.node}", "do-timestamp=true",
               "!", "videorate", "!", f"video/x-raw,framerate={self.fps}/1"]
        if self.size:
            sw, sh = self.size
            fx, fy, fw, fh = self.region
            left, top = int(fx * sw), int(fy * sh)
            w, h = even(min(int(fw * sw), sw - left)), even(min(int(fh * sh), sh - top))
            cmd += ["!", "videocrop", f"left={left}", f"top={top}",
                    f"right={max(0, sw - left - w)}", f"bottom={max(0, sh - top - h)}"]
        cmd += ["!", "videoconvert", "!", "video/x-raw,format=I420", "!", "queue", "!", *self.encoder,
                "!", self.mux, "name=mux", "!", "filesink", f"location={path}"]
        return cmd + gst_audio(self.mic, self.system)

    def popen_options(self) -> dict:
        return {"pass_fds": [self.fd]}

    def start(self, path: str) -> bool:
        # a PipeWire remote is good for one client: every segment gets a fresh one
        self.fd = self._open_remote()
        if self.fd is None:
            return False
        ok = super().start(path)
        try:
            os.close(self.fd)  # the child has its own copy
        except OSError:
            pass
        return ok

    def close(self) -> None:
        super().close()
        if self.bus is not None and self.session:
            from gi.repository import Gio
            try:
                self.bus.call_sync(PORTAL, self.session, "org.freedesktop.portal.Session", "Close", None, None,
                                   Gio.DBusCallFlags.NONE, 2000, None)
            except Exception:
                pass
        self.session = None


def _gi_available() -> bool:
    try:
        import gi  # noqa: F401
        return True
    except Exception:
        return False


def backend_kind() -> str | None:
    """Which recorder this desktop can use: "ffmpeg", "wf-recorder", "portal" or None."""
    if not is_wayland():
        return "ffmpeg" if which("ffmpeg") else None
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    portal = bool(which("gst-launch-1.0")) and _gi_available()
    big_desktop = any(name in desktop for name in ("gnome", "kde", "unity", "cinnamon"))
    if which("wf-recorder") and not big_desktop:
        return "wf-recorder"
    if portal:
        return "portal"
    return "wf-recorder" if which("wf-recorder") else None


RECORD_HELP_X11 = "Screen recording needs ffmpeg. Install it with your package manager (package: ffmpeg)."
RECORD_HELP_WAYLAND = ("Screen recording on Wayland needs GStreamer with PipeWire (gstreamer1.0-pipewire, "
                       "gstreamer1.0-plugins-good/-ugly, python3-gi), or wf-recorder on Sway / Hyprland.")


# ====================================================================== region picker

class RegionPicker(QWidget):
    """Frozen screen with the recording area: drag to draw it, drag inside to move, edges to resize."""

    def __init__(self, screen, shot: QImage, on_start, on_cancel, audio_problem: str = ""):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("screenX")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.target_screen = screen
        self.shot = shot.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.shot.setDevicePixelRatio(1)
        self.on_start, self.on_cancel = on_start, on_cancel
        self.region: QRectF | None = None
        self.drag = None
        self.done = False
        self.count = 0  # > 0 while the countdown runs
        self.count_timer = QTimer(self)
        self.count_timer.setInterval(1000)
        self.count_timer.timeout.connect(self._count_tick)
        self.count_anim = QVariantAnimation(self)  # 0 -> 1 over each second; paint derives scale and opacity
        self.count_anim.setStartValue(0.0)
        self.count_anim.setEndValue(1.0)
        self.count_anim.setDuration(1000)
        self.count_anim.valueChanged.connect(lambda _v: self.update())

        self.b_start = bar_button("dot", "Start recording (Enter)", self.start, text="Start")
        self.b_start.setIcon(icons.icon("dot", RED.name()))
        self.time = QLabel("00:00:00")
        self.time.setStyleSheet("color: white; font-size: 13px; padding: 0 6px; background: transparent;")
        self.b_system = bar_button("speaker", "Record system audio (what's playing)", self._toggle_system,
                                   checkable=True)
        self.b_mic = bar_button("mic", "Record the microphone", self._toggle_mic, checkable=True)
        for b in (self.b_system, self.b_mic):
            b.setEnabled(not audio_problem)
            if audio_problem:
                b.setToolTip(audio_problem)
        self.b_close = bar_button("close", "Close (Esc)", self.cancel)
        self.separator = bar_separator()
        self.bar = Bar(self, [self.b_start, self.time, self.separator, self.b_system, self.b_mic, self.b_close])
        self._refresh()

    def open(self) -> None:
        if self.target_screen is not None:
            self.setScreen(self.target_screen)
            self.setGeometry(self.target_screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def k(self) -> float:
        return self.shot.width() / max(1, self.width())

    def bounds(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def _refresh(self) -> None:
        mic = bool(Prefs.get("recordMic")) and self.b_mic.isEnabled()
        self.b_mic.setChecked(mic)
        self.b_mic.setIcon(icons.icon("mic" if mic else "mic-off"))
        system = bool(Prefs.get("recordSystemAudio")) and self.b_system.isEnabled()
        self.b_system.setChecked(system)
        self.b_system.setIcon(icons.icon("speaker" if system else "speaker-off"))
        self.b_start.setEnabled(self.region is not None)
        counting = self.count > 0
        for w in (self.b_start, self.separator, self.b_system, self.b_mic):
            w.setVisible(not counting)
        self.time.setText(f"Starting in {self.count}…" if counting else "00:00:00")
        self.b_close.setToolTip("Cancel the countdown (Esc)" if counting else "Close (Esc)")
        self._place_bar()

    def _toggle_mic(self) -> None:
        Prefs.set("recordMic", not Prefs.get("recordMic"))
        self._refresh()

    def _toggle_system(self) -> None:
        Prefs.set("recordSystemAudio", not Prefs.get("recordSystemAudio"))
        self._refresh()

    def _place_bar(self) -> None:
        """Centre the bar above the area (below or inside it when there is no room)."""
        if self.width() <= 0:
            return
        self.bar.adjustSize()
        bw, bh = self.bar.width(), self.bar.height()
        if self.region is None:
            self.bar.move((self.width() - bw) // 2, 18)
            return
        r = self.region
        x = int(min(max(r.center().x() - bw / 2, 8), self.width() - bw - 8))
        y = int(r.top() - bh - 12)
        if y < 8:
            y = int(r.bottom() + 12)
            if y + bh > self.height() - 8:
                y = int(r.top() + 12)
        self.bar.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_bar()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.bounds()
        p.drawImage(area, self.shot, QRectF(self.shot.rect()))
        draw_dim(p, area, self.region)
        if self.region is None:
            draw_pill(p, "Drag to choose the area to record. Esc to cancel.",
                      centre=QPointF(area.center().x(), area.center().y()), size=14)
            return
        draw_handles(p, self.region, RED)
        k = self.k()
        r = self.region
        label = f"{even(round(r.width() * k))} × {even(round(r.height() * k))}"
        draw_pill(p, label, origin=QPointF(r.left() + 8, r.top() + 8))
        if self.count > 0:
            self._paint_badge(p, r.center())

    def _paint_badge(self, p: QPainter, centre: QPointF) -> None:
        """The countdown number: it grows in from 1.3x and fades in over 250 ms, and fades out in the last 150 ms."""
        ms = float(self.count_anim.currentValue() or 0.0) * 1000
        if ms < 250:
            e = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(ms / 250)
            scale, opacity = 1.3 - 0.3 * e, e
        else:
            scale, opacity = 1.0, 1.0 if ms < 850 else max(0.0, 1 - (ms - 850) / 150)
        p.save()
        p.setOpacity(opacity)
        p.translate(centre)
        p.scale(scale, scale)
        p.setPen(QPen(QColor(255, 255, 255, 51), 1.5))
        p.setBrush(QColor(0, 0, 0, 153))
        p.drawEllipse(QPointF(0, 0), 75, 75)
        font = QFont(p.font())
        font.setPointSize(72)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("white"))
        p.drawText(QRectF(-75, -75, 150, 150), Qt.AlignmentFlag.AlignCenter, str(self.count))
        p.restore()

    # ---- mouse

    def _clamp(self, pos: QPointF) -> QPointF:
        return QPointF(min(max(pos.x(), 0.0), float(self.width())), min(max(pos.y(), 0.0), float(self.height())))

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.count > 0:
            return
        pos = self._clamp(e.position())
        edges = edge_hit(pos, self.region) if self.region is not None else None
        if edges:
            self.drag = ("resize", edges)
        elif self.region is not None and self.region.contains(pos):
            self.drag = ("move", pos - self.region.topLeft())
        else:
            self.region = None
            self.drag = ("new", pos)
        self.update()

    def mouseMoveEvent(self, e):
        pos = self._clamp(e.position())
        d = self.drag
        if self.count > 0:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if d is None:
            edges = edge_hit(pos, self.region) if self.region is not None else None
            inside = self.region is not None and self.region.contains(pos)
            self.setCursor(edge_cursor(edges) if edges else
                           Qt.CursorShape.SizeAllCursor if inside else Qt.CursorShape.CrossCursor)
            return
        if d[0] == "new":
            self.region = rect_from(d[1], pos, self.bounds())
        elif d[0] == "resize":
            self.region = resize_rect(self.region, d[1], pos, self.bounds())
        elif d[0] == "move":
            x = min(max(pos.x() - d[1].x(), 0.0), self.width() - self.region.width())
            y = min(max(pos.y() - d[1].y(), 0.0), self.height() - self.region.height())
            self.region.moveTo(x, y)
        self._place_bar()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.drag is None:
            return
        if self.drag[0] == "new" and self.region is not None and (self.region.width() < 32 or self.region.height() < 32):
            self.region = None
        self.drag = None
        self._refresh()
        self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.start()
        else:
            super().keyPressEvent(e)

    def start(self) -> None:
        if self.region is None or self.done or self.count > 0:
            return
        seconds = Prefs.countdown()
        if seconds <= 0:
            self._go()
            return
        self.drag = None
        self.count = seconds
        self.count_anim.stop()
        self.count_anim.start()
        self.count_timer.start()
        self._refresh()
        self.update()

    def _count_tick(self) -> None:
        self.count -= 1
        if self.count > 0:
            self.count_anim.stop()
            self.count_anim.start()
            self._refresh()
            self.update()
            return
        self.count_timer.stop()
        self.count_anim.stop()
        self._go()

    def stop_countdown(self) -> None:
        """Esc / Cancel during the countdown: back to the ready state (area adjustable again)."""
        self.count_timer.stop()
        self.count_anim.stop()
        self.count = 0
        self._refresh()
        self.update()

    def _go(self) -> None:
        self.done = True
        region, k, size = QRectF(self.region), self.k(), (self.width(), self.height())
        self.close()
        self.on_start(region, k, size)

    def cancel(self) -> None:
        if self.count > 0:
            self.stop_countdown()
            return
        if self.done:
            return
        self.done = True
        self.close()
        self.on_cancel()


# ====================================================================== small windows

class FrameWindow(QWidget):
    """Red dashed frame just outside the recorded area. Clicks pass through it (X11 only)."""

    T = 3

    def __init__(self, outer: QRect):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(outer)
        t = self.T
        ring = QRegion(0, 0, outer.width(), outer.height()) - QRegion(t, t, outer.width() - 2 * t, outer.height() - 2 * t)
        self.setMask(ring)
        self.paused = False

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFB900") if self.paused else RED)
        p.setPen(QPen(QColor("white"), self.T, Qt.PenStyle.DashLine))
        t = self.T / 2
        p.drawRect(QRectF(t, t, self.width() - self.T, self.height() - self.T))


class ControlBar(QWidget):
    """[pause] [stop] 00:00:09 [discard] — floats above the area, can be dragged elsewhere."""

    def __init__(self, recorder: "Recorder", can_pause: bool):
        flags = Qt.WindowType.WindowStaysOnTopHint
        if not is_wayland():  # Wayland windows cannot place themselves: keep the title bar so it can be moved
            flags |= Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        super().__init__(None, flags)
        self.setWindowTitle("screenX recording")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.grip: QPoint | None = None
        self.b_pause = bar_button("pause", "Pause", recorder.toggle_pause)
        self.b_pause.setVisible(can_pause)
        b_stop = bar_button("stop", "Stop and save", recorder.stop_and_save)
        b_stop.setIcon(icons.icon("stop", RED.name()))
        self.time = QLabel("00:00:00")
        self.time.setStyleSheet("color: white; font-size: 13px; padding: 0 6px; background: transparent;")
        b_discard = bar_button("trash", "Stop and discard", recorder.stop_and_discard)
        self.bar = Bar(self, [self.b_pause, b_stop, self.time, bar_separator(), b_discard])
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.bar)
        self.adjustSize()

    def set_paused(self, paused: bool) -> None:
        self.b_pause.setIcon(icons.icon("play" if paused else "pause"))
        self.b_pause.setToolTip("Resume" if paused else "Pause")

    def mousePressEvent(self, e):
        self.grip = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.grip is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self.grip)

    def mouseReleaseEvent(self, _e):
        self.grip = None


# ====================================================================== recorder

class Recorder(QObject):
    current: "Recorder | None" = None

    def __init__(self, app, back_to_snip: bool):
        super().__init__()
        self.app = app
        self.back_to_snip = back_to_snip
        self.kind = backend_kind()
        self.backend: Backend | None = None
        self.picker: RegionPicker | None = None
        self.frame: FrameWindow | None = None
        self.bar: ControlBar | None = None
        self.segments: list = []
        self.paused = False
        self.elapsed = 0.0
        self.since = 0.0
        self.screen = None
        self.audio_problem = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    @classmethod
    def start(cls, app, back_to_snip: bool = False) -> None:
        if cls.current is not None:
            return
        r = cls(app, back_to_snip)
        if r.kind is None:
            toast(RECORD_HELP_WAYLAND if is_wayland() else RECORD_HELP_X11, seconds=7)
            return
        r.screen = screen_under_pointer()
        shot = grab(r.screen)
        if shot is None:
            toast(GRAB_HELP, seconds=6)
            return
        cls.current = r
        r.audio_problem = audio_problem()
        r.picker = RegionPicker(r.screen, shot, r._begin, r._cancelled, r.audio_problem)
        r.picker.open()

    def _cancelled(self) -> None:
        Recorder.current = None
        if self.back_to_snip:
            self.app.snip(Prefs.snip_mode())

    # ---- starting

    def _begin(self, region: QRectF, k: float, size: tuple) -> None:
        """`region` is in logical points of the screen, `k` screenshot pixels per point."""
        geo = self.screen.geometry()
        sound = not self.audio_problem
        mic = sound and bool(Prefs.get("recordMic"))
        system = default_monitor() if sound and Prefs.get("recordSystemAudio") else ""
        cursor = bool(Prefs.get("recordCursor"))
        w, h = even(round(region.width() * k)), even(round(region.height() * k))
        if self.kind == "ffmpeg":
            # Qt keeps a screen's origin in native pixels and scales only its size
            rect = (geo.x() + round(region.left() * k), geo.y() + round(region.top() * k), w, h)
            self.backend = FfmpegX11(rect, Prefs.fps(), cursor, mic, system)
        elif self.kind == "wf-recorder":
            rect = (geo.x() + round(region.left()), geo.y() + round(region.top()),
                    even(round(region.width())), even(round(region.height())))
            self.backend = WfRecorder(rect, mic, system)
        else:
            frac = (region.left() / size[0], region.top() / size[1], region.width() / size[0], region.height() / size[1])
            self.backend = PortalGst(frac, Prefs.fps(), cursor, mic, system)
        self.global_region = QRect(geo.x() + round(region.left()), geo.y() + round(region.top()),
                                   round(region.width()), round(region.height()))
        # the countdown (if any) ran in the picker; give the closed picker time to leave the screen so the
        # compositor / X server has unmapped it before the first frame is grabbed (or the portal dialog shows)
        QTimer.singleShot(150 if is_wayland() else 120, self._prepare_and_go)

    def _prepare_and_go(self) -> None:
        if not self.backend.prepare():
            self._fail("The desktop did not share the screen, so nothing was recorded.")
            return
        if not self._start_segment():
            self._fail("Could not start the recorder. " + self.backend.error_text())
            return
        if not is_wayland():
            t = FrameWindow.T
            self.frame = FrameWindow(self.global_region.adjusted(-t, -t, t, t))
            self.frame.show()
        self.bar = ControlBar(self, can_pause=bool(which("ffmpeg")))
        self._place_bar()
        self.bar.show()
        if self.backend.note:
            toast(self.backend.note, seconds=6)
        self.timer.start(250)
        QTimer.singleShot(1200, self._check_alive)

    def _place_bar(self) -> None:
        if is_wayland():
            return
        g, area = self.global_region, self.screen.geometry()
        bw, bh = self.bar.width(), self.bar.height()
        x = min(max(g.center().x() - bw // 2, area.left() + 8), area.right() - bw - 8)
        y = g.top() - bh - 14
        if y < area.top() + 4:
            y = g.bottom() + 14
            if y + bh > area.bottom() - 4:
                y = g.top() + 14  # no room outside: it will show in the recording
        self.bar.move(x, y)

    def _start_segment(self) -> bool:
        path = os.path.join(tempfile.gettempdir(), "screenX-seg-%d-%d.%s" % (os.getpid(), len(self.segments), self.backend.ext))
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        if not self.backend.start(path):
            return False
        self.segments.append(path)
        self.since = time.monotonic()
        return True

    def _check_alive(self) -> None:
        if Recorder.current is self and not self.paused and self.backend and not self.backend.alive():
            self._fail("The recorder stopped. " + self.backend.error_text())

    def _tick(self) -> None:
        if self.bar is not None:
            total = self.elapsed + (0 if self.paused else time.monotonic() - self.since)
            self.bar.time.setText(clock(total))

    # ---- controls

    def toggle_pause(self) -> None:
        if self.backend is None:
            return
        if not self.paused:
            self.elapsed += time.monotonic() - self.since
            self.backend.stop()
            self.paused = True
        else:
            if not self._start_segment():
                self._fail("Could not resume the recording. " + self.backend.error_text())
                return
            self.paused = False
        self.bar.set_paused(self.paused)
        if self.frame is not None:
            self.frame.paused = self.paused
            self.frame.update()

    def stop_and_save(self) -> None:
        if Recorder.current is not self or self.backend is None:
            return
        self._halt()
        final = self._join()
        self._cleanup()
        if final:
            copy_file(final)
            folder = os.path.dirname(final)
            toast(f"Recording saved to {os.path.basename(folder)} and copied. Click to open the folder.",
                  on_click=lambda: open_folder(folder), seconds=6)
        else:
            toast("The recording could not be saved.", seconds=6)

    def stop_and_discard(self) -> None:
        if Recorder.current is not self:
            return
        self._halt()
        self._cleanup()
        toast("Recording discarded")

    def _fail(self, message: str) -> None:
        self._halt()
        self._cleanup()
        toast(message.strip(), seconds=8)

    def _halt(self) -> None:
        self.timer.stop()
        if self.backend is not None and not self.paused:
            self.backend.stop()
        for w in (self.frame, self.bar):
            if w is not None:
                w.close()
        self.frame = self.bar = None

    def _join(self) -> str | None:
        """Moves the single segment, or joins several with ffmpeg, into the videos folder."""
        parts = [s for s in self.segments if os.path.isfile(s) and os.path.getsize(s) > 0]
        if not parts:
            return None
        final = unique_file(Prefs.get("videoFolder"), "Recording", self.backend.ext)
        try:
            if len(parts) == 1:
                shutil.move(parts[0], final)
                return final
            listing = os.path.join(tempfile.gettempdir(), "screenX-concat-%d.txt" % os.getpid())
            with open(listing, "w") as f:
                for part in parts:
                    f.write("file '%s'\n" % part.replace("'", "'\\''"))
            res = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                                  "-i", listing, "-c", "copy", final], timeout=300,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.remove(listing)
            if res.returncode == 0 and os.path.isfile(final):
                return final
            shutil.move(parts[0], final)  # joining failed: keep at least the first part
            return final
        except (OSError, subprocess.SubprocessError):
            return final if os.path.isfile(final) else None

    def _cleanup(self) -> None:
        for s in self.segments:
            try:
                if os.path.exists(s):
                    os.remove(s)
            except OSError:
                pass
        self.segments = []
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        if Recorder.current is self:
            Recorder.current = None
