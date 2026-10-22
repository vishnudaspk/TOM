"""TOM Main Entry Point ÔÇö Production Application Supervisor.

Composes all Phase 0-13 subsystems into a supervised async application (TomApp)
with clean startup, shutdown, signal handling, and operating modes.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.agents.prompts import build_system_prompt
from tom.core.config import load_config
from tom.core.engine import EngineClient
from tom.core.lifecycle import LifecycleError, LifecycleManager
from tom.core.router import IntentRouter
from tom.memory.manager import MemoryManager
from tom.models.providers.http import HttpModelProvider
from tom.resources.manager import ResourceManager
from tom.resources.model_manager import ModelLifecycleCoordinator
from tom.schemas.agent import AgentConfig
from tom.schemas.config import TOMConfig
from tom.telemetry.logging import configure_logging, get_logger
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import default_registry

if TYPE_CHECKING:
    from tom.ipc.transport import TransportProtocol
    from tom.voice.streaming_stt import StreamingSTTProvider
    from tom.voice.streaming_tts import StreamingTTSProvider

logger = get_logger(__name__, component="tom.main")


@dataclass
class TomAppArgs:
    """Parsed CLI arguments for TomApp."""

    config_path: str | None
    mode: str
    headless: bool
    debug: bool
    no_engine: bool
    verbose: bool = False


# Sentinel for default turn_arbitrator injection in TomApp
class _DefaultArbiterSentinel:
    """Private sentinel marking the default turn arbitration injection."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<DEFAULT_ARBITER>"


_DEFAULT_ARBITER: _DefaultArbiterSentinel = _DefaultArbiterSentinel()


class TomApp:
    """Top-level application supervisor for TOM.

    Owns the complete subsystem wiring and lifecycle:
    - Configuration (TOMConfig)
    - LifecycleManager (engine IPC only)
    - ResourceManager + ModelLifecycleCoordinator
    - MemoryManager
    - Tool registry + executor + permissions
    - HttpModelProvider + IntentRouter
    - Agent + AgentOrchestrator
    - Voice pipeline (InteractionCore) when in voice mode
    - Text console when in CLI mode
    - Health check when in health mode
    """

    def __init__(self) -> None:
        self._config: TOMConfig | None = None
        self._lifecycle: LifecycleManager | None = None
        self._resource_manager: ResourceManager | None = None
        self._model_coordinator: ModelLifecycleCoordinator | None = None
        self._memory_manager: MemoryManager | None = None
        self._tool_executor: ToolExecutor | None = None
        self._model_provider: HttpModelProvider | None = None
        self._router: IntentRouter | None = None
        self._agent: Agent | None = None
        self._orchestrator: AgentOrchestrator | None = None
        self._interaction_core: Any = None  # InteractionCore (voice mode)
        self._text_console: Any = None  # TextConsole (CLI mode)
        self._tray: Any = None  # TrayDaemon (desktop UX)
        self._shutdown_event: asyncio.Event | None = None
        self._args: TomAppArgs | None = None
        self._engine_client: EngineClient | None = None
        self._mode: str = "voice"

        # Dependency injection seams for testing
        self._injected_engine_client: EngineClient | None = None
        self._injected_lifecycle: LifecycleManager | None = None
        self._injected_transport: TransportProtocol | None = None
        self._injected_stt: StreamingSTTProvider | None = None
        self._injected_tts: StreamingTTSProvider | None = None
        self._injected_turn_arbitrator: Any | _DefaultArbiterSentinel = _DEFAULT_ARBITER

    # ------------------------------------------------------------------
    # Configuration & Setup
    # ------------------------------------------------------------------

    def _parse_args(self, argv: list[str] | None = None) -> TomAppArgs:
        """Parse CLI arguments."""
        parser = argparse.ArgumentParser(
            prog="tom",
            description="TOM ÔÇö Personal AI Assistant",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--config",
            dest="config_path",
            default=None,
            help="Path to config directory (default: project_root/config)",
        )
        parser.add_argument(
            "--voice",
            dest="mode",
            action="store_const",
            const="voice",
            default="voice",
            help="Voice interaction mode (default)",
        )
        parser.add_argument(
            "--cli",
            dest="mode",
            action="store_const",
            const="cli",
            help="Interactive text console mode",
        )
        parser.add_argument(
            "--health",
            dest="mode",
            action="store_const",
            const="health",
            help="Run bounded health checks and exit",
        )
        parser.add_argument(
            "--headless",
            action="store_true",
            default=False,
            help="Suppress GUI/tray (for CI/remote/headless)",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            default=False,
            help="Enable debug logging",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Enable console JSON logs (default: file-first to logs/tom.log)",
        )
        parser.add_argument(
            "--no-engine",
            action="store_true",
            default=False,
            help="Skip engine connection (no IPC, no audio). Requires --cli or --health.",
        )

        ns = parser.parse_args(argv)

        # Validate flag combinations
        if ns.no_engine and ns.mode == "voice":
            parser.error(
                "--voice requires engine connection; use --cli --no-engine or --health --no-engine"
            )
        if ns.no_engine and ns.mode not in ("cli", "health"):
            parser.error("--no-engine only valid with --cli or --health")

        return TomAppArgs(
            config_path=ns.config_path,
            mode=ns.mode,
            headless=ns.headless,
            debug=ns.debug,
            no_engine=ns.no_engine,
            verbose=ns.verbose,
        )

    async def _load_config(self, config_path: str | None) -> TOMConfig:
        """Load and validate TOM configuration."""
        if config_path:
            from pathlib import Path

            return load_config(config_dir=Path(config_path), force_reload=True)
        return load_config(force_reload=True)

    async def _setup_telemetry(self, debug: bool, verbose: bool = False) -> None:
        """Configure file-first structured logging."""
        level = "DEBUG" if debug else self._config.telemetry.log_level if self._config else "INFO"
        configure_logging(level=level, json_output=True, verbose=verbose)
        logger.info("telemetry_configured", log_level=level, verbose=verbose)

    async def _setup_lifecycle(self) -> None:
        """Initialize and start LifecycleManager (engine IPC)."""
        if self._args.no_engine:
            logger.info("lifecycle_skipped", reason="no_engine_flag")
            return

        self._lifecycle = LifecycleManager(
            config=self._config,
            engine=self._injected_engine_client,
            transport=self._injected_transport,
        )

        try:
            await self._lifecycle.start()
            self._engine_client = self._lifecycle.engine
            logger.info(
                "lifecycle_started",
                engine_version=self._engine_client._ipc._version
                if hasattr(self._engine_client._ipc, "_version")
                else "unknown",
            )
        except LifecycleError as exc:
            logger.error("lifecycle_startup_failed", error=str(exc))
            raise

    def _setup_resources(self) -> None:
        """Initialize resource governor and model lifecycle coordinator."""
        self._resource_manager = ResourceManager(
            config=self._config.resources if self._config else None,
            safety_headroom_mb=500,
        )
        self._model_coordinator = ModelLifecycleCoordinator()
        logger.info("resources_initialized")

    def _setup_memory(self) -> None:
        """Initialize memory manager (SQLite authoritative)."""
        self._memory_manager = MemoryManager(
            config=self._config.memory if self._config else None,
        )
        logger.info("memory_initialized")

    def _setup_tools(self) -> None:
        """Register all 34 canonical tools and create ToolExecutor."""
        setup_default_tools(
            registry=default_registry,
            engine_client=self._engine_client,
            memory_manager=self._memory_manager,
            config=self._config.tools if self._config else None,
        )

        self._tool_executor = ToolExecutor(
            registry=default_registry,
        )
        logger.info("tools_registered", tool_count=len(default_registry.list_tools()))

    def _setup_model_provider(self) -> None:
        """Initialize HTTP model provider (llama.cpp endpoint)."""
        base_url = "http://127.0.0.1:65400/v1"
        if self._config and self._config.models:
            # Could extract from config, but default is the production endpoint
            pass

        self._model_provider = HttpModelProvider(
            base_url=base_url,
        )
        logger.info("model_provider_initialized", base_url=base_url)

    def _setup_router(self) -> None:
        """Initialize intent router with model provider for Tier 2."""
        self._router = IntentRouter(
            model_provider=self._model_provider,
            timeout_seconds=3.5,
        )
        logger.info("router_initialized")

    def _setup_agent(self) -> None:
        """Initialize Agent with mode-specific system prompt and dependencies."""
        system_prompt = build_system_prompt(self._mode)

        agent_config = AgentConfig(
            name="tom-agent",
            system_prompt=system_prompt,
            temperature=self._config.models.reasoning.temperature if self._config else 0.7,
            max_steps=self._config.task.default_max_steps if self._config else 10,
        )

        dependencies = AgentDependencies(
            registry=default_registry,
            executor=self._tool_executor,
            model_provider=self._model_provider,
            memory_manager=self._memory_manager,
        )

        self._agent = Agent(
            config=agent_config,
            dependencies=dependencies,
        )
        logger.info("agent_initialized", mode=self._mode, system_prompt_len=len(system_prompt))

    def _setup_orchestrator(self) -> None:
        """Initialize AgentOrchestrator with agent, router, and resource manager."""
        self._orchestrator = AgentOrchestrator(
            agent=self._agent,
            router=self._router,
            resource_manager=self._resource_manager,
            config=self._config,
        )
        logger.info("orchestrator_initialized")

    async def _setup(self, args: TomAppArgs) -> None:
        """Complete subsystem wiring in dependency order."""
        self._args = args
        self._mode = args.mode

        # Load configuration first
        self._config = await self._load_config(args.config_path)

        # Configure logging
        await self._setup_telemetry(args.debug, args.verbose)

        # Core infrastructure
        self._setup_resources()
        self._setup_memory()

        # Health mode only needs config, resources, memory - no engine, tools, agent
        if args.mode == "health":
            logger.info("tomapp_setup_complete", mode=self._mode)
            return

        await self._setup_lifecycle()
        self._setup_tools()
        self._setup_model_provider()
        self._setup_router()
        self._setup_agent()
        self._setup_orchestrator()

        logger.info("tomapp_setup_complete", mode=self._mode)

    # ------------------------------------------------------------------
    # Mode Implementations
    # ------------------------------------------------------------------

    async def _run_health(self) -> int:
        """Run bounded health checks and return exit code."""
        from scripts.health_check import print_results, run_health_check

        config = self._config or TOMConfig()
        exit_code, results = await run_health_check(config)
        print_results(results)
        return exit_code

    def _start_tray_daemon(self) -> None:
        """Start the system tray daemon if not headless and dependencies available."""
        if self._args and self._args.headless:
            return

        from tom.desktop.tray import HAS_TRAY, TrayDaemon

        if not HAS_TRAY:
            return

        def _connection_status() -> str:
            """Callback to get current engine connection status."""
            if self._engine_client is not None:
                ipc = getattr(self._engine_client, "_ipc", None)
                if ipc is not None and getattr(ipc, "is_connected", False):
                    return "Connected"
            return "Disconnected"

        self._tray = TrayDaemon(
            shutdown_event=self._shutdown_event,
            interaction_core=self._interaction_core,
            engine_client=self._engine_client,
            status_callback=_connection_status,
        )
        self._tray.start()

    async def _run_cli(self) -> int:
        """Run interactive text console mode."""
        from tom.desktop.console import TextConsole

        console = TextConsole(stop_event=self._shutdown_event)
        self._text_console = console

        # Start tray daemon for CLI mode
        self._start_tray_daemon()

        logger.info("cli_mode_started")
        try:
            console_task = asyncio.create_task(console.run(self._orchestrator))
            tasks: list[asyncio.Task[Any]] = [console_task]
            if self._shutdown_event is not None:
                tasks.append(asyncio.create_task(self._shutdown_event.wait()))

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._shutdown_event is not None and self._shutdown_event.is_set():
                await console.stop()

            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if console_task in done and not console_task.cancelled():
                exc = console_task.exception()
                if exc is not None and not isinstance(exc, (KeyboardInterrupt, EOFError)):
                    raise exc

        except KeyboardInterrupt:
            logger.info("cli_mode_interrupted")
        except EOFError:
            logger.info("cli_mode_eof")
        return 0

    async def _run_voice(self) -> int:
        """Run voice interaction mode with InteractionCore."""
        # Import voice components (heavy, only needed in voice mode)
        from tom.voice.interaction_core import InteractionCore
        from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
        from tom.voice.pipeline import VoicePipelineManager
        from tom.voice.response_policy import ResponsePolicy
        from tom.voice.smart_turn import SmartTurnV3Arbiter
        from tom.voice.streaming_stt import FasterWhisperStreamingAdapter
        from tom.voice.streaming_tts import IncrementalTTSStreamer
        from tom.voice.stt import FasterWhisperSTTProvider
        from tom.voice.tts import KokoroTTSProvider
        from tom.voice.turn_manager import ConversationTurnManager

        # Build voice pipeline components
        mic_config = MicrophoneConfig(
            sample_rate=16000,
            frame_duration_ms=20,
            silence_duration_ms=self._config.voice.silence_duration_ms if self._config else 600,
            energy_threshold=getattr(self._config.voice, "energy_threshold", 0.003)
            if self._config
            else 0.003,
            capture_device=self._config.voice.capture_device if self._config else None,
        )

        mic_loop = MicrophoneLoop(
            engine_client=self._engine_client,
            config=mic_config,
        )

        from tom.voice.stt import STTConfig
        from tom.voice.tts import TTSConfig

        stt_provider = self._injected_stt or FasterWhisperStreamingAdapter(
            provider=FasterWhisperSTTProvider(
                config=STTConfig(
                    model_name=self._config.models.stt.model_name
                    if self._config
                    else "C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en",
                    device="cpu",
                    compute_type="int8",
                )
            ),
            sample_rate=16000,
        )

        tts_provider = self._injected_tts or IncrementalTTSStreamer(
            provider=KokoroTTSProvider(
                config=TTSConfig(
                    model_path=self._config.models.tts.model_name
                    if self._config
                    else "C:/Users/vishnuu/Projects/TOM/models/kokoro-v0_19.onnx",
                    voices_path="C:/Users/vishnuu/Projects/TOM/models/voices.bin"
                    if self._config
                    else "voices.bin",
                )
            ),
            engine_client=self._engine_client,
            sample_rate=24000,
            max_concurrent_synthesis=2,
            playback_device=self._config.voice.playback_device if self._config else None,
        )

        turn_manager = ConversationTurnManager()
        policy = ResponsePolicy()
        if self._injected_turn_arbitrator is not _DEFAULT_ARBITER:
            turn_arbitrator = self._injected_turn_arbitrator
        else:
            arbiter = SmartTurnV3Arbiter()
            if arbiter.is_available:
                turn_arbitrator = arbiter
            else:
                logger.info(
                    "smart_turn_unavailable_using_vad_endpointing",
                    reason=arbiter.unavailable_reason,
                )
                turn_arbitrator = None

        pipeline = VoicePipelineManager(
            stt_provider=stt_provider,
            tts_provider=tts_provider,
            engine_client=self._engine_client,
        )

        self._interaction_core = InteractionCore(
            mic_loop=mic_loop,
            stt=stt_provider,
            tts=tts_provider,
            orchestrator=self._orchestrator,
            turn_manager=turn_manager,
            policy=policy,
            engine=self._engine_client,
            pipeline=pipeline,
            turn_arbitrator=turn_arbitrator,
        )

        # Tray daemon integration (optional desktop UX)
        self._start_tray_daemon()

        logger.info("voice_mode_started", headless=self._args.headless if self._args else False)

        # Run the interaction core supervised by shutdown event
        try:
            voice_task = asyncio.create_task(self._interaction_core.run())
            tasks: list[asyncio.Task[Any]] = [voice_task]
            if self._shutdown_event is not None:
                tasks.append(asyncio.create_task(self._shutdown_event.wait()))

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # If shutdown event triggered, signal interaction core to stop cleanly
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                if self._interaction_core is not None:
                    await self._interaction_core.stop()

            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            # Check if voice_task failed with an exception
            if voice_task in done and not voice_task.cancelled():
                exc = voice_task.exception()
                if exc is not None:
                    raise exc

        except KeyboardInterrupt:
            logger.info("voice_mode_interrupted")
        except Exception as exc:
            logger.error("voice_mode_error", error=str(exc))
            raise

        return 0

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown(self) -> None:
        """Graceful teardown of all subsystems in reverse order."""
        logger.info("shutdown_initiated")

        # 0. Stop desktop tray daemon
        if self._tray is not None:
            try:
                self._tray.stop()
                logger.info("tray_daemon_stopped")
            except Exception as exc:
                logger.warning("tray_daemon_stop_error", error=str(exc))
            self._tray = None

        # 1. Stop voice interaction core
        if self._interaction_core is not None:
            try:
                await self._interaction_core.stop()
                logger.info("interaction_core_stopped")
            except Exception as exc:
                logger.warning("interaction_core_stop_error", error=str(exc))

        # 2. Stop text console (no-op if not running)
        if self._text_console is not None:
            try:
                await self._text_console.stop()
                logger.info("text_console_stopped")
            except Exception as exc:
                logger.warning("text_console_stop_error", error=str(exc))

        # 3. Close memory manager
        if self._memory_manager is not None:
            try:
                await self._memory_manager.close()
                logger.info("memory_closed")
            except Exception as exc:
                logger.warning("memory_close_error", error=str(exc))

        # 4. Stop lifecycle manager (engine IPC)
        if self._lifecycle is not None:
            try:
                await self._lifecycle.stop()
                logger.info("lifecycle_stopped")
            except Exception as exc:
                logger.warning("lifecycle_stop_error", error=str(exc))

        logger.info("shutdown_complete")

    # ------------------------------------------------------------------
    # Public Entry Point
    # ------------------------------------------------------------------

    async def run(self, argv: list[str] | None = None) -> int:
        """Main async entry point."""
        self._shutdown_event = asyncio.Event()

        # Install signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown_event.set)
            except (NotImplementedError, AttributeError):
                # Windows ProactorEventLoop doesn't support add_signal_handler
                try:
                    signal.signal(
                        sig,
                        lambda *_: loop.call_soon_threadsafe(self._shutdown_event.set),
                    )
                except Exception:
                    pass

        try:
            args = self._parse_args(argv)
            await self._setup(args)

            # Dispatch to mode
            if args.mode == "health":
                return await self._run_health()
            elif args.mode == "cli":
                return await self._run_cli()
            elif args.mode == "voice":
                return await self._run_voice()
            else:
                logger.error("invalid_mode", mode=args.mode)
                return 2

        except LifecycleError as exc:
            logger.error("lifecycle_error", error=str(exc))
            print(f"TOM startup failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            logger.error("tomapp_fatal_error", error=str(exc))
            print(f"Fatal error: {exc}", file=sys.stderr)
            return 1
        finally:
            await self._shutdown()


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point declared in pyproject.toml."""
    app = TomApp()
    try:
        return asyncio.run(app.run(argv))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
