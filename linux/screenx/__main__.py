# SPDX-License-Identifier: GPL-3.0-only
import sys

try:
    import PyQt6.QtWidgets  # noqa: F401
except ImportError:
    sys.exit("screenX needs PyQt6. Install it with your package manager (python3-pyqt6 on Debian/Ubuntu, "
             "python3-pyqt6 on Fedora, python-pyqt6 on Arch) or with: pip install --user PyQt6")

from .app import main

sys.exit(main(sys.argv[1:]))
