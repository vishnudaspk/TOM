"""TOM Main Entry Point ÔÇö Production Application Supervisor.

Composes all Phase 0-13 subsystems into a supervised async application (TomApp)
with clean startup, shutdown, signal handling, and operating modes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
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
from tom.voice.debug_trace import create_debug_trace_manager
from tom.voice.tts import TTSConfig

if TYPE_CHECKING:
    from tom.ipc.transport import TransportProtocol
    from tom.voice.debug_trace import DebugTraceManager
    from tom.voice.streaming_stt import StreamingSTTProvider
    from tom.voice.streaming_tts import StreamingTTSProvider

logger = get_logger(__name__, component="tom.main")


def _configure_stdio() -> None:
    """Force UTF-8 text streams so redirected pipes never receive cp1252 bytes."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream is sys.stdin:
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


def _log_background_task_failure(task: asyncio.Task[Any]) -> None:
    """Log an unexpected background-task failure without stopping the app."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "background_task_failed",
            task=task.get_name(),
            error=str(exc),
        )


@dataclass
class TomAppArgs:
    """Parsed CLI arguments for TomApp."""

    config_path: str | None
    mode: str
    headless: bool
    debug: bool
    no_engine: bool
    verbose: bool = False
    bench_wav: str | None = None


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
        self._control_api: Any = None
        self._stt_provider: Any = None
        self._tts_provider: Any = None
        self._microphone_loop: Any = None
        self._voice_operations_lock = asyncio.Lock()
        self._voice_last_error: str | None = None
        self._voice_task: asyncio.Task[Any] | None = None

        # Dependency injection seams for testing
        self._injected_engine_client: EngineClient | None = None
        self._injected_lifecycle: LifecycleManager | None = None
        self._injected_transport: TransportProtocol | None = None
        self._injected_stt: StreamingSTTProvider | None = None
        self._injected_tts: StreamingTTSProvider | None = None
        self._injected_turn_arbitrator: Any | _DefaultArbiterSentinel = _DEFAULT_ARBITER

        # Debug trace manager (voice mode)
        self._debug_trace: DebugTraceManager | None = None

    # ------------------------------------------------------------------
    # Configuration & Setup
    # ------------------------------------------------------------------

    def _parse_args(self, argv: list[str] | None = None) -> TomAppArgs:
        """Parse CLI arguments."""
        parser = argparse.ArgumentParser(
            prog="tom",
            description="TOM - Personal AI Assistant",
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
            "--service",
            dest="mode",
            action="store_const",
            const="service",
            help="Run headless under an authenticated local management API",
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
        parser.add_argument(
            "--bench-voice",
            dest="bench_wav",
            metavar="WAV",
            default=None,
            help="Run offline voice pipeline benchmark on a WAV file and exit",
        )

        ns = parser.parse_args(argv)

        mode = "bench_voice" if ns.bench_wav else ns.mode
        if ns.bench_wav:
            ns.no_engine = True

        # Validate flag combinations
        if ns.no_engine and mode == "voice":
            parser.error(
                "--voice requires engine connection; use --cli --no-engine or --health --no-engine"
            )
        if ns.no_engine and mode not in ("cli", "health", "bench_voice"):
            parser.error("--no-engine only valid with --cli, --health, or --bench-voice")

        return TomAppArgs(
            config_path=ns.config_path,
            mode=mode,
            headless=ns.headless,
            debug=ns.debug,
            no_engine=ns.no_engine,
            verbose=ns.verbose,
            bench_wav=ns.bench_wav,
        )

    async def _start_control_api(self) -> None:
        """Start the authenticated local management API when service mode is selected."""
        raw_port = os.environ.get("TOM_CONTROL_PORT", "")
        token = os.environ.get("TOM_CONTROL_TOKEN", "")
        if self._mode != "service" and not (raw_port and token):
            return
        from tom.desktop.control_api import ControlApiServer

        if self._mode == "service" and not (raw_port and token):
            raise RuntimeError("Service mode requires TOM_CONTROL_PORT and TOM_CONTROL_TOKEN")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise RuntimeError("TOM_CONTROL_PORT must be a valid TCP port") from exc
        self._control_api = ControlApiServer(
            port=port,
            token=token,
            status_provider=self._management_status,
            tools_provider=self._management_tools,
            memory_provider=self._management_memory,
            shutdown_callback=self._shutdown_event.set,
            voice_operation=self._management_voice_operation,
            cli_input_callback=self._handle_cli_input,
        )
        await self._control_api.start()
        logger.info("control_api_started", host="127.0.0.1", port=port)

    def _handle_cli_input(self, line: str) -> None:
        if self._text_console is not None:
            self._text_console.submit_input(line)
        else:
            raise RuntimeError("CLI console is not active in this session")

    async def _management_engine_status(self) -> dict[str, Any]:
        if self._engine_client is None:
            return {"state": "not_configured"}
        try:
            snapshot = await asyncio.wait_for(
                self._engine_client.get_system_snapshot(), timeout=1.5
            )
            return {"state": "ready", "telemetry": snapshot.model_dump(mode="json")}
        except Exception as exc:
            return {"state": "unavailable", "error": type(exc).__name__}

    async def _management_model_status(self) -> dict[str, Any]:
        if self._model_provider is None:
            return {"state": "unavailable"}
        try:
            healthy = await asyncio.wait_for(self._model_provider.check_health(), timeout=1.5)
            return {
                "state": "ready" if healthy else "unavailable",
                "endpoint": "http://127.0.0.1:65400/v1",
            }
        except Exception as exc:
            return {"state": "unavailable", "error": type(exc).__name__}

    async def _management_audio_status(self) -> dict[str, Any]:
        if self._engine_client is None:
            return {"state": "unavailable"}
        try:
            audio = await asyncio.wait_for(
                self._engine_client.get_audio_status(), timeout=1.5
            )
            return audio.model_dump(mode="json")
        except Exception as exc:
            return {"state": "unavailable", "error": type(exc).__name__}

    def _voice_lifecycle(self) -> tuple[str, str | None]:
        if self._voice_last_error:
            return "failed", self._voice_last_error
        core = self._interaction_core
        if core is None:
            return ("starting", None) if self._mode == "voice" else ("stopped", None)
        pipeline_state = getattr(getattr(core, "state", None), "value", None)
        if pipeline_state == "error":
            return "failed", "Voice pipeline reported an error state"
        if not getattr(core, "is_running", False):
            if getattr(core, "is_stopped", False):
                return "stopped", None
            return "starting", None
        if self._microphone_running():
            return "ready", None
        if pipeline_state in (None, "idle"):
            return "starting", None
        return "degraded", "Voice pipeline is running but the microphone is not capturing"

    async def _management_status(self) -> dict[str, Any]:
        engine, model, audio = await asyncio.gather(
            self._management_engine_status(),
            self._management_model_status(),
            self._management_audio_status(),
        )
        mic_config = getattr(self._microphone_loop, "config", None)
        voice = self._voice_status_snapshot()
        voice.update(
            {
                "microphone": {
                    "state": (
                        "capturing"
                        if audio.get("capture_active")
                        else "idle"
                        if self._engine_client
                        else "unavailable"
                    ),
                    "device": getattr(mic_config, "capture_device", None),
                    "sample_rate": getattr(mic_config, "sample_rate", 16000),
                    "channels": getattr(mic_config, "channels", 1),
                },
                "audio": audio,
                "captured_samples_scope": "current_capture_session",
                "vad": {
                    "state": "configured" if self._microphone_loop else "not_started",
                    "implementation": (
                        type(getattr(self._microphone_loop, "_vad", None)).__name__
                        if self._microphone_loop
                        else None
                    ),
                },
                "wakeword": {
                    "enabled": (
                        getattr(self._config.voice, "wakeword_enabled", True)
                        if self._config
                        else True
                    ),
                    "phrase": (
                        getattr(self._config.voice, "wakeword_phrase", "hey tom")
                        if self._config
                        else "hey tom"
                    ),
                },
                "turn_detection": {
                    "smart_turn_enabled": (
                        getattr(self._config.voice, "smart_turn_enabled", True)
                        if self._config
                        else True
                    )
                },
            }
        )
        return {
            "mode": self._mode,
            "python": {
                "state": "ready" if self._orchestrator is not None else "starting",
                "runtime": "TOM Python Runtime",
                "mode": self._mode,
            },
            "engine": engine,
            "model": model,
            "voice": voice,
        }

    @staticmethod
    def _provider_snapshot(provider: Any) -> dict[str, Any]:
        if provider is None:
            return {"state": "not_initialized"}
        try:
            info = provider.get_model_info()
            return {**info, "state": "loaded" if info.get("loaded") else "unloaded"}
        except Exception:
            return {"state": "configured"}

    def _voice_status_snapshot(self) -> dict[str, Any]:
        lifecycle_state, last_error = self._voice_lifecycle()
        core = self._interaction_core
        return {
            "lifecycle_state": lifecycle_state,
            "last_error": last_error,
            "state": (
                "running"
                if self._microphone_running()
                else "initialized"
                if core is not None
                else "not_started"
            ),
            "pipeline_active": self._voice_pipeline_active(),
            "pipeline_state": getattr(getattr(core, "state", None), "value", None),
            "microphone_running": self._microphone_running(),
            "stt": self._provider_snapshot(self._stt_provider),
            "tts": self._provider_snapshot(self._tts_provider),
        }

    def _microphone_running(self) -> bool:
        running = getattr(self._microphone_loop, "is_running", False)
        return bool(running() if callable(running) else running)

    def _management_tools(self) -> dict[str, Any]:
        tools = []
        for item in default_registry.list_tools():
            schema = item.input_schema
            if hasattr(schema, "model_json_schema"):
                schema = schema.model_json_schema()
            elif not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}, "description": "Schema unavailable"}
            tools.append({
                "name": item.name,
                "description": item.description,
                "category": str(item.category),
                "permission": str(item.permission_level),
                "input_schema": schema,
                "last_execution": "not_tracked",
            })
        return {"tools": tools}

    async def _management_memory(self) -> dict[str, Any]:
        from tom.desktop.control_api import sqlite_readonly_diagnostic

        memory_config = self._config.memory if self._config else None
        db_path = memory_config.sqlite_db_path if memory_config else ""
        sqlite = await asyncio.to_thread(sqlite_readonly_diagnostic, db_path) if db_path else {"integrity": "not_configured"}
        vector: dict[str, Any] = {"state": "not_configured"}
        if memory_config and memory_config.enable_semantic_search:
            host = memory_config.qdrant_host
            if host not in {"127.0.0.1", "localhost", "::1"}:
                vector = {"state": "unsupported", "reason": "Only loopback Qdrant diagnostics are enabled"}
            else:
                import aiohttp

                url = f"http://{host}:{memory_config.qdrant_port}/healthz"
                try:
                    timeout = aiohttp.ClientTimeout(total=1.0)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(url) as response:
                            vector = {"state": "ready" if response.status == 200 else "degraded", "http_status": response.status}
                except (aiohttp.ClientError, TimeoutError) as exc:
                    vector = {"state": "unavailable", "error": type(exc).__name__}
        return {"sqlite": sqlite, "vector": vector}

    async def _management_voice_operation(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform a bounded, existing-provider voice diagnostic or safe lifecycle action."""
        from tom.schemas.voice import SynthesisRequest, TranscriptionRequest

        async with self._voice_operations_lock:
            if operation == "status":
                audio = await self._engine_client.get_audio_status() if self._engine_client else None
                return {
                    **self._voice_status_snapshot(),
                    "audio": audio.model_dump(mode="json") if audio else {"state": "unavailable"},
                }
            if operation == "start_pipeline":
                result = await self._start_managed_voice_pipeline()
                return {"operation": operation, "success": True, "result": result}
            if operation == "stop_pipeline":
                result = await self._stop_managed_voice_pipeline()
                return {"operation": operation, "success": True, "result": result}
            if operation not in {
                "load_stt",
                "unload_stt",
                "test_stt",
                "load_tts",
                "unload_tts",
                "test_tts",
            }:
                raise ValueError("Unsupported voice operation")
            if self._voice_pipeline_active():
                raise RuntimeError(
                    "Stop the active Voice pipeline before independent voice tests or unload operations"
                )
            if operation.endswith("stt"):
                provider = self._stt_provider
                manager_name = "voice.stt"
            else:
                provider = self._tts_provider
                manager_name = "voice.tts"
            if provider is None:
                raise RuntimeError("Voice provider is not configured")
            stt_config = getattr(provider, "_config", None) if manager_name == "voice.stt" else None
            original_fallback = getattr(stt_config, "allow_cpu_fallback", None)
            if stt_config is not None and original_fallback is not None:
                stt_config.allow_cpu_fallback = False

            try:
                if operation.startswith("load_"):
                    info = await self._load_stt_with_policy(provider) if manager_name == "voice.stt" else await asyncio.wait_for(provider.load_model(), timeout=180.0)
                    if self._model_coordinator:
                        await self._model_coordinator.record_ready(manager_name, observed_residency=True)
                    return {"operation": operation, "success": True, "result": info}
                if operation.startswith("unload_"):
                    info = await asyncio.wait_for(provider.unload_model(), timeout=15.0)
                    if manager_name == "voice.stt" and self._resource_manager and self._provider_snapshot(provider).get("device") == "cuda":
                        from tom.resources.types import ModelRole
                        self._resource_manager.clear_resident_model(ModelRole.STT)
                    if self._model_coordinator:
                        await self._model_coordinator.record_unloaded(manager_name, confirmed=False)
                    return {"operation": operation, "success": True, "result": info}
                if operation == "test_tts":
                    text = payload.get("text", "TOM voice synthesis test.")
                    if not isinstance(text, str) or not text.strip() or len(text) > 300:
                        raise ValueError("TTS test text must contain 1 to 300 characters")
                    if self._engine_client is None:
                        raise RuntimeError("Engine playback is unavailable")
                    status = await self._engine_client.get_audio_status()
                    if status.playback_active or status.capture_active:
                        raise RuntimeError("Engine audio is busy; test playback was not started")
                    result = await provider.synthesize(SynthesisRequest(text=text))
                    if self._model_coordinator:
                        await self._model_coordinator.record_ready(manager_name, observed_residency=True)
                    await self._engine_client.play_audio_buffer(
                        samples=result.samples,
                        sample_rate=result.sample_rate,
                        channels=result.channels,
                        device_name=self._config.voice.playback_device if self._config else None,
                    )
                    deadline = asyncio.get_running_loop().time() + 15.0
                    playback_started = False
                    while asyncio.get_running_loop().time() < deadline:
                        status = await self._engine_client.get_audio_status()
                        playback_started = playback_started or status.playback_active
                        if playback_started and not status.playback_active:
                            return {"operation": operation, "success": True, "synthesis_latency_ms": result.latency_ms, "duration_s": result.duration_s, "playback": "completed"}
                        await asyncio.sleep(0.05)
                    raise RuntimeError(f"TTS synthesized in {result.latency_ms} ms but playback did not complete within 15 seconds")
                if operation == "test_stt":
                    if self._engine_client is None:
                        raise RuntimeError("Engine microphone capture is unavailable")
                    await self._load_stt_with_policy(provider)
                    seconds = payload.get("seconds", 3)
                    if not isinstance(seconds, int) or not 1 <= seconds <= 5:
                        raise ValueError("Microphone diagnostic duration must be 1 to 5 seconds")
                    status = await self._engine_client.get_audio_status()
                    if status.capture_active or status.playback_active:
                        raise RuntimeError("Engine audio is busy; microphone test was not started")
                    voice_cfg = self._config.voice if self._config else None
                    await self._engine_client.start_audio_capture(
                        device_name=voice_cfg.capture_device if voice_cfg else None,
                        sample_rate=16000, channels=1, chunk_size=320,
                    )
                    try:
                        await asyncio.sleep(seconds)
                    finally:
                        await self._engine_client.stop_audio_capture()
                    captured = await self._engine_client.get_captured_speech(clear=True)
                    if captured.sample_count == 0:
                        raise RuntimeError("Microphone capture completed but produced no samples")
                    result = await provider.transcribe(TranscriptionRequest(
                        samples=captured.samples,
                        sample_rate=captured.sample_rate,
                        channels=captured.channels,
                    ))
                    if self._model_coordinator:
                        await self._model_coordinator.record_ready(manager_name, observed_residency=True)
                    return {"operation": operation, "success": True, "sample_count": captured.sample_count, "audio_duration_s": result.duration_s, "transcription_latency_ms": result.latency_ms, "text": result.text, "model": result.model_name, "device": self._provider_snapshot(provider).get("device")}
            except Exception as exc:
                if self._model_coordinator and operation.startswith("load_"):
                    try:
                        await self._model_coordinator.record_error(manager_name, f"{type(exc).__name__}: {exc}")
                    except Exception:
                        pass
                raise RuntimeError(f"{operation} failed: {type(exc).__name__}: {exc}") from exc
            finally:
                if stt_config is not None and original_fallback is not None:
                    stt_config.allow_cpu_fallback = original_fallback
        raise ValueError("Unsupported voice operation")

    async def _load_stt_with_policy(self, provider: Any) -> dict[str, Any]:
        """Admit the configured STT device before loading, without CPU fallback."""
        info = self._provider_snapshot(provider)
        if info.get("loaded"):
            return info
        if info.get("device") == "cuda":
            required_vram_mb = (
                self._config.models.stt.vram_required_mb
                if self._config is not None
                else 0
            )
            if required_vram_mb <= 0:
                raise RuntimeError(
                    "CUDA STT load rejected because models.stt.vram_required_mb is not configured"
                )
            if self._resource_manager is None:
                raise RuntimeError("GPU resource manager is unavailable")
            telemetry = await asyncio.to_thread(self._resource_manager.telemetry.get_telemetry)
            if not telemetry.gpu_available:
                raise RuntimeError("GPU VRAM telemetry is unavailable; CUDA STT load was rejected")
            if not self._resource_manager.check_budget(required_vram_mb=required_vram_mb):
                raise RuntimeError("CUDA STT load rejected by the configured VRAM safety budget")
        result = await asyncio.wait_for(provider.load_model(), timeout=180.0)
        if info.get("device") == "cuda" and self._resource_manager is not None:
            from tom.resources.types import ModelRole
            self._resource_manager.register_resident_model(ModelRole.STT, required_vram_mb)
        return result

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

        from tom.desktop.wheel_server import WheelConfirmationHook

        self._tool_executor = ToolExecutor(
            registry=default_registry,
            confirmation_hook=WheelConfirmationHook(),
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
        # Voice mode applies a token cap to keep spoken replies concise.
        # CLI mode keeps the full 1024-token budget for structured output.
        voice_tokens = (
            self._config.voice.max_response_tokens
            if self._config and self._mode == "voice" and self._config.voice.max_response_tokens > 0
            else 1024
        )
        self._orchestrator = AgentOrchestrator(
            agent=self._agent,
            router=self._router,
            resource_manager=self._resource_manager,
            config=self._config,
            max_response_tokens=voice_tokens,
        )
        logger.info("orchestrator_initialized")

    def _configure_voice_providers(self) -> None:
        """Construct the existing lazy providers without loading their models."""
        if self._injected_stt is not None:
            self._stt_provider = getattr(self._injected_stt, "provider", self._injected_stt)
        else:
            from tom.voice.stt import FasterWhisperSTTProvider, STTConfig

            self._stt_provider = FasterWhisperSTTProvider(config=STTConfig(
                model_name=self._config.models.stt.model_name if self._config else "C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en",
                device=self._config.models.stt.device if self._config else "cuda",
                compute_type=self._config.models.stt.compute_type if self._config else "int8_float16",
            ))
        if self._injected_tts is not None:
            self._tts_provider = getattr(self._injected_tts, "provider", self._injected_tts)
        else:
            from tom.voice.tts import KokoroTTSProvider

            self._tts_provider = KokoroTTSProvider(config=TTSConfig(
                model_path=self._config.models.tts.model_name if self._config else "C:/Users/vishnuu/Projects/TOM/models/kokoro-v0_19.onnx",
                voices_path="C:/Users/vishnuu/Projects/TOM/models/voices.bin" if self._config else "voices.bin",
            ))

    async def _register_voice_models(self) -> None:
        if self._model_coordinator is None:
            return
        from tom.resources.model_manager import ModelRegistration
        from tom.resources.types import ModelRole

        stt_info = self._provider_snapshot(self._stt_provider)
        tts_info = self._provider_snapshot(self._tts_provider)
        await self._model_coordinator.register_model(ModelRegistration(
            model_name="voice.stt",
            role=ModelRole.STT,
            vram_required_mb=(
                self._config.models.stt.vram_required_mb
                if self._config and stt_info.get("device") == "cuda"
                else 0
            ),
            metadata={"model": stt_info.get("model"), "device": stt_info.get("device")},
        ))
        await self._model_coordinator.register_model(ModelRegistration(
            model_name="voice.tts",
            role=ModelRole.GENERAL,
            vram_required_mb=0,
            metadata={"model": tts_info.get("model"), "device": tts_info.get("device")},
        ))

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

        # Health and bench_voice modes only need config, resources, memory - no engine, tools, agent
        if args.mode in ("health", "bench_voice"):
            logger.info("tomapp_setup_complete", mode=self._mode)
            return

        await self._setup_lifecycle()
        self._setup_tools()
        self._setup_model_provider()
        self._setup_router()
        self._setup_agent()
        self._setup_orchestrator()
        self._configure_voice_providers()
        await self._register_voice_models()
        await self._start_control_api()

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
        from tom.desktop.wheel_server import run_wheel_server

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

            # Wheel server: local HTTP bridge for AHK radial menu. It is a
            # background service; its failure must never terminate the app.
            wheel_task: asyncio.Task[Any] | None = None
            if self._orchestrator is not None and self._tool_executor is not None:
                wheel_task = asyncio.create_task(
                    run_wheel_server(self._orchestrator, self._tool_executor),
                    name="wheel_server",
                )
                wheel_task.add_done_callback(_log_background_task_failure)

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._shutdown_event is not None and self._shutdown_event.is_set():
                await console.stop()

            for t in pending:
                t.cancel()
            if wheel_task is not None:
                wheel_task.cancel()
            await asyncio.gather(
                *pending,
                *([wheel_task] if wheel_task is not None else []),
                return_exceptions=True,
            )

            if console_task in done and not console_task.cancelled():
                exc = console_task.exception()
                if exc is not None and not isinstance(exc, (KeyboardInterrupt, EOFError)):
                    raise exc

        except KeyboardInterrupt:
            logger.info("cli_mode_interrupted")
        except EOFError:
            logger.info("cli_mode_eof")
        return 0

    def _build_voice_runtime(self) -> None:
        """Construct the existing Voice pipeline over the shared providers.

        The microphone loop, streaming adapters, turn manager, and
        ``InteractionCore`` are created here so both the ``--voice`` entry point
        and the Authenticated Management API use exactly one voice runtime.
        Neither STT nor Kokoro is loaded by this method.
        """
        from tom.voice.interaction_core import InteractionCore
        from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
        from tom.voice.pipeline import VoicePipelineManager
        from tom.voice.response_policy import ResponsePolicy
        from tom.voice.smart_turn import SmartTurnV3Arbiter
        from tom.voice.streaming_stt import FasterWhisperStreamingAdapter
        from tom.voice.streaming_tts import IncrementalTTSStreamer
        from tom.voice.turn_manager import ConversationTurnManager

        mic_config = MicrophoneConfig(
            sample_rate=16000,
            frame_duration_ms=20,
            silence_duration_ms=self._config.voice.silence_duration_ms if self._config else 600,
            energy_threshold=getattr(self._config.voice, "energy_threshold", 0.003)
            if self._config
            else 0.003,
            capture_device=self._config.voice.capture_device if self._config else None,
        )
        mic_loop = MicrophoneLoop(engine_client=self._engine_client, config=mic_config)

        debug_session = None
        if self._config and self._config.voice.debug_trace:
            self._debug_trace = create_debug_trace_manager(self._config.voice)
            if self._debug_trace:
                debug_session = self._debug_trace.start_session()
                if debug_session:
                    debug_session.log_audio_env(
                        input_device=mic_config.capture_device,
                        output_device=self._config.voice.playback_device,
                    )

        stt_provider = self._injected_stt or FasterWhisperStreamingAdapter(
            provider=self._stt_provider,
            sample_rate=16000,
            mode=self._config.models.stt.mode if self._config else "final_only",
        )

        stt_vram_required_mb = (
            self._config.models.stt.vram_required_mb
            if self._config and self._config.models.stt.device == "cuda"
            else 0
        )
        if self._resource_manager is not None and stt_vram_required_mb > 0:
            from tom.resources.types import ModelRole

            self._resource_manager.register_resident_model(ModelRole.STT, stt_vram_required_mb)

        tts_provider = self._injected_tts or IncrementalTTSStreamer(
            provider=self._tts_provider,
            engine_client=self._engine_client,
            sample_rate=24000,
            max_concurrent_synthesis=2,
            playback_device=self._config.voice.playback_device if self._config else None,
            playback_tail_ms=self._config.voice.playback_tail_ms if self._config else 400,
        )

        turn_manager = ConversationTurnManager()
        policy = ResponsePolicy()
        if self._injected_turn_arbitrator is not _DEFAULT_ARBITER:
            turn_arbitrator = self._injected_turn_arbitrator
        elif not getattr(self._config.voice, "smart_turn_enabled", True):
            logger.info("smart_turn_disabled_by_config_using_vad_endpointing")
            turn_arbitrator = None
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
            debug_trace=debug_session,
            wakeword_enabled=getattr(self._config.voice, "wakeword_enabled", True) if self._config else True,
            wakeword_phrase=getattr(self._config.voice, "wakeword_phrase", "hey tom") if self._config else "hey tom",
            wakeword_timeout_seconds=getattr(self._config.voice, "wakeword_timeout_seconds", 30.0) if self._config else 30.0,
        )
        self._stt_provider = getattr(stt_provider, "provider", stt_provider)
        self._tts_provider = getattr(tts_provider, "provider", tts_provider)
        self._microphone_loop = mic_loop

    def _voice_pipeline_active(self) -> bool:
        core = self._interaction_core
        return core is not None and not getattr(core, "is_stopped", False)

    def _voice_task_completed(self, completed: asyncio.Task[Any]) -> None:
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            self._voice_last_error = f"{type(error).__name__}: {error}"
        if self._voice_task is completed:
            self._voice_task = None

    async def _start_managed_voice_pipeline(self) -> dict[str, Any]:
        """Start the shared Voice pipeline from the management API."""
        if self._engine_client is None:
            raise RuntimeError("Voice pipeline requires the Rust Engine connection")
        if self._voice_pipeline_active():
            raise RuntimeError("Voice pipeline is already active")
        if self._interaction_core is not None or self._voice_task is not None:
            await self._stop_managed_voice_pipeline()
        self._voice_last_error = None
        self._build_voice_runtime()
        core = self._interaction_core
        if core is None:
            raise RuntimeError("Voice pipeline could not be constructed")
        self._voice_task = asyncio.create_task(core.run(), name="managed_voice_pipeline")
        self._voice_task.add_done_callback(self._voice_task_completed)

        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            if self._voice_task.done():
                error = self._voice_task.exception()
                self._voice_last_error = (
                    f"{type(error).__name__}: {error}" if error else self._voice_last_error
                )
                self._interaction_core = None
                raise RuntimeError(self._voice_last_error or "Voice pipeline exited during startup")
            if getattr(core, "is_running", False) and self._microphone_running():
                return self._voice_status_snapshot()
            await asyncio.sleep(0.05)

        await self._stop_managed_voice_pipeline()
        raise RuntimeError("Voice pipeline did not start the microphone within 10 seconds")

    async def _stop_managed_voice_pipeline(self) -> dict[str, Any]:
        """Stop the management-owned Voice pipeline without unloading providers."""
        core = self._interaction_core
        if core is None:
            self._voice_task = None
            return self._voice_status_snapshot()
        await core.stop()
        task = self._voice_task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                pass
        self._voice_task = None
        self._interaction_core = None
        self._microphone_loop = None
        return self._voice_status_snapshot()

    async def _run_voice(self) -> int:
        """Run voice interaction mode with InteractionCore."""
        self._voice_last_error = None
        self._build_voice_runtime()

        # Tray daemon integration (optional desktop UX)
        self._start_tray_daemon()

        logger.info("voice_mode_started", headless=self._args.headless if self._args else False)

        # Run the interaction core supervised by shutdown event
        try:
            voice_task = asyncio.create_task(self._interaction_core.run())
            tasks: list[asyncio.Task[Any]] = [voice_task]
            if self._shutdown_event is not None:
                tasks.append(asyncio.create_task(self._shutdown_event.wait()))

            # Wheel server: local HTTP bridge for AHK radial menu. It is a
            # background service; its failure must never terminate the app.
            from tom.desktop.wheel_server import run_wheel_server

            wheel_task: asyncio.Task[Any] | None = None
            if self._orchestrator is not None and self._tool_executor is not None:
                wheel_task = asyncio.create_task(
                    run_wheel_server(self._orchestrator, self._tool_executor),
                    name="wheel_server",
                )
                wheel_task.add_done_callback(_log_background_task_failure)

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
            if wheel_task is not None:
                wheel_task.cancel()
            await asyncio.gather(
                *pending,
                *([wheel_task] if wheel_task is not None else []),
                return_exceptions=True,
            )

            # Check if voice_task failed with an exception
            if voice_task in done and not voice_task.cancelled():
                exc = voice_task.exception()
                if exc is not None:
                    raise exc

        except KeyboardInterrupt:
            logger.info("voice_mode_interrupted")
        except Exception as exc:
            self._voice_last_error = f"{type(exc).__name__}: {exc}"
            logger.error("voice_mode_error", error=str(exc))
            raise

        return 0

    async def _run_bench_voice(self, wav_path: str | None) -> int:
        """Run offline voice pipeline benchmark on a WAV file (Item 1)."""
        import json
        import os
        import statistics
        import time
        import wave

        import numpy as np

        from tom.schemas.voice import SynthesisRequest, TranscriptionRequest
        from tom.voice.stt import FasterWhisperSTTProvider, STTConfig
        from tom.voice.tts import KokoroTTSProvider, TTSConfig
        from tom.voice.vad import EnergyVADDetector

        if not wav_path or not os.path.exists(wav_path):
            print(f"Error: WAV file not found: {wav_path}", file=sys.stderr)
            return 1

        print(f"=== Voice Pipeline Benchmark: {wav_path} ===")

        # Load WAV
        with wave.open(wav_path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            pcm_bytes = wf.readframes(nframes)

        if sampwidth == 2:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            samples = np.frombuffer(pcm_bytes, dtype=np.float32)
        else:
            print(f"Error: Unsupported sample width {sampwidth}", file=sys.stderr)
            return 1

        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)

        audio_s = len(samples) / float(framerate)
        print(f"Loaded audio: {audio_s:.2f}s, {framerate}Hz, mono")

        # Initialize providers
        stt_cfg = STTConfig(
            model_name=self._config.models.stt.model_name
            if self._config and hasattr(self._config.models, "stt")
            else "C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en",
            device=self._config.models.stt.device if self._config and hasattr(self._config.models, "stt") else "cuda",
            compute_type=self._config.models.stt.compute_type if self._config and hasattr(self._config.models, "stt") else "int8_float16",
        )
        stt = FasterWhisperSTTProvider(config=stt_cfg)
        await stt.check_health()
        model_info = stt.get_model_info()

        tts_cfg = TTSConfig(
            model_path=self._config.models.tts.model_name
            if self._config and hasattr(self._config.models, "tts")
            else "C:/Users/vishnuu/Projects/TOM/models/kokoro-v0_19.onnx",
            voices_path="C:/Users/vishnuu/Projects/TOM/models/voices.bin"
            if self._config and hasattr(self._config.models, "tts")
            else "voices.bin",
        )
        tts = KokoroTTSProvider(config=tts_cfg)
        await tts.check_health()

        vad = EnergyVADDetector()

        runs_data: list[dict[str, Any]] = []
        num_runs = 5
        print(f"Running {num_runs} warmup/benchmark passes (VAD -> STT -> LLM -> TTS)...")

        for run_idx in range(1, num_runs + 1):
            # 1. VAD
            t_vad_0 = time.monotonic()
            frame_sz = 512
            speech_frames = 0
            for i in range(0, len(samples), frame_sz):
                frame = samples[i : i + frame_sz]
                if vad.is_speech(frame.tolist(), sample_rate=framerate):
                    speech_frames += 1
            _vad_ms = (time.monotonic() - t_vad_0) * 1000.0

            # 2. STT
            req = TranscriptionRequest(samples=pcm_bytes, sample_rate=framerate, channels=1)
            t_stt_0 = time.monotonic()
            trans_res = await stt.transcribe(req)
            stt_decode_ms = (time.monotonic() - t_stt_0) * 1000.0
            stt_rtf = (stt_decode_ms / 1000.0) / max(audio_s, 0.001)

            # 3. LLM (probe connection, fallback to mock if llama.cpp not running)
            llm_prompt_tokens = "NOT_MEASURED"
            llm_cached_tokens = "NOT_MEASURED"
            llm_text = trans_res.text or "What is the current time?"

            # 4. TTS
            synth_req = SynthesisRequest(text=llm_text)
            t_tts_0 = time.monotonic()
            synth_res = await tts.synthesize(synth_req)
            tts_synth_ms = (time.monotonic() - t_tts_0) * 1000.0
            tts_audio_s = len(synth_res.samples) / 24000.0
            tts_rtf = (tts_synth_ms / 1000.0) / max(tts_audio_s, 0.001)

            run_log = {
                "run": run_idx,
                "stt_audio_s": round(audio_s, 3),
                "stt_decode_ms": round(stt_decode_ms, 2),
                "stt_rtf": round(stt_rtf, 3),
                "stt_model": model_info.get("model"),
                "stt_device": model_info.get("device"),
                "stt_compute_type": model_info.get("compute_type"),
                "tts_synth_ms": round(tts_synth_ms, 2),
                "tts_audio_s": round(tts_audio_s, 3),
                "tts_rtf": round(tts_rtf, 3),
                "llm_prompt_tokens": llm_prompt_tokens,
                "llm_cached_tokens": llm_cached_tokens,
                "transcript": trans_res.text,
            }
            runs_data.append(run_log)
            print(
                f"Run {run_idx}: STT={stt_decode_ms:.1f}ms (RTF={stt_rtf:.2f}), "
                f"TTS={tts_synth_ms:.1f}ms (RTF={tts_rtf:.2f}), "
                f"Transcript=\"{trans_res.text}\""
            )

        # Compute summary
        stt_decodes = [r["stt_decode_ms"] for r in runs_data]
        tts_synths = [r["tts_synth_ms"] for r in runs_data]
        stt_rtfs = [r["stt_rtf"] for r in runs_data]
        tts_rtfs = [r["tts_rtf"] for r in runs_data]

        summary = {
            "wav_path": wav_path,
            "audio_duration_s": round(audio_s, 3),
            "runs": num_runs,
            "stt_decode_ms": {
                "min": min(stt_decodes),
                "median": statistics.median(stt_decodes),
                "max": max(stt_decodes),
            },
            "stt_rtf": {
                "min": min(stt_rtfs),
                "median": statistics.median(stt_rtfs),
                "max": max(stt_rtfs),
            },
            "tts_synth_ms": {
                "min": min(tts_synths),
                "median": statistics.median(tts_synths),
                "max": max(tts_synths),
            },
            "tts_rtf": {
                "min": min(tts_rtfs),
                "median": statistics.median(tts_rtfs),
                "max": max(tts_rtfs),
            },
            "per_turn_records": runs_data,
        }

        print("\n=== SUMMARY (min / median / max) ===")
        print(f"STT decode ms: {summary['stt_decode_ms']['min']:.1f} / {summary['stt_decode_ms']['median']:.1f} / {summary['stt_decode_ms']['max']:.1f}")
        print(f"STT RTF:       {summary['stt_rtf']['min']:.3f} / {summary['stt_rtf']['median']:.3f} / {summary['stt_rtf']['max']:.3f}")
        print(f"TTS synth ms:  {summary['tts_synth_ms']['min']:.1f} / {summary['tts_synth_ms']['median']:.1f} / {summary['tts_synth_ms']['max']:.1f}")
        print(f"TTS RTF:       {summary['tts_rtf']['min']:.3f} / {summary['tts_rtf']['median']:.3f} / {summary['tts_rtf']['max']:.3f}")

        # Save to docs/perf/
        out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "perf")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"bench_voice_baseline_{int(time.time())}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"\nSaved baseline report to: {out_file}")

        return 0

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _run_service(self) -> int:
        """Run the headless service and host the shared Command Wheel backend."""
        from tom.desktop.wheel_server import run_wheel_server

        logger.info("service_mode_started", ready=self._orchestrator is not None)
        wheel_task: asyncio.Task[Any] | None = None
        if self._orchestrator is not None and self._tool_executor is not None:
            wheel_task = asyncio.create_task(
                run_wheel_server(self._orchestrator, self._tool_executor),
                name="wheel_server",
            )
            wheel_task.add_done_callback(_log_background_task_failure)
        try:
            if self._shutdown_event is not None:
                await self._shutdown_event.wait()
        finally:
            if wheel_task is not None:
                wheel_task.cancel()
                await asyncio.gather(wheel_task, return_exceptions=True)
        return 0

    async def _shutdown(self) -> None:
        """Graceful teardown of all subsystems in reverse order."""
        logger.info("shutdown_initiated")

        if self._control_api is not None:
            try:
                await self._control_api.close()
            except Exception as exc:
                logger.warning("control_api_close_error", error=str(exc))
            self._control_api = None

        # 0. Stop desktop tray daemon
        if self._tray is not None:
            try:
                self._tray.stop()
                logger.info("tray_daemon_stopped")
            except Exception as exc:
                logger.warning("tray_daemon_stop_error", error=str(exc))
            self._tray = None

        # Close debug trace manager
        if self._debug_trace:
            try:
                self._debug_trace.close()
                logger.info("debug_trace_closed")
            except Exception as exc:
                logger.warning("debug_trace_close_error", error=str(exc))
            self._debug_trace = None

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
        _configure_stdio()
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
            elif args.mode == "service":
                return await self._run_service()
            elif args.mode == "bench_voice":
                return await self._run_bench_voice(args.bench_wav)
            else:
                logger.error("invalid_mode", mode=args.mode)
                return 2

        except LifecycleError as exc:
            logger.error("lifecycle_error", error=str(exc))
            print(f"TOM startup failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            logger.error("tomapp_fatal_error", error=str(exc), exc_info=True)
            print(f"Fatal error: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return 1
        finally:
            await self._shutdown()


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point declared in pyproject.toml."""
    _configure_stdio()
    app = TomApp()
    try:
        return asyncio.run(app.run(argv))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
