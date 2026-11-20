#!/bin/bash
# Installs screenX for Linux for the current user (no root needed, except for the system packages):
#   bash linux/install.sh            install the packages screenX needs, then screenX itself
#   bash linux/install.sh --no-deps  skip the system packages
# Files go to ~/.local/share/screenX, the launcher to ~/.local/bin/screenx.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

install_packages() {
  # $1 = install command, $2 = required packages, $3 = optional packages (tried one by one)
  local cmd="$1" required="$2" optional="$3" p
  echo "Installing required packages: $required"
  sudo $cmd $required
  for p in $optional; do
    sudo $cmd "$p" >/dev/null 2>&1 && echo "  + $p" || echo "  - $p not installed (optional)"
  done
}

if [ "${1:-}" != "--no-deps" ]; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update -qq || true
    install_packages "apt-get install -y" "python3 python3-pyqt6" \
      "qt6-wayland python3-gi ffmpeg tesseract-ocr x11-utils wl-clipboard xdg-desktop-portal gstreamer1.0-tools gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly pulseaudio-utils python3-pyqt6.qtmultimedia"
  elif command -v dnf >/dev/null; then
    install_packages "dnf install -y" "python3 python3-pyqt6" \
      "qt6-qtwayland python3-gobject ffmpeg-free tesseract xprop xwininfo wl-clipboard gstreamer1-plugins-base-tools pipewire-gstreamer gstreamer1-plugins-good gstreamer1-plugin-openh264 pulseaudio-utils"
  elif command -v pacman >/dev/null; then
    install_packages "pacman -S --needed --noconfirm" "python python-pyqt6" \
      "qt6-wayland python-gobject ffmpeg tesseract tesseract-data-eng xorg-xprop xorg-xwininfo wl-clipboard gst-plugin-pipewire gst-plugins-good gst-plugins-ugly libpulse qt6-multimedia"
  elif command -v zypper >/dev/null; then
    install_packages "zypper install -y" "python3 python3-PyQt6" \
      "python3-gobject ffmpeg tesseract-ocr xprop xwininfo wl-clipboard gstreamer-plugin-pipewire gstreamer-plugins-good pulseaudio-utils"
  else
    echo "Unknown package manager. Install these yourself: Python 3 with PyQt6, ffmpeg, tesseract, wl-clipboard, pactl."
  fi
fi

if ! python3 -c "import PyQt6.QtWidgets" 2>/dev/null; then
  echo "PyQt6 is missing. Install it (package python3-pyqt6, or: pip install --user PyQt6) and run this again."
  exit 1
fi

APP="$HOME/.local/share/screenX"
rm -rf "$APP/screenx"
mkdir -p "$APP" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
cp -R screenx "$APP/screenx"
find "$APP/screenx" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp run.sh "$APP/run.sh"
chmod +x "$APP/run.sh"
if [ -f ../Assets/screenX.png ]; then
  cp ../Assets/screenX.png "$APP/screenx/icon.png"
  cp ../Assets/screenX.png "$HOME/.local/share/icons/hicolor/256x256/apps/screenx.png"
fi
if [ -d ../Assets/tray ]; then
  cp -R ../Assets/tray "$APP/screenx/tray"
fi
if [ -f ../Assets/shutter.wav ]; then
  cp ../Assets/shutter.wav "$APP/screenx/shutter.wav"
fi
ln -sf "$APP/run.sh" "$HOME/.local/bin/screenx"

cat > "$HOME/.local/share/applications/screenx.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=screenX
Comment=Snips, markup, screen recording, colour picker and text extractor
Exec="$APP/run.sh"
Icon=screenx
Terminal=false
Categories=Utility;Graphics;
Keywords=screenshot;snip;capture;record;
StartupWMClass=screenx
Actions=snip;record;

[Desktop Action snip]
Name=New Snip
Exec="$APP/run.sh" --snip

[Desktop Action record]
Name=Record Screen
Exec="$APP/run.sh" --record
DESKTOP
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo
echo "screenX is installed. Start it from the applications menu, or run:  screenx"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(~/.local/bin is not on your PATH yet: log out and in again, or run $APP/run.sh)";; esac
