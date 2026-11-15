# SPDX-License-Identifier: GPL-3.0-only
"""Screen grabs, the window list, text recognition and Google Lens.

X11: Qt grabs the root window. Wayland: apps may not read the screen, so the grab goes through the
desktop portal (org.freedesktop.portal.Screenshot), falling back to grim / spectacle / gnome-screenshot.
"""
from __future__ import annotations

import locale
import os
import random
import re
import subprocess
import tempfile
import time

from PyQt6.QtCore import QRect, QRectF
from PyQt6.QtGui import QCursor, QGuiApplication, QImage

from .common import copy_image, flatten_on_white, is_wayland, open_path, toast, which

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


def screen_under_pointer():
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


# ---------------------------------------------------------------- portal plumbing (Gio)

def portal_request(bus, interface: str, method: str, make_args, timeout: int = 120):
    """Calls a portal method and waits for its Response signal. Returns (code, results) or None.

    `make_args(token)` builds the GLib.Variant of the call. A private GLib main loop runs while
    waiting, so this also works when Qt does not use the GLib event loop.
    """
    from gi.repository import Gio, GLib

    token = "screenx%d" % random.randrange(1, 1 << 30)
    sender = bus.get_unique_name()[1:].replace(".", "_")
    path = f"{PORTAL_PATH}/request/{sender}/{token}"
    loop = GLib.MainLoop()
    answer = []

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        answer.append(params.unpack())
        loop.quit()

    sub = bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
                               Gio.DBusSignalFlags.NONE, on_response)
    timed_out = []

    def on_timeout():
        timed_out.append(True)
        loop.quit()
        return False  # one shot: GLib removes the timer itself

    timer = GLib.timeout_add_seconds(timeout, on_timeout)
    try:
        bus.call_sync(PORTAL, PORTAL_PATH, interface, method, make_args(token), None,
                      Gio.DBusCallFlags.NONE, -1, None)
        loop.run()
    except GLib.Error:
        return None
    finally:
        bus.signal_unsubscribe(sub)
        if not timed_out:
            GLib.source_remove(timer)
    return answer[0] if answer else None


def _portal_screenshot() -> QImage | None:
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except Exception:
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return None

    def args(token):
        return GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                              "interactive": GLib.Variant("b", False)}))

    reply = portal_request(bus, "org.freedesktop.portal.Screenshot", "Screenshot", args, timeout=60)
    if not reply or reply[0] != 0:
        return None
    uri = reply[1].get("uri", "")
    if not uri.startswith("file://"):
        return None
    path = Gio.File.new_for_uri(uri).get_path()
    image = QImage(path)
    try:
        os.remove(path)  # the portal leaves a copy in Pictures; screenX saves its own
    except OSError:
        pass
    return None if image.isNull() else image


def _cli_screenshot() -> QImage | None:
    tmp = os.path.join(tempfile.gettempdir(), "screenX-grab-%d.png" % os.getpid())
    commands = [["grim", tmp], ["spectacle", "-b", "-n", "-f", "-o", tmp], ["gnome-screenshot", "-f", tmp]]
    for cmd in commands:
        if not which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        image = QImage(tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if not image.isNull():
            return image
    return None


def _crop_to_screen(desktop: QImage, screen) -> QImage:
    """Cuts one screen out of a grab of the whole desktop and tags it with its scale factor."""
    virtual = QRect()
    for s in QGuiApplication.screens():
        virtual = virtual.united(s.geometry())
    geo = screen.geometry()
    if virtual.width() <= 0 or virtual.height() <= 0:
        return desktop
    k = desktop.width() / virtual.width()
    px = QRect(round((geo.x() - virtual.x()) * k), round((geo.y() - virtual.y()) * k),
               round(geo.width() * k), round(geo.height() * k)).intersected(desktop.rect())
    out = desktop.copy(px) if px != desktop.rect() and not px.isEmpty() else desktop
    out.setDevicePixelRatio(max(0.1, out.width() / max(1, geo.width())))
    return out


def grab(screen) -> QImage | None:
    """Still image of one whole screen. Its devicePixelRatio maps pixels to the screen's logical size."""
    if screen is None:
        return None
    if not is_wayland():
        pm = screen.grabWindow(0)
        if pm.isNull():
            return None
        image = pm.toImage()
        image.setDevicePixelRatio(max(0.1, image.width() / max(1, screen.geometry().width())))
        return image
    desktop = _portal_screenshot() or _cli_screenshot()
    if desktop is None:
        return None
    return _crop_to_screen(desktop, screen)


GRAB_HELP = ("Could not capture the screen. On Wayland screenX needs xdg-desktop-portal "
             "(or grim / spectacle / gnome-screenshot).")


# ---------------------------------------------------------------- window list (X11)

def _run(cmd: list, timeout: float = 2.0) -> str:
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _extents(props: str, name: str):
    m = re.search(name + r"\(CARDINAL\) = (\d+), (\d+), (\d+), (\d+)", props)
    return tuple(int(v) for v in m.groups()) if m else (0, 0, 0, 0)


def window_rects(screen) -> list:
    """On-screen application windows, front to back, in the logical coordinates of `screen`.

    Uses xprop / xwininfo (package x11-utils). Wayland does not tell apps where other windows are,
    so the list is empty there and window snips fall back to dragging an area.
    """
    if is_wayland() or not (which("xprop") and which("xwininfo")):
        return []
    listing = _run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    ids = re.findall(r"0x[0-9a-fA-F]+", listing.split("#", 1)[-1]) if "#" in listing else []
    geo = screen.geometry()
    dpr = screen.devicePixelRatio() or 1.0
    area = QRectF(0, 0, geo.width(), geo.height())
    out = []
    deadline = time.monotonic() + 2.5
    for wid in reversed(ids[-60:]):
        if time.monotonic() > deadline:
            break
        info = _run(["xwininfo", "-id", wid])
        if "IsViewable" not in info:
            continue
        m = [re.search(p, info) for p in (r"Absolute upper-left X:\s+(-?\d+)", r"Absolute upper-left Y:\s+(-?\d+)",
                                          r"Width:\s+(\d+)", r"Height:\s+(\d+)")]
        if not all(m):
            continue
        x, y, w, h = (int(v.group(1)) for v in m)
        props = _run(["xprop", "-id", wid, "_NET_FRAME_EXTENTS", "_GTK_FRAME_EXTENTS", "_NET_WM_WINDOW_TYPE",
                      "_NET_WM_PID"])
        if "_NET_WM_WINDOW_TYPE_DESKTOP" in props or "_NET_WM_WINDOW_TYPE_DOCK" in props:
            continue
        pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", props)
        if pid and int(pid.group(1)) == os.getpid():
            continue  # screenX's own windows (the overlay is on screen while this runs)
        fl, fr, ft, fb = _extents(props, "_NET_FRAME_EXTENTS")   # title bar and borders around the client
        gl, gr, gt, gb = _extents(props, "_GTK_FRAME_EXTENTS")   # invisible shadow inside the client
        x, y, w, h = x - fl + gl, y - ft + gt, w + fl + fr - gl - gr, h + ft + fb - gt - gb
        local = QRectF((x - geo.x()) / dpr, (y - geo.y()) / dpr, w / dpr, h / dpr).intersected(area)
        if local.width() >= 40 and local.height() >= 40:
            out.append(local)
    return out


# ---------------------------------------------------------------- text recognition

OCR_HELP = "Text extraction needs Tesseract. Install it with your package manager (package: tesseract-ocr)."


def ocr_languages() -> str:
    """"eng" plus the language of the desktop when Tesseract has its data installed."""
    listed = _run(["tesseract", "--list-langs"], timeout=5).split()
    wanted = ["eng"]
    try:
        code = (locale.getlocale()[0] or os.environ.get("LANG", "")).split("_")[0].lower()
    except (ValueError, IndexError):
        code = ""
    three = {"id": "ind", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
             "ru": "rus", "ja": "jpn", "ko": "kor", "zh": "chi_sim", "ar": "ara", "hi": "hin", "tr": "tur",
             "vi": "vie", "th": "tha", "ms": "msa", "pl": "pol", "uk": "ukr"}.get(code)
    if three and three in listed and three not in wanted:
        wanted.append(three)
    return "+".join(lang for lang in wanted if lang in listed or lang == "eng")


def recognize_text(image: QImage) -> str | None:
    """Runs Tesseract on the image. Returns None when Tesseract is not installed."""
    if not which("tesseract"):
        return None
    src = flatten_on_white(image)
    src.setDevicePixelRatio(1)
    if max(src.width(), src.height()) < 1400:  # small screen text reads much better enlarged
        from PyQt6.QtCore import Qt
        src = src.scaled(src.width() * 2, src.height() * 2, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    tmp = os.path.join(tempfile.gettempdir(), "screenX-ocr-%d.png" % os.getpid())
    if not src.save(tmp):
        return ""
    try:
        res = subprocess.run(["tesseract", tmp, "stdout", "-l", ocr_languages(), "--psm", "6"],
                             timeout=45, capture_output=True, text=True)
        text = res.stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- Google Lens

def lens_page(image: QImage) -> str | None:
    """Writes a small local page that posts the snip to lens.google.com/v3/upload (the request the
    "Search any image" box on google.com sends). Returns its path."""
    import base64
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt

    flat = flatten_on_white(image)
    flat.setDevicePixelRatio(1)
    if max(flat.width(), flat.height()) > 2400:
        flat = flat.scaled(2400, 2400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not flat.save(buf, "JPG", 90):
        return None
    buf.close()
    try:
        lang = (locale.getlocale()[0] or "en").split("_")[0] or "en"
    except (ValueError, IndexError):
        lang = "en"
    ms = int(time.time() * 1000)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Google Lens · screenX</title>
<style>body{{font:15px system-ui,sans-serif;display:grid;place-items:center;height:90vh;color:#555}}</style>
</head><body>
<p id="msg">Opening Google Lens…</p>
<form id="f" method="POST" enctype="multipart/form-data" action="https://lens.google.com/v3/upload?hl={lang}&st={ms}">
<input type="file" name="encoded_image" id="i" hidden></form>
<script>
try {{
  const bin = atob("{base64.b64encode(bytes(data)).decode('ascii')}");
  const bytes = new Uint8Array(bin.length);
  for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], "screenX.jpg", {{ type: "image/jpeg" }}));
  document.getElementById("i").files = dt.files;
  document.getElementById("f").submit();
}} catch (e) {{
  document.getElementById("msg").textContent =
    "This browser could not send the image. The snip is on the clipboard: open lens.google.com and press Ctrl+V.";
}}
</script></body></html>
"""
    folder = os.path.join(tempfile.gettempdir(), "screenX-lens-%d" % os.getuid())
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        for old in os.listdir(folder):  # drop pages from earlier searches
            if old.startswith("lens-") and old.endswith(".html"):
                os.remove(os.path.join(folder, old))
        page = os.path.join(folder, f"lens-{ms}.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(html)
        return page
    except OSError:
        return None


def search_lens(image: QImage) -> None:
    copy_image(image)  # also on the clipboard, in case the browser blocks the upload
    page = lens_page(image)
    if page:
        open_path(page)
        toast("Searching with Google Lens…")
    else:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://www.google.com/?olud"))
        toast("Could not upload to Google Lens. The snip is copied: press Ctrl+V in the Lens box.")
