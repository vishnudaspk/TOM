"""Unit tests for TOM Desktop System Tray Daemon."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from tom.desktop.tray import TrayDaemon, create_tray_icon_image


class TestCreateTrayIconImage:
    """Test programmatic tray icon generation."""

    def test_returns_none_when_pil_unavailable(self) -> None:
        """Should return None when PIL is not available."""
        with patch("tom.desktop.tray.HAS_PIL", False):
            result = create_tray_icon_image()
            assert result is None

    def test_returns_image_when_pil_available(self) -> None:
        """Should return an Image when PIL is available."""
        with patch("tom.desktop.tray.HAS_PIL", True):
            with patch("tom.desktop.tray.Image") as mock_image:
                mock_img = MagicMock()
                mock_image.new.return_value = mock_img
                mock_draw = MagicMock()
                with patch("tom.desktop.tray.ImageDraw") as mock_draw_cls:
                    mock_draw_cls.Draw.return_value = mock_draw
                    result = create_tray_icon_image()
                    assert result is not None
                    mock_image.new.assert_called_once()
                    mock_draw.ellipse.assert_called_once()


class TestTrayDaemonInitialization:
    """Test TrayDaemon initialization and basic properties."""

    def test_initial_state(self) -> None:
        """Test initial state after construction."""
        daemon = TrayDaemon(title="Test TOM")
        assert daemon.title == "Test TOM"
        assert daemon._connection_status == "Disconnected"
        assert daemon._muted is False
        assert daemon.is_running is False
        assert daemon._thread is None
        assert daemon._icon is None

    def test_custom_shutdown_event(self) -> None:
        """Test initialization with custom shutdown event."""
        event = asyncio.Event()
        daemon = TrayDaemon(shutdown_event=event)
        assert daemon.shutdown_event is event

    def test_custom_interaction_core(self) -> None:
        """Test initialization with interaction core."""
        core = MagicMock()
        daemon = TrayDaemon(interaction_core=core)
        assert daemon.interaction_core is core

    def test_custom_loop(self) -> None:
        """Test initialization with custom event loop."""
        loop = MagicMock()
        daemon = TrayDaemon(loop=loop)
        assert daemon.loop is loop


class TestTrayDaemonStatusCallback:
    """Test connection status callback functionality."""

    def test_status_callback_used_when_provided(self) -> None:
        """Test that status callback is used when provided."""
        callback = MagicMock(return_value="Custom Status")
        daemon = TrayDaemon(status_callback=callback)

        # Mock engine client to be None so callback is used
        daemon.engine_client = None

        status = daemon._get_connection_status()
        assert status == "Custom Status"
        callback.assert_called_once()

    def test_fallback_to_engine_client_when_no_callback(self) -> None:
        """Test fallback to engine client when no callback provided."""
        daemon = TrayDaemon()
        mock_engine = MagicMock()
        mock_ipc = MagicMock()
        mock_ipc.is_connected = True
        mock_engine._ipc = mock_ipc
        daemon.engine_client = mock_engine

        status = daemon._get_connection_status()
        assert status == "Connected"

    def test_fallback_disconnected_when_no_engine(self) -> None:
        """Test disconnected status when no engine and no callback."""
        daemon = TrayDaemon()
        daemon.engine_client = None
        daemon._status_callback = None

        status = daemon._get_connection_status()
        assert status == "Disconnected"

    def test_engine_client_disconnected(self) -> None:
        """Test status when engine client exists but IPC not connected."""
        daemon = TrayDaemon()
        mock_engine = MagicMock()
        mock_ipc = MagicMock()
        mock_ipc.is_connected = False
        mock_engine._ipc = mock_ipc
        daemon.engine_client = mock_engine

        status = daemon._get_connection_status()
        assert status == "Disconnected"


class TestTrayDaemonMenuCreation:
    """Test menu creation and menu item callbacks."""

    @pytest.fixture
    def daemon(self) -> TrayDaemon:
        """Create a TrayDaemon with mocked dependencies."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray:
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"
            daemon = TrayDaemon()
            daemon._icon = MagicMock()
            yield daemon

    def test_create_menu_returns_none_when_tray_unavailable(self) -> None:
        """Menu creation should return None when HAS_TRAY is False."""
        with patch("tom.desktop.tray.HAS_TRAY", False):
            daemon = TrayDaemon()
            result = daemon._create_menu()
            assert result is None

    def test_mute_toggle_updates_status(self, daemon: TrayDaemon) -> None:
        """Test mute toggle updates muted state and title."""
        daemon._muted = False
        daemon.interaction_core = MagicMock()
        daemon.interaction_core._mic = MagicMock()

        with patch.object(daemon, "_update_title") as mock_update:
            daemon._toggle_mute()

            assert daemon._muted is True
            daemon.interaction_core._mic.mute.assert_called_once()
            mock_update.assert_called_once()

    def test_mute_toggle_unmutes(self, daemon: TrayDaemon) -> None:
        """Test mute toggle from muted to unmuted."""
        daemon._muted = True
        daemon.interaction_core = MagicMock()
        daemon.interaction_core._mic = MagicMock()

        with patch.object(daemon, "_update_title") as mock_update:
            daemon._toggle_mute()

            assert daemon._muted is False
            daemon.interaction_core._mic.unmute.assert_called_once()
            mock_update.assert_called_once()

    def test_mute_toggle_no_mic_handles_gracefully(self, daemon: TrayDaemon) -> None:
        """Test mute toggle when interaction core has no microphone."""
        daemon._muted = False
        daemon.interaction_core = MagicMock()
        daemon.interaction_core._mic = None

        with patch.object(daemon, "_update_title") as mock_update:
            daemon._toggle_mute()

            assert daemon._muted is True
            mock_update.assert_called_once()

    def test_open_dashboard_logs(self, daemon: TrayDaemon, caplog: pytest.LogCaptureFixture) -> None:
        """Test open dashboard logs the request."""
        with caplog.at_level("INFO", logger="tom.desktop.tray"):
            daemon._open_dashboard()
            assert "tray_open_dashboard_requested" in caplog.text


class TestTrayDaemonQuit:
    """Test quit functionality."""

    @pytest.fixture
    def daemon(self) -> TrayDaemon:
        """Create a TrayDaemon with mocked dependencies."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray"):
            daemon = TrayDaemon(shutdown_event=asyncio.Event())
            daemon._icon = MagicMock()
            yield daemon

    def test_quit_stops_icon_and_sets_shutdown(self, daemon: TrayDaemon) -> None:
        """Test quit stops icon and sets shutdown event."""
        daemon._icon = MagicMock()
        shutdown_event = asyncio.Event()
        daemon.shutdown_event = shutdown_event
        daemon.loop = None

        daemon._trigger_quit()

        daemon._icon.stop.assert_called_once()
        assert shutdown_event.is_set()

    def test_quit_handles_icon_stop_error(self, daemon: TrayDaemon, caplog: pytest.LogCaptureFixture) -> None:
        """Test quit handles icon stop error gracefully."""
        daemon._icon = MagicMock()
        daemon._icon.stop.side_effect = Exception("Icon stop failed")
        shutdown_event = asyncio.Event()
        daemon.shutdown_event = shutdown_event

        with caplog.at_level("WARNING", logger="tom.desktop.tray"):
            daemon._trigger_quit()

            assert "tray_icon_stop_error" in caplog.text
            assert shutdown_event.is_set()

    def test_quit_uses_running_loop_when_available(self, daemon: TrayDaemon) -> None:
        """Test quit uses running event loop when available."""
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mock_loop.is_running.return_value = True
        daemon.loop = mock_loop
        daemon.shutdown_event = asyncio.Event()

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            daemon._trigger_quit()

            mock_loop.call_soon_threadsafe.assert_called_once_with(daemon.shutdown_event.set)

    def test_quit_fallback_when_no_running_loop(self, daemon: TrayDaemon) -> None:
        """Test quit falls back to direct set when no running loop."""
        daemon.loop = None
        daemon.shutdown_event = asyncio.Event()

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            daemon._trigger_quit()

            assert daemon.shutdown_event.is_set()


class TestTrayDaemonStartStop:
    """Test start and stop lifecycle."""

    def test_start_returns_false_when_tray_unavailable(self) -> None:
        """Start should return False when HAS_TRAY is False."""
        with patch("tom.desktop.tray.HAS_TRAY", False):
            daemon = TrayDaemon()
            result = daemon.start()
            assert result is False

    def test_start_returns_true_when_already_running(self) -> None:
        """Start should return True when already running."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray:
            mock_icon = MagicMock()
            mock_pystray.Icon.return_value = mock_icon
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"
            daemon = TrayDaemon()
            daemon._thread = MagicMock()
            daemon._thread.is_alive.return_value = True

            result = daemon.start()
            assert result is True

    def test_start_creates_icon_and_thread(self) -> None:
        """Test start creates icon and starts thread."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray, \
             patch("tom.desktop.tray.create_tray_icon_image", return_value=MagicMock()), \
             patch("threading.Thread") as mock_thread_class:

            mock_icon = MagicMock()
            mock_pystray.Icon.return_value = mock_icon
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"

            mock_thread = MagicMock()
            mock_thread.daemon = True
            mock_thread.name = "tom-tray-daemon"
            mock_thread_class.return_value = mock_thread

            daemon = TrayDaemon()
            result = daemon.start()

            assert result is True
            assert daemon._icon is mock_icon
            assert daemon._thread is mock_thread
            assert daemon._thread.daemon is True
            assert daemon._thread.name == "tom-tray-daemon"
            # Verify thread was started with correct target
            mock_thread_class.assert_called_once_with(
                target=mock_icon.run,
                name="tom-tray-daemon",
                daemon=True,
            )
            mock_thread.start.assert_called_once()

    def test_stop_stops_icon_and_joins_thread(self) -> None:
        """Test stop stops icon and joins thread."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray:
            mock_icon = MagicMock()
            mock_pystray.Icon.return_value = mock_icon
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"

            daemon = TrayDaemon()
            daemon._icon = mock_icon
            mock_thread = MagicMock()
            mock_thread.is_alive.return_value = True
            daemon._thread = mock_thread

            daemon.stop()

            mock_icon.stop.assert_called_once()
            mock_thread.join.assert_called_once_with(timeout=1.0)
            assert daemon._icon is None
            assert daemon._thread is None

    def test_stop_handles_icon_stop_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test stop handles icon stop error gracefully."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray:
            mock_icon = MagicMock()
            mock_pystray.Icon.return_value = mock_icon
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"

            daemon = TrayDaemon()
            daemon._icon = mock_icon
            mock_icon.stop.side_effect = Exception("Stop failed")
            daemon._thread = MagicMock()
            daemon._thread.is_alive.return_value = True

            with caplog.at_level("WARNING", logger="tom.desktop.tray"):
                daemon.stop()

                assert "tray_icon_stop_error" in caplog.text

    def test_stop_does_not_join_dead_thread(self) -> None:
        """Test stop does not join already dead thread."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray") as mock_pystray:
            mock_icon = MagicMock()
            mock_pystray.Icon.return_value = mock_icon
            mock_pystray.Menu = MagicMock()
            mock_pystray.MenuItem = MagicMock()
            mock_pystray.Menu.SEPARATOR = "SEPARATOR"

            daemon = TrayDaemon()
            daemon._icon = mock_icon
            daemon._thread = MagicMock()
            daemon._thread.is_alive.return_value = False

            daemon.stop()

            daemon._thread.join.assert_not_called()


class TestTrayDaemonUpdateConnectionStatus:
    """Test connection status update functionality."""

    @pytest.fixture
    def daemon(self) -> TrayDaemon:
        """Create a TrayDaemon with mocked dependencies."""
        with patch("tom.desktop.tray.HAS_TRAY", True), patch("tom.desktop.tray.pystray"):
            daemon = TrayDaemon()
            daemon._icon = MagicMock()
            daemon._icon.title = ""
            yield daemon

    def test_update_connection_status_calls_update_title(self, daemon: TrayDaemon) -> None:
        """Test update_connection_status calls _update_title."""
        with patch.object(daemon, "_update_title") as mock_update:
            daemon.update_connection_status()
            mock_update.assert_called_once()

    def test_update_title_updates_icon_title(self, daemon: TrayDaemon) -> None:
        """Test _update_title updates icon title."""
        daemon._update_title()

        assert "TOM" in daemon._icon.title

    def test_update_title_handles_error_gracefully(self, daemon: TrayDaemon, caplog: pytest.LogCaptureFixture) -> None:
        """Test _update_title handles errors gracefully."""
        # Simulate an error by making title property raise
        def raise_error(*args, **kwargs):
            raise Exception("Title update failed")
        type(daemon._icon).title = property(fset=raise_error)

        with caplog.at_level("WARNING", logger="tom.desktop.tray"):
            daemon._update_title()

            assert "tray_status_update_error" in caplog.text


class TestTrayDaemonEdgeCases:
    """Test edge cases and error handling."""

    def test_muted_property_defaults_false(self) -> None:
        """Test _muted defaults to False."""
        daemon = TrayDaemon()
        assert daemon._muted is False

    def test_connection_status_defaults_disconnected(self) -> None:
        """Test _connection_status defaults to Disconnected."""
        daemon = TrayDaemon()
        assert daemon._connection_status == "Disconnected"

    def test_connection_status_property_accessible(self) -> None:
        """Test _connection_status attribute is accessible."""
        daemon = TrayDaemon()
        assert hasattr(daemon, "_connection_status")

    def test_mic_mute_unmute_methods_called(self) -> None:
        """Test that mic mute/unmute methods are called correctly."""
        daemon = TrayDaemon()
        mock_mic = MagicMock()
        daemon.interaction_core = MagicMock()
        daemon.interaction_core._mic = mock_mic

        # Test mute
        daemon._muted = False
        daemon._toggle_mute()
        mock_mic.mute.assert_called_once()

        # Test unmute
        mock_mic.reset_mock()
        daemon._toggle_mute()
        mock_mic.unmute.assert_called_once()

    def test_no_interaction_core_handles_gracefully(self) -> None:
        """Test mute toggle handles missing interaction core gracefully."""
        daemon = TrayDaemon()
        daemon.interaction_core = None

        with patch.object(daemon, "_update_title") as mock_update:
            daemon._toggle_mute()
            mock_update.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
