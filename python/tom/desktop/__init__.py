"""TOM Desktop Package ÔÇö Optional GUI and Console Components.

This package contains optional desktop integration components:
- tray.py: System tray daemon (guarded pystray integration)
- console.py: Interactive text console for --cli mode
"""

from __future__ import annotations

from tom.desktop.console import TextConsole
from tom.desktop.tray import HAS_PIL, HAS_TRAY, TrayDaemon, create_tray_icon_image

__all__ = [
    "HAS_PIL",
    "HAS_TRAY",
    "TextConsole",
    "TrayDaemon",
    "create_tray_icon_image",
]
