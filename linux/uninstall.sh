#!/bin/bash
# Removes screenX for Linux from this user account. Settings, screenshots and recordings are kept.
rm -rf "$HOME/.local/share/screenX"
rm -f "$HOME/.local/bin/screenx" "$HOME/.local/share/applications/screenx.desktop" \
      "$HOME/.local/share/icons/hicolor/256x256/apps/screenx.png" "$HOME/.config/autostart/screenx.desktop"
echo "screenX was removed. Settings stay in ~/.config/screenX."
