"""In-memory screen capture and monitor discovery for TOM.

Adheres to Phase 7 Iteration 1 requirements and skills/system-design/resource-management:
- Multi-monitor discovery and DPI-aware coordinate handling
- Zero disk persistence (all frames kept ephemerally in RAM)
- Fast capture (< 30ms engineering target via mss)
- Pre-capture privacy enforcement via PrivacyShield
- Deterministic mock backend support for offline unit tests
"""

from __future__ import annotations

import ctypes
import sys
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from tom.schemas.vision import CapturedFrame, MonitorInfo, ScreenDimensions
from tom.telemetry.logging import get_logger
from tom.vision.privacy import PrivacyShield

if TYPE_CHECKING:
    pass

logger = get_logger(__name__, component="vision.capture")


class ScreenCaptureError(Exception):
    """Raised when screen capture or monitor enumeration fails."""


def enable_windows_dpi_awareness() -> None:
    """Set process-level per-monitor DPI awareness on Windows."""
    if sys.platform != "win32":
        return
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class CaptureBackend(ABC):
    """Abstract interface for screen capture implementations."""

    @abstractmethod
    def get_monitors(self) -> list[MonitorInfo]:
        """Discover connected monitors and virtual desktop dimensions."""

    @abstractmethod
    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        """Capture a monitor (None: primary, 0: full virtual desktop, 1..N: monitor)."""

    @abstractmethod
    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        """Capture a specific bounding box in virtual desktop coordinates."""


class MSSCaptureBackend(CaptureBackend):
    """Fast screen capture using mss."""

    def __init__(self) -> None:
        try:
            import mss

            self._sct = mss.mss()
        except ImportError as exc:
            raise ScreenCaptureError(f"mss is not installed: {exc}") from exc

    def _get_system_scale_factor(self) -> float:
        if sys.platform != "win32":
            return 1.0
        try:
            user32 = ctypes.windll.user32
            dpi = user32.GetDpiForSystem()
            return max(0.5, round(dpi / 96.0, 2))
        except Exception:
            return 1.0

    def get_monitors(self) -> list[MonitorInfo]:
        scale = self._get_system_scale_factor()
        monitors: list[MonitorInfo] = []
        for idx, mon in enumerate(self._sct.monitors):
            # idx 0 is the "all in one" virtual screen in mss
            is_virtual = idx == 0
            is_primary = idx == 1  # Standard primary display index in mss
            monitors.append(
                MonitorInfo(
                    index=idx,
                    name="Virtual Desktop" if is_virtual else f"Display {idx}",
                    x=mon["left"],
                    y=mon["top"],
                    width=mon["width"],
                    height=mon["height"],
                    is_primary=is_primary,
                    scale_factor=scale,
                )
            )
        return monitors

    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        target_idx = 1 if monitor_index is None else monitor_index
        if target_idx < 0 or target_idx >= len(self._sct.monitors):
            raise ScreenCaptureError(
                f"Monitor index {target_idx} out of range (available: 0..{len(self._sct.monitors) - 1})"
            )

        mon = self._sct.monitors[target_idx]
        sct_img = self._sct.grab(mon)
        raw_rgb = sct_img.rgb  # 3-channel RGB bytes
        return CapturedFrame(
            width=sct_img.width,
            height=sct_img.height,
            channels=3,
            format="RGB",
            raw_bytes=raw_rgb,
            monitor_index=target_idx,
            source_region=ScreenDimensions(
                left=mon["left"],
                top=mon["top"],
                width=mon["width"],
                height=mon["height"],
            ),
            is_ephemeral=True,
        )

    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        bbox = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        sct_img = self._sct.grab(bbox)
        raw_rgb = sct_img.rgb
        return CapturedFrame(
            width=sct_img.width,
            height=sct_img.height,
            channels=3,
            format="RGB",
            raw_bytes=raw_rgb,
            source_region=region,
            is_ephemeral=True,
        )


class PillowCaptureBackend(CaptureBackend):
    """Fallback screen capture using Pillow ImageGrab."""

    def __init__(self) -> None:
        try:
            from PIL import ImageGrab

            self._imagegrab = ImageGrab
        except ImportError as exc:
            raise ScreenCaptureError(f"Pillow is not installed: {exc}") from exc

    def get_monitors(self) -> list[MonitorInfo]:
        # Pillow ImageGrab does not natively enumerate multi-monitor geometry on its own
        img = self._imagegrab.grab(all_screens=True)
        return [
            MonitorInfo(
                index=0,
                name="Virtual Desktop",
                x=0,
                y=0,
                width=img.width,
                height=img.height,
                is_primary=True,
                scale_factor=1.0,
            ),
            MonitorInfo(
                index=1,
                name="Display 1",
                x=0,
                y=0,
                width=img.width,
                height=img.height,
                is_primary=True,
                scale_factor=1.0,
            ),
        ]

    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        img = self._imagegrab.grab(all_screens=True if monitor_index == 0 else False)
        return CapturedFrame.from_pil(
            img,
            monitor_index=monitor_index,
            source_region=ScreenDimensions(left=0, top=0, width=img.width, height=img.height),
        )

    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        bbox = (region.left, region.top, region.left + region.width, region.top + region.height)
        img = self._imagegrab.grab(bbox=bbox)
        return CapturedFrame.from_pil(img, source_region=region)


class MockCaptureBackend(CaptureBackend):
    """Deterministic, offline mock backend for testing without display hardware."""

    def __init__(
        self,
        monitors: list[MonitorInfo] | None = None,
        default_color: tuple[int, int, int, int] = (100, 150, 200, 255),
    ) -> None:
        from PIL import Image

        self.monitors = monitors or [
            MonitorInfo(
                index=0,
                name="Virtual Desktop",
                x=0,
                y=0,
                width=3840,
                height=1080,
                is_primary=False,
                scale_factor=1.0,
            ),
            MonitorInfo(
                index=1,
                name="Display 1 (Primary)",
                x=0,
                y=0,
                width=1920,
                height=1080,
                is_primary=True,
                scale_factor=1.0,
            ),
            MonitorInfo(
                index=2,
                name="Display 2 (Secondary)",
                x=1920,
                y=0,
                width=1920,
                height=1080,
                is_primary=False,
                scale_factor=1.25,
            ),
        ]
        self.default_color = default_color
        self._image_factory = Image.new

    def get_monitors(self) -> list[MonitorInfo]:
        return list(self.monitors)

    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        target_idx = 1 if monitor_index is None else monitor_index
        match = next((m for m in self.monitors if m.index == target_idx), None)
        if not match:
            match = self.monitors[0]

        img = self._image_factory("RGBA", (match.width, match.height), self.default_color)
        return CapturedFrame.from_pil(
            img,
            monitor_index=target_idx,
            source_region=ScreenDimensions(
                left=match.x,
                top=match.y,
                width=match.width,
                height=match.height,
            ),
        )

    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        img = self._image_factory("RGBA", (region.width, region.height), self.default_color)
        return CapturedFrame.from_pil(img, source_region=region)


class ScreenCaptureService:
    """Coordinates screen capture, multi-monitor discovery, DPI scaling, and privacy policy."""

    def __init__(
        self,
        privacy_shield: PrivacyShield | None = None,
        backend: str | CaptureBackend = "auto",
        enable_dpi_awareness: bool = True,
    ) -> None:
        if enable_dpi_awareness:
            enable_windows_dpi_awareness()

        self.privacy_shield = privacy_shield or PrivacyShield()

        if isinstance(backend, CaptureBackend):
            self._backend = backend
        elif backend == "mock":
            self._backend = MockCaptureBackend()
        elif backend == "mss":
            self._backend = MSSCaptureBackend()
        elif backend == "pillow":
            self._backend = PillowCaptureBackend()
        elif backend == "auto":
            try:
                self._backend = MSSCaptureBackend()
            except Exception:
                try:
                    self._backend = PillowCaptureBackend()
                except Exception:
                    self._backend = MockCaptureBackend()
        else:
            raise ValueError(f"Unknown capture backend: {backend!r}")

    @property
    def backend(self) -> CaptureBackend:
        return self._backend

    def get_monitors(self) -> list[MonitorInfo]:
        """Discover connected monitors and virtual desktop bounds."""
        return self._backend.get_monitors()

    def get_primary_monitor(self) -> MonitorInfo:
        """Return the primary display geometry."""
        monitors = self.get_monitors()
        for mon in monitors:
            if mon.is_primary:
                return mon
        return monitors[1] if len(monitors) > 1 else monitors[0]

    def capture_screen(self, monitor_index: int | None = None) -> CapturedFrame:
        """Capture the screen in memory. Enforces privacy check beforehand."""
        self.privacy_shield.assert_capture_allowed()
        start = time.perf_counter()
        frame = self._backend.capture_screen(monitor_index=monitor_index)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            "screen_captured",
            width=frame.width,
            height=frame.height,
            monitor=frame.monitor_index,
            duration_ms=round(duration_ms, 2),
        )
        return frame

    def capture_region(self, region: ScreenDimensions) -> CapturedFrame:
        """Capture a bounded rectangle. Enforces privacy check beforehand."""
        self.privacy_shield.assert_capture_allowed()
        start = time.perf_counter()
        frame = self._backend.capture_region(region)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            "region_captured",
            region=region.model_dump(),
            duration_ms=round(duration_ms, 2),
        )
        return frame

    def capture_active_window(self) -> CapturedFrame:
        """Capture the active foreground window. Enforces privacy check beforehand."""
        if sys.platform != "win32":
            raise ScreenCaptureError("capture_active_window is currently only supported on Windows")

        self.privacy_shield.assert_capture_allowed()

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ScreenCaptureError("No active foreground window found")

        # Query true window bounding box
        return self.capture_window(hwnd)

    def capture_window(self, hwnd: int) -> CapturedFrame:
        """Capture a specific window handle (HWND)."""
        if sys.platform != "win32":
            raise ScreenCaptureError("capture_window is currently only supported on Windows")

        self.privacy_shield.assert_capture_allowed()

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        # Prefer DwmGetWindowAttribute for accurate borderless window bounds
        dwmapi = getattr(ctypes.windll, "dwmapi", None)
        dwm_success = False
        if dwmapi is not None:
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            hr = dwmapi.DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if hr == 0:
                dwm_success = True

        if not dwm_success:
            user32 = ctypes.windll.user32
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise ScreenCaptureError(f"Failed to retrieve window rectangle for HWND {hwnd}")

        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise ScreenCaptureError(f"Window bounds invalid or minimized: {width}x{height}")

        region = ScreenDimensions(left=rect.left, top=rect.top, width=width, height=height)
        return self.capture_region(region)

    @staticmethod
    def dpi_to_physical(x: float, y: float, scale_factor: float) -> tuple[int, int]:
        """Convert logical DPI coordinates to physical pixel coordinates."""
        return int(round(x * scale_factor)), int(round(y * scale_factor))

    @staticmethod
    def physical_to_dpi(x: int, y: int, scale_factor: float) -> tuple[float, float]:
        """Convert physical pixel coordinates to logical DPI coordinates."""
        if scale_factor <= 0:
            scale_factor = 1.0
        return round(x / scale_factor, 2), round(y / scale_factor, 2)

    def to_virtual_coordinates(
        self, monitor_index: int, local_x: int, local_y: int
    ) -> tuple[int, int]:
        """Translate monitor-relative pixel coordinates into canonical virtual-desktop space."""
        monitors = self.get_monitors()
        match = next((m for m in monitors if m.index == monitor_index), None)
        if not match:
            return local_x, local_y
        return match.x + local_x, match.y + local_y

    def from_virtual_coordinates(self, virtual_x: int, virtual_y: int) -> tuple[int, int, int]:
        """Find monitor containing the virtual coordinate and return (monitor_index, local_x, local_y)."""
        monitors = [m for m in self.get_monitors() if m.index > 0]
        for m in monitors:
            if m.x <= virtual_x < m.x + m.width and m.y <= virtual_y < m.y + m.height:
                return m.index, virtual_x - m.x, virtual_y - m.y
        # Fallback to primary monitor
        primary = self.get_primary_monitor()
        return primary.index, max(0, virtual_x - primary.x), max(0, virtual_y - primary.y)
