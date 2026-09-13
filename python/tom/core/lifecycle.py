"""Lifecycle coordinator for TOM Python core.

Manages the startup, execution, and shutdown sequence of TOM:
1. Logging configuration
2. IPC client instantiation and connection
3. EngineClient creation and readiness checks (ping, status)
4. Orderly, idempotent shutdown and signal handling
"""

import asyncio
import signal
from typing import Any

from tom.core.engine import EngineClient
from tom.ipc.client import NamedPipeIpcClient
from tom.ipc.errors import LifecycleError
from tom.ipc.transport import TransportProtocol
from tom.schemas.config import TOMConfig
from tom.telemetry.logging import configure_logging, get_logger


class LifecycleManager:
    """Coordinates startup, execution, and graceful shutdown of TOM.

    Architecture:
        LifecycleManager
            ├── EngineClient (high-level API)
            └── NamedPipeIpcClient (IPC transport & reconnection)
    """

    def __init__(
        self,
        config: TOMConfig | None = None,
        *,
        engine: EngineClient | None = None,
        ipc_client: NamedPipeIpcClient | None = None,
        transport: TransportProtocol | None = None,
    ) -> None:
        """Initialize LifecycleManager.

        Args:
            config: Master configuration. Defaults to default TOMConfig().
            engine: Optional pre-constructed EngineClient (e.g. for testing).
            ipc_client: Optional pre-constructed NamedPipeIpcClient.
            transport: Optional custom transport (e.g. MockTransport).
        """
        self._config = config or TOMConfig()
        self._injected_engine = engine
        self._injected_ipc = ipc_client
        self._transport = transport

        self._engine: EngineClient | None = engine
        self._ipc: NamedPipeIpcClient | None = ipc_client or (engine.ipc if engine else None)

        self._started: bool = False
        self._stopped: bool = False
        self._shutdown_event: asyncio.Event | None = None
        self._logger = get_logger(__name__, component="core.lifecycle")

    @property
    def is_running(self) -> bool:
        """True if the manager is started and has not been stopped."""
        return self._started and not self._stopped

    @property
    def is_started(self) -> bool:
        """True if start() completed successfully."""
        return self._started

    @property
    def is_stopped(self) -> bool:
        """True if stop() has completed."""
        return self._stopped

    @property
    def engine(self) -> EngineClient:
        """Return the active EngineClient instance.

        Raises:
            LifecycleError: If accessed before start() or after stop().
        """
        if not self._started or self._stopped or self._engine is None:
            raise LifecycleError(
                "EngineClient is not available: LifecycleManager has not been started or has been stopped"
            )
        return self._engine

    @property
    def config(self) -> TOMConfig:
        """Return the active configuration."""
        return self._config

    async def start(self) -> None:
        """Start up the TOM Python core and verify connectivity with tom-engine.

        Startup sequence:
        1. configure_logging()
        2. Instantiate NamedPipeIpcClient(config.ipc)
        3. Instantiate EngineClient(ipc)
        4. Connect IPC client
        5. Verify engine liveness with ping()
        6. Verify engine health with status()
        7. Log TOM ready

        Raises:
            LifecycleError: If startup fails or if already running.
        """
        if self._started and not self._stopped:
            raise LifecycleError("LifecycleManager is already running")

        # 1. Configure structured logging
        try:
            configure_logging()
        except Exception as exc:
            self._logger.warning("lifecycle_logging_config_warning", error=str(exc))

        self._logger.info(
            "lifecycle_starting",
            pipe=self._config.ipc.pipe_name,
            env=self._config.environment,
        )

        # 2 & 3. Prepare IPC client and EngineClient
        if self._injected_engine is not None:
            self._engine = self._injected_engine
            self._ipc = self._injected_ipc or getattr(self._injected_engine, "ipc", None)
        elif self._injected_ipc is not None:
            self._ipc = self._injected_ipc
            self._engine = EngineClient(self._ipc)
        else:
            self._ipc = NamedPipeIpcClient(config=self._config.ipc, transport=self._transport)
            self._engine = EngineClient(self._ipc)

        # Reset states
        self._stopped = False

        # 4, 5, 6. Connect and verify readiness
        try:
            if self._ipc is not None and hasattr(self._ipc, "connect"):
                if not getattr(self._ipc, "is_connected", False):
                    await self._ipc.connect()

            ping_resp = await self._engine.ping()
            self._logger.debug(
                "lifecycle_engine_ping_success",
                pong=ping_resp.pong,
                version=ping_resp.version,
            )

            status_resp = await self._engine.status()
            self._logger.info(
                "lifecycle_engine_status_success",
                engine=status_resp.engine,
                status=status_resp.status,
            )
        except Exception as exc:
            self._logger.error("lifecycle_startup_failed", error=str(exc))
            # Cleanup any partially initialized resources
            await self._cleanup_on_failure()
            raise LifecycleError(f"TOM startup failed: {exc}") from exc

        self._started = True
        self._logger.info(
            "lifecycle_ready",
            engine=status_resp.engine,
            status=status_resp.status,
            version=ping_resp.version,
        )

    async def _cleanup_on_failure(self) -> None:
        """Clean up partially started resources after startup failure."""
        self._started = False
        try:
            if self._engine is not None and hasattr(self._engine, "close"):
                await self._engine.close()
            elif self._ipc is not None and hasattr(self._ipc, "close"):
                await self._ipc.close()
        except Exception as exc:
            self._logger.warning("lifecycle_cleanup_error", error=str(exc))

    async def stop(self) -> None:
        """Gracefully shut down TOM Python core.

        This method is idempotent: calling it multiple times or calling it
        before start() will never raise an error.
        """
        if self._stopped:
            return

        self._logger.info("lifecycle_stopping")
        self._stopped = True
        self._started = False

        # Trigger any awaiting run_until_shutdown loop
        if self._shutdown_event is not None and not self._shutdown_event.is_set():
            self._shutdown_event.set()

        # Close Engine / IPC client
        try:
            if self._engine is not None and hasattr(self._engine, "close"):
                await self._engine.close()
            elif self._ipc is not None and hasattr(self._ipc, "close"):
                await self._ipc.close()
        except Exception as exc:
            self._logger.warning("lifecycle_close_warning", error=str(exc))

        self._logger.info("lifecycle_stopped")

    def trigger_shutdown(self) -> None:
        """Signal the run_until_shutdown event loop to initiate shutdown."""
        if self._shutdown_event is not None and not self._shutdown_event.is_set():
            self._shutdown_event.set()

    async def run_until_shutdown(self) -> None:
        """Run until a shutdown signal or trigger occurs, then cleanly stop.

        Handles SIGINT and SIGTERM where supported, as well as CancelledError
        and KeyboardInterrupt.
        """
        if not self._started:
            await self.start()

        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()
        installed_signals: list[signal.Signals] = []

        def _handle_signal(*_: Any) -> None:
            self._logger.info("lifecycle_shutdown_signal_received")
            self.trigger_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
                installed_signals.append(sig)
            except (NotImplementedError, AttributeError):
                # ProactorEventLoop on Windows does not support add_signal_handler
                try:
                    signal.signal(sig, _handle_signal)
                except Exception:
                    pass

        try:
            await self._shutdown_event.wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            self._logger.info("lifecycle_wait_interrupted")
        finally:
            for sig in installed_signals:
                try:
                    loop.remove_signal_handler(sig)
                except Exception:
                    pass
            await self.stop()
