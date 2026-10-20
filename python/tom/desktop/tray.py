"""TOM Desktop System Tray Daemon.

Provides an optional system tray icon and menu for TOM using pystray.
If pystray or Pillow is not installed, the module degrades gracefully without error.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    from tom.core.engine import EngineClient

logger = get_logger(__name__, component="tom.desktop.tray")

# Guarded imports for optional desktop dependencies
try:
    from PIL import Image, ImageDraw

    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    HAS_PIL = False

try:
    import pystray

    HAS_TRAY = True
except ImportError:
    pystray = None  # type: ignore[assignment]
    HAS_TRAY = False


def create_tray_icon_image(
    size: tuple[int, int] = (64, 64),
    color: str = "#4A90E2",
) -> Any | None:
    """Generate a programmatic tray icon image using Pillow."""
    if not HAS_PIL or Image is None or ImageDraw is None:
        return None

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Circular icon with contrasting border
    draw.ellipse([4, 4, size[0] - 4, size[1] - 4], fill=color, outline="#FFFFFF", width=2)
    return image


class TrayDaemon:
    """Optional system tray daemon running pystray in a dedicated background thread.

    Monitors engine connection status and provides quick actions:
    - Mute/unmute microphone (voice mode)
    - Open dashboard (placeholder)
    - Quit application
    """

    def __init__(
        self,
        shutdown_event: asyncio.Event | None = None,
        interaction_core: Any = None,
        engine_client: EngineClient | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        title: str = "TOM",
        status_callback: Callable[[], str] | None = None,
    ) -> None:
        self.shutdown_event = shutdown_event
        self.interaction_core = interaction_core
        self.engine_client = engine_client
        self.loop = loop
        self.title = title
        self._status_callback = status_callback
        self._connection_status: str = "Disconnected"
        self._muted: bool = False
        self._thread: threading.Thread | None = None
        self._icon: Any = None

    @property
    def is_running(self) -> bool:
        """True if the tray daemon thread is active and alive."""
        return self._thread is not None and self._thread.is_alive()

    def _get_connection_status(self) -> str:
        """Get the current engine connection status."""
        if self._status_callback is not None:
            try:
                return self._status_callback()
            except Exception:
                pass
        if self.engine_client is not None:
            try:
                ipc = getattr(self.engine_client, "_ipc", None)
                if ipc is not None and getattr(ipc, "is_connected", False):
                    return "Connected"
            except Exception:
                pass
        return "Disconnected"

    def _create_menu(self) -> Any:
        """Construct the pystray menu."""
        if not HAS_TRAY or pystray is None:
            return None

        def _get_status_text(_item: Any) -> str:
            conn = self._get_connection_status()
            mic = "Muted" if self._muted else "Listening"
            return f"TOM \u2014 {conn} \u2014 {mic}"

        def _get_mute_text(_item: Any) -> str:
            return "Unmute Microphone" if self._muted else "Mute Microphone"

        def _on_mute_toggle(_icon: Any, _item: Any) -> None:
            self._toggle_mute()

        def _on_dashboard(_icon: Any, _item: Any) -> None:
            self._open_dashboard()

        def _on_quit(_icon: Any, _item: Any) -> None:
            self._trigger_quit()

        return pystray.Menu(
            pystray.MenuItem(_get_status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_get_mute_text, _on_mute_toggle),
            pystray.MenuItem("Open Dashboard", _on_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _on_quit),
        )

    def _toggle_mute(self) -> None:
        """Toggle microphone mute status on interaction core."""
        self._muted = not self._muted
        if self.interaction_core is not None:
            mic = getattr(self.interaction_core, "_mic", None)
            if mic is not None:
                if self._muted and hasattr(mic, "mute"):
                    mic.mute()
                elif not self._muted and hasattr(mic, "unmute"):
                    mic.unmute()
        self._update_title()
        logger.info("tray_mute_toggled", muted=self._muted)

    def _open_dashboard(self) -> None:
        """Open the TOM dashboard (placeholder - logs for now)."""
        logger.info("tray_open_dashboard_requested")

    def _trigger_quit(self) -> None:
        """Handle Quit menu item click."""
        logger.info("tray_quit_clicked")
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                logger.warning("tray_icon_stop_error", error=str(exc))

        if self.shutdown_event is not None:
            target_loop = self.loop
            if target_loop is None or target_loop.is_closed():
                try:
                    target_loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass

            if target_loop is not None and not target_loop.is_closed() and target_loop.is_running():
                target_loop.call_soon_threadsafe(self.shutdown_event.set)
            else:
                self.shutdown_event.set()

    def _update_title(self) -> None:
        """Update the tray icon title with current status."""
        if self._icon is not None:
            try:
                self._icon.title = f"{self.title} \u2014 {self._get_connection_status()}"
                if hasattr(self._icon, "update_menu"):
                    self._icon.update_menu()
            except Exception as exc:
                logger.warning("tray_status_update_error", error=str(exc))

    def update_connection_status(self) -> None:
        """Public method to refresh connection status display."""
        self._update_title()

    def start(self) -> bool:
        """Start the system tray icon in a dedicated daemon thread."""
        if not HAS_TRAY or pystray is None:
            logger.info("tray_disabled_pystray_not_installed")
            return False

        if self.is_running:
            return True

        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        image = create_tray_icon_image()
        menu = self._create_menu()
        self._icon = pystray.Icon(name="tom", icon=image, title=self.title, menu=menu)

        self._thread = threading.Thread(
            target=self._icon.run,
            name="tom-tray-daemon",
            daemon=True,
        )
        self._thread.start()
        # Initial status update
        self._update_title()
        logger.info("tray_daemon_started")
        return True

    def stop(self) -> None:
        """Stop and clean up the system tray daemon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as exc:
                logger.warning("tray_icon_stop_error", error=str(exc))
            self._icon = None

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

        logger.info("tray_daemon_stopped")


__all__ = [
    "HAS_PIL",
    "HAS_TRAY",
    "TrayDaemon",
    "create_tray_icon_image",
]
