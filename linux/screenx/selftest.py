# SPDX-License-Identifier: GPL-3.0-only
"""`screenx --selftest <folder>`: renders the windows over a made-up screen into PNG files and checks the
core functions. Needs no display server (QT_QPA_PLATFORM=offscreen works) and never grabs the real screen.
The log ends with DONE; any line starting with FAIL is a problem."""
from __future__ import annotations

import os
import traceback

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from . import __version__
from .canvas import Mark, edge_hit, resize_rect
from .common import Toast, colour_formats, save_image, which, with_border
from .prefs import Prefs, unique_file

W, H = 1280, 800


def fake_screen(scale: int = 1) -> QImage:
    """A desktop-like picture with known colours at known places."""
    img = QImage(W * scale, H * scale, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.scale(scale, scale)
    p.fillRect(0, 0, W, H, QColor("#2B5F8A"))
    p.fillRect(0, 0, W, 28, QColor("#1B1B1B"))
    p.fillRect(120, 110, 620, 420, QColor("#F4F4F4"))
    p.fillRect(120, 110, 620, 34, QColor("#D0D0D0"))
    p.fillRect(800, 200, 360, 260, QColor("#107C10"))
    p.fillRect(200, 600, 100, 100, QColor("#E81123"))
    font = QFont()
    font.setPixelSize(26)
    p.setFont(font)
    p.setPen(QColor("#202020"))
    p.drawText(QRectF(150, 170, 560, 60), Qt.AlignmentFlag.AlignLeft, "screenX self-test 12345")
    p.end()
    return img


def run(qapp: QApplication, folder: str) -> int:
    os.makedirs(folder, exist_ok=True)
    log_path = os.path.join(folder, "selftest.log")
    lines: list = []
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        lines.append(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))

    def step(name: str, fn) -> None:
        try:
            fn()
        except Exception:  # a crash in one window must not hide the others
            check(name, False, traceback.format_exc().strip().splitlines()[-1])
            lines.append(traceback.format_exc())

    Toast.quiet = True
    Prefs.path = os.path.join(folder, "settings.json")
    Prefs.reset_cache()
    Prefs.set("screenshotFolder", os.path.join(folder, "shots"))
    Prefs.set("videoFolder", os.path.join(folder, "videos"))
    Prefs.set("quickMarkup", True)
    Prefs.set("addBorder", False)
    lines.append(f"screenX {__version__} self-test, platform {qapp.platformName()}")

    def overlay_tests():
        from .overlay import Overlay
        got = []
        o = Overlay(None, fake_screen(2), mode="rectangle", on_capture=got.append)
        o.resize(W, H)
        o.show()
        qapp.processEvents()
        check("overlay scale", abs(o.k() - 2.0) < 0.01, f"k={o.k():.2f}")
        o.grab().save(os.path.join(folder, "overlay-pick.png"))
        o.sel = QRectF(100, 90, 680, 470)
        o._picked()
        check("quick markup opens", o.phase == "markup" and o.markup_bar.isVisible())
        o.marks.append(Mark("pen", [QPointF(180, 300), QPointF(300, 340), QPointF(420, 300)], QColor("#E81123"), 4))
        o.marks.append(Mark("highlighter", [QPointF(150, 200), QPointF(450, 200)], QColor("#FCE100"), 16))
        o.marks.append(Mark("rectangle", [QPointF(500, 260), QPointF(700, 400)], QColor("#0078D7"), 4))
        o.marks.append(Mark("arrow", [QPointF(520, 500), QPointF(700, 430)], QColor("#107C10"), 6))
        o.marks.append(Mark("ellipse", [QPointF(160, 400), QPointF(330, 520)], QColor("#B146C2"), 4))
        o._refresh()
        qapp.processEvents()
        o.grab().save(os.path.join(folder, "overlay.png"))
        out = o.output()
        check("snip size", out is not None and (out.width(), out.height()) == (1360, 940),
              f"{out.width()}x{out.height()}" if out is not None else "none")
        check("snip pixel", out is not None and out.pixelColor(700, 900).name().upper() == "#2B5F8A",
              out.pixelColor(700, 900).name() if out is not None else "")
        check("mark burnt in", out is not None and out.pixelColor(1200, 440).name().upper() == "#0078D7",
              out.pixelColor(1200, 440).name() if out is not None else "")
        if out is not None:
            out.save(os.path.join(folder, "snip.png"))
        before = QRectF(o.sel)
        o.nudge(10, 0)
        check("nudge moves area and marks", o.sel.left() == before.left() + 10 and o.marks[2].points[0].x() == 510)
        o._push_undo()
        o.marks.pop()
        o.undo()
        check("undo", len(o.marks) == 5)
        o._capture()
        qapp.processEvents()
        check("capture callback", len(got) == 1 and not got[0].isNull())

        o = Overlay(None, fake_screen(1), mode="freeform")
        o.resize(W, H)
        o.free = [QPointF(300, 200), QPointF(500, 200), QPointF(400, 400)]
        o.sel = QRectF(300, 200, 200, 200)
        cut = o.output()
        check("freeform is transparent outside", cut is not None and cut.pixelColor(2, 198).alpha() == 0
              and cut.pixelColor(100, 60).alpha() == 255)
        o.close()

        o = Overlay(None, fake_screen(1), color=True)
        o.resize(W, H)
        o.show()
        o.cursor_pos = QPointF(250, 650)
        qapp.processEvents()
        o.grab().save(os.path.join(folder, "colour-picker.png"))
        hex_ = colour_formats(o.shot.pixelColor(*o._pixel(o.cursor_pos)))[0]
        check("colour picker", hex_ == "#E81123", hex_)
        o.close()

    def canvas_tests():
        r = QRectF(100, 100, 200, 100)
        check("edge hit corner", edge_hit(QPointF(101, 99), r) == (True, False, True, False))
        check("edge hit inside", edge_hit(QPointF(200, 150), r) is None)
        grown = resize_rect(r, (False, True, False, True), QPointF(400, 300), QRectF(0, 0, 1000, 1000))
        check("resize", grown == QRectF(100, 100, 300, 200))
        m = Mark("rectangle", [QPointF(10, 10), QPointF(60, 40)], QColor("red"), 4)
        check("eraser hit", m.hit(QPointF(30, 20)) and not m.hit(QPointF(200, 200)))
        check("colour formats", colour_formats(QColor("#E81123")) == ("#E81123", "rgb(232, 17, 35)", "hsl(355, 86%, 49%)"),
              str(colour_formats(QColor("#E81123"))))

    def editor_tests():
        from .editor import Editor

        class FakeApp:
            def open_settings(self): pass
            def snip(self, *a, **k): pass
            def start_recording(self, *a, **k): pass
            def search_lens(self, *a): pass
            def extract_text_from(self, *a): pass

        shot = fake_screen(1).copy(100, 90, 680, 470)
        e = Editor(FakeApp(), shot, None)
        e.show()
        e.canvas.marks.append(Mark("arrow", [QPointF(60, 400), QPointF(260, 300)], QColor("#E81123"), 5))
        qapp.processEvents()
        e.grab().save(os.path.join(folder, "editor.png"))
        flat = e.canvas.flattened()
        check("editor flatten", flat is not None and flat.size() == shot.size())
        e.canvas._apply_crop(QRectF(20, 20, 300, 200))
        check("editor crop", e.canvas.image.width() == 300 and e.canvas.image.height() == 200 and not e.canvas.marks)
        e.canvas.undo()
        check("editor undo crop", e.canvas.image.width() == 680 and len(e.canvas.marks) == 1)
        e.dirty = False
        e.close()
        empty = Editor(FakeApp())
        empty.show()
        qapp.processEvents()
        empty.grab().save(os.path.join(folder, "editor-empty.png"))
        empty.close()

    def settings_tests():
        from .settings import SettingsWindow

        class FakeApp:
            hotkey_ok = True
            def quit(self): pass

        s = SettingsWindow(FakeApp())
        s.show()
        qapp.processEvents()
        s.grab().save(os.path.join(folder, "settings.png"))
        s.close()

    def recorder_tests():
        from .recorder import ControlBar, PortalGst, RegionPicker, clock, even, ffmpeg_command
        started = []
        picker = RegionPicker(None, fake_screen(1), lambda *a: started.append(a), lambda: None)
        picker.resize(W, H)
        picker.region = QRectF(300, 160, 641, 361)
        picker.show()
        picker._refresh()
        qapp.processEvents()
        picker.grab().save(os.path.join(folder, "record-setup.png"))
        picker.start()
        check("record start callback", len(started) == 1 and started[0][0] == QRectF(300, 160, 641, 361))

        class FakeRecorder:
            def toggle_pause(self): pass
            def stop_and_save(self): pass
            def stop_and_discard(self): pass

        bar = ControlBar(FakeRecorder(), can_pause=True)
        bar.show()
        qapp.processEvents()
        bar.grab().save(os.path.join(folder, "record-bar.png"))
        bar.close()
        cmd = ffmpeg_command(":0", 300, 160, even(641), even(361), 30, True, False, "/tmp/out.mp4")
        check("ffmpeg command", cmd[cmd.index("-video_size") + 1] == "640x360" and ":0+300,160" in cmd and "pulse" not in cmd,
              " ".join(cmd))
        check("ffmpeg command with microphone", "pulse" in ffmpeg_command(":0", 0, 0, 640, 360, 30, False, True, "/tmp/o.mp4"))
        g = PortalGst((0.25, 0.25, 0.5, 0.5), 30, True, False)
        g.fd, g.node, g.size = 7, 42, (1920, 1080)
        gst = g.command("/tmp/out." + g.ext)
        check("gstreamer command", "left=480" in gst and "top=270" in gst and "right=480" in gst and "bottom=270" in gst,
              " ".join(gst))
        check("clock", clock(3725) == "01:02:05")

    def file_tests():
        img = fake_screen(1).copy(0, 0, 200, 120)
        a = unique_file(Prefs.get("screenshotFolder"), "Screenshot", "png")
        check("save png", save_image(img, a) and os.path.getsize(a) > 0)
        b = unique_file(Prefs.get("screenshotFolder"), "Screenshot", "png")
        check("unique file name", a != b, os.path.basename(b))
        jpg = os.path.join(folder, "shots", "test.jpg")
        check("save jpg", save_image(img, jpg) and os.path.getsize(jpg) > 0)
        Prefs.set("borderSize", 4)
        Prefs.set("borderColor", "#00FF00")
        bordered = with_border(img)
        check("border", bordered.pixelColor(1, 1).name().upper() == "#00FF00"
              and bordered.pixelColor(100, 60).name().upper() != "#00FF00")
        Prefs.reset_cache()
        check("settings persist", Prefs.get("borderSize") == 4 and Prefs.get("borderColor") == "#00FF00")
        from .capture import lens_page
        page = lens_page(img)
        check("lens page", bool(page) and "lens.google.com/v3/upload" in open(page, encoding="utf-8").read())

    def ocr_tests():
        from .capture import recognize_text
        if not which("tesseract"):
            lines.append("SKIP ocr: tesseract is not installed")
            return
        text = recognize_text(fake_screen(2).copy(240, 220, 1400, 520)) or ""
        check("ocr", "12345" in text, repr(text[:60]))

    def record_tests():
        """A real 2-second recording, only on X11 with ffmpeg (deleted afterwards)."""
        import time
        from .common import is_wayland
        from .recorder import FfmpegX11
        if is_wayland() or not which("ffmpeg") or not os.environ.get("DISPLAY") or qapp.platformName() != "xcb":
            lines.append("SKIP recording: needs an X11 session with ffmpeg")
            return
        out = os.path.join(folder, "record-test.mp4")
        b = FfmpegX11((0, 0, 320, 240), 30, True, False)
        ok = b.start(out)
        time.sleep(2.0)
        b.stop()
        size = os.path.getsize(out) if os.path.isfile(out) else 0
        check("recording", ok and size > 1000, f"{size} bytes {b.error_text()}")
        b.close()
        if os.path.isfile(out):
            os.remove(out)

    for name, fn in (("overlay", overlay_tests), ("canvas", canvas_tests), ("editor", editor_tests),
                     ("settings", settings_tests), ("recorder", recorder_tests), ("files", file_tests),
                     ("ocr", ocr_tests), ("recording", record_tests)):
        step(name, fn)

    lines.append("DONE" if failures == 0 else f"DONE with {failures} failure(s)")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if failures == 0 else 1
