"""Unit tests for TOM screen capture service, monitor discovery, and coordinate mapping.

Adheres to Phase 7 Iteration 1 requirements and skills/testing/python-testing:
- Offline deterministic tests via MockCaptureBackend
- Multi-monitor discovery and coordinate mapping
- DPI-aware scaling and virtual desktop coordinate translation
- Zero disk persistence verification
- Pre-capture privacy shield blocking
"""

import time
from pathlib import Path

import pytest
from PIL import Image
from tom.schemas.vision import CapturedFrame, MonitorInfo, ScreenDimensions
from tom.vision.capture import (
    MockCaptureBackend,
    ScreenCaptureService,
)
from tom.vision.privacy import PrivacyShield, SensitiveScreenContentError


@pytest.fixture
def mock_monitors() -> list[MonitorInfo]:
    """Standard multi-monitor test setup: 2 monitors side by side."""
    return [
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
            name="Display 1",
            x=0,
            y=0,
            width=1920,
            height=1080,
            is_primary=True,
            scale_factor=1.0,
        ),
        MonitorInfo(
            index=2,
            name="Display 2",
            x=1920,
            y=0,
            width=1920,
            height=1080,
            is_primary=False,
            scale_factor=1.5,
        ),
    ]


@pytest.fixture
def mock_service(mock_monitors: list[MonitorInfo]) -> ScreenCaptureService:
    """ScreenCaptureService initialized with MockCaptureBackend."""
    backend = MockCaptureBackend(monitors=mock_monitors)
    shield = PrivacyShield(foreground_window_provider=lambda: "Terminal - safe")
    return ScreenCaptureService(privacy_shield=shield, backend=backend, enable_dpi_awareness=False)


def test_monitor_discovery(mock_service: ScreenCaptureService):
    """Verify monitor discovery reports correct count and properties."""
    monitors = mock_service.get_monitors()
    assert len(monitors) == 3
    primary = mock_service.get_primary_monitor()
    assert primary.index == 1
    assert primary.is_primary is True
    assert primary.width == 1920
    assert primary.height == 1080


def test_capture_screen_primary(mock_service: ScreenCaptureService):
    """Verify in-memory screen capture of the primary monitor."""
    frame = mock_service.capture_screen(monitor_index=1)
    assert isinstance(frame, CapturedFrame)
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.channels == 4
    assert frame.format == "RGBA"
    assert frame.is_ephemeral is True
    assert len(frame.raw_bytes) > 0

    # Ensure PIL conversion works in memory
    img = frame.to_pil()
    assert isinstance(img, Image.Image)
    assert img.size == (1920, 1080)


def test_capture_screen_secondary(mock_service: ScreenCaptureService):
    """Verify capturing secondary display."""
    frame = mock_service.capture_screen(monitor_index=2)
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.monitor_index == 2
    assert frame.source_region is not None
    assert frame.source_region.left == 1920


def test_capture_virtual_desktop(mock_service: ScreenCaptureService):
    """Verify capturing full virtual desktop (index 0)."""
    frame = mock_service.capture_screen(monitor_index=0)
    assert frame.width == 3840
    assert frame.height == 1080
    assert frame.monitor_index == 0


def test_capture_region(mock_service: ScreenCaptureService):
    """Verify capturing a bounded subregion."""
    region = ScreenDimensions(left=100, top=150, width=500, height=300)
    frame = mock_service.capture_region(region)
    assert frame.width == 500
    assert frame.height == 300
    assert frame.source_region == region


def test_dpi_coordinate_conversion():
    """Verify logical DPI to physical pixel conversions."""
    # Scale 1.5x (e.g. 150% Windows scaling)
    phys_x, phys_y = ScreenCaptureService.dpi_to_physical(100.0, 200.0, 1.5)
    assert phys_x == 150
    assert phys_y == 300

    log_x, log_y = ScreenCaptureService.physical_to_dpi(150, 300, 1.5)
    assert log_x == 100.0
    assert log_y == 200.0


def test_virtual_coordinate_translation(mock_service: ScreenCaptureService):
    """Verify translating coordinates across multi-monitor setup."""
    # Point (100, 100) on monitor 2 (starts at x=1920) -> virtual (2020, 100)
    vx, vy = mock_service.to_virtual_coordinates(monitor_index=2, local_x=100, local_y=100)
    assert vx == 2020
    assert vy == 100

    # Translate virtual point (2020, 100) back to monitor and local coords
    mon_idx, lx, ly = mock_service.from_virtual_coordinates(2020, 100)
    assert mon_idx == 2
    assert lx == 100
    assert ly == 100


def test_privacy_shield_blocks_capture_screen():
    """Verify capture_screen raises SensitiveScreenContentError when sensitive window is active."""
    blocked_shield = PrivacyShield(foreground_window_provider=lambda: "KeePass Password Safe")
    backend = MockCaptureBackend()
    service = ScreenCaptureService(
        privacy_shield=blocked_shield, backend=backend, enable_dpi_awareness=False
    )

    with pytest.raises(SensitiveScreenContentError) as exc_info:
        service.capture_screen()
    assert "KeePass" in str(exc_info.value)


def test_privacy_shield_blocks_capture_region():
    """Verify capture_region raises SensitiveScreenContentError when sensitive window is active."""
    blocked_shield = PrivacyShield(foreground_window_provider=lambda: "Bank of America - Login")
    backend = MockCaptureBackend()
    service = ScreenCaptureService(
        privacy_shield=blocked_shield, backend=backend, enable_dpi_awareness=False
    )

    with pytest.raises(SensitiveScreenContentError):
        service.capture_region(ScreenDimensions(left=0, top=0, width=100, height=100))


def test_zero_disk_persistence(mock_service: ScreenCaptureService, tmp_path: Path):
    """Verify capture creates no files on disk."""
    initial_files = set(tmp_path.glob("**/*"))
    frame = mock_service.capture_screen()
    _ = frame.to_pil()
    final_files = set(tmp_path.glob("**/*"))
    assert initial_files == final_files


def test_captured_frame_security_serialization():
    """Verify CapturedFrame does not expose raw byte buffers in model_dump or repr."""
    raw = b"\xff\x00\x00\xff" * 100
    frame = CapturedFrame(
        width=10,
        height=10,
        channels=4,
        format="RGBA",
        raw_bytes=raw,
        is_ephemeral=True,
    )

    dump = frame.model_dump()
    assert "raw_bytes" not in dump

    rep = repr(frame)
    assert "b'\\" not in rep
    assert "10x10" in rep
    assert "ephemeral=True" in rep


def test_capture_latency_benchmark(mock_service: ScreenCaptureService):
    """Verify screen capture execution speed (< 30ms engineering target)."""
    iterations = 20
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = mock_service.capture_screen(monitor_index=1)
        times.append((time.perf_counter() - t0) * 1000)

    median_ms = sorted(times)[len(times) // 2]
    # In-memory mock/fast capture should comfortably be < 30ms
    assert median_ms < 30.0
