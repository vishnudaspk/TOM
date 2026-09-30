"""TOM Deterministic Voice Tools.

Provides typed, deterministic voice tools integrating with VoicePipelineManager / VoiceInteractionManager:
- voice.announce (SAFE): Speak an urgent notification or alert aloud.
- voice.status (SAFE): Query current voice pipeline state and audio device health.

Adheres to:
- Phase 6 Voice Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- Decision 045: Voice as Outer Interaction Layer vs. LLM Tools
- tools/executor and security/permissions decoupled contracts
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

# ---------------------------------------------------------------------------
# Pydantic Input and Output Schemas
# ---------------------------------------------------------------------------


class VoiceAnnounceInput(BaseModel):
    """Input parameters for voice.announce tool."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        ...,
        min_length=1,
        description="Text content of the notification or alert to speak aloud",
    )
    priority: bool = Field(
        default=False,
        description="Whether to interrupt/barge-in active speech to deliver this announcement",
    )


class VoiceAnnounceOutput(BaseModel):
    """Output result for voice.announce tool."""

    model_config = ConfigDict(extra="ignore")

    announced: bool = Field(
        description="Whether the announcement was successfully synthesized and played"
    )
    message: str = Field(description="The announced message text")


class VoiceStatusInput(BaseModel):
    """Input parameters for voice.status tool."""

    model_config = ConfigDict(extra="forbid")


class VoiceStatusOutput(BaseModel):
    """Output result for voice.status tool."""

    model_config = ConfigDict(extra="ignore")

    pipeline_state: str = Field(
        description="Current state of the voice pipeline (IDLE, LISTENING, etc.)"
    )
    is_idle: bool = Field(description="Whether the pipeline is idle and ready for interaction")
    is_speaking: bool = Field(description="Whether the pipeline is currently playing speech")
    is_listening: bool = Field(description="Whether the pipeline is currently capturing audio")
    history_message_count: int = Field(
        default=0, description="Number of messages in ephemeral voice session context"
    )
    audio_engine: dict[str, Any] = Field(
        default_factory=dict, description="Hardware / IPC audio engine status"
    )


# ---------------------------------------------------------------------------
# Tool Factory & Handlers
# ---------------------------------------------------------------------------


def create_voice_tools(
    voice_manager: Any | None = None,
) -> list[ToolDefinition]:
    """Create deterministic voice inspection and announcement tools bound to a voice manager.

    Args:
        voice_manager: Optional injected VoiceInteractionManager or VoicePipelineManager.

    Returns:
        List of ToolDefinition instances (voice.announce and voice.status).
    """

    async def announce(params: VoiceAnnounceInput) -> VoiceAnnounceOutput:
        """Speak an urgent notification or alert aloud."""
        if voice_manager is None:
            return VoiceAnnounceOutput(
                announced=False,
                message=params.message,
            )

        if hasattr(voice_manager, "announce"):
            # VoiceInteractionManager
            res = await voice_manager.announce(params.message, priority=params.priority)
            return VoiceAnnounceOutput(
                announced=bool(res),
                message=params.message,
            )

        # Fallback if VoicePipelineManager directly passed
        if hasattr(voice_manager, "_tts") and hasattr(voice_manager, "_engine"):
            from tom.schemas.voice import SynthesisRequest, VoicePipelineState

            if voice_manager.is_speaking:
                if params.priority:
                    await voice_manager.handle_barge_in()
                else:
                    return VoiceAnnounceOutput(announced=False, message=params.message)

            formatted = (
                voice_manager._formatter.format(params.message)
                if hasattr(voice_manager, "_formatter")
                else params.message
            )
            # Valid transition: IDLE → PROCESSING → SPEAKING (IDLE→SPEAKING is disallowed).
            if voice_manager.is_idle or voice_manager.is_interrupted:
                voice_manager.transition_to(VoicePipelineState.PROCESSING)
            try:
                synth = await voice_manager._tts.synthesize(SynthesisRequest(text=formatted))
                voice_manager.transition_to(VoicePipelineState.SPEAKING)
                await voice_manager._engine.play_audio_buffer(
                    samples=synth.samples,
                    sample_rate=synth.sample_rate,
                    channels=synth.channels,
                )
                return VoiceAnnounceOutput(announced=True, message=params.message)
            finally:
                if not voice_manager.is_idle:
                    try:
                        voice_manager.transition_to(VoicePipelineState.IDLE)
                    except Exception:
                        pass

        return VoiceAnnounceOutput(announced=False, message=params.message)

    async def status(params: VoiceStatusInput) -> VoiceStatusOutput:
        """Query the current voice pipeline state and audio device health."""
        if voice_manager is None:
            return VoiceStatusOutput(
                pipeline_state="UNAVAILABLE",
                is_idle=False,
                is_speaking=False,
                is_listening=False,
                history_message_count=0,
                audio_engine={"available": False},
            )

        if hasattr(voice_manager, "get_status"):
            st = await voice_manager.get_status()
            return VoiceStatusOutput(
                pipeline_state=st.get("pipeline_state", "UNKNOWN"),
                is_idle=st.get("is_idle", False),
                is_speaking=st.get("is_speaking", False),
                is_listening=st.get("is_listening", False),
                history_message_count=st.get("history_message_count", 0),
                audio_engine=st.get("audio_engine", {}),
            )

        # Fallback if pipeline directly injected
        engine_status = {}
        if hasattr(voice_manager, "_engine") and hasattr(voice_manager._engine, "get_audio_status"):
            try:
                eng = await voice_manager._engine.get_audio_status()
                engine_status = {
                    "capture_active": getattr(eng, "capture_active", False),
                    "playback_active": getattr(eng, "playback_active", False),
                }
            except Exception as e:
                engine_status = {"error": str(e)}

        return VoiceStatusOutput(
            pipeline_state=str(getattr(voice_manager, "state", "UNKNOWN")),
            is_idle=bool(getattr(voice_manager, "is_idle", False)),
            is_speaking=bool(getattr(voice_manager, "is_speaking", False)),
            is_listening=bool(getattr(voice_manager, "is_listening", False)),
            history_message_count=0,
            audio_engine=engine_status,
        )

    return [
        ToolDefinition(
            name="voice.announce",
            description="Speak an urgent notification or alert aloud through host audio output.",
            category="voice",
            input_schema=VoiceAnnounceInput,
            output_schema=VoiceAnnounceOutput,
            permission_level=PermissionLevel.SAFE,
            handler=announce,
        ),
        ToolDefinition(
            name="voice.status",
            description="Query the operational state of the voice pipeline and audio hardware health.",
            category="voice",
            input_schema=VoiceStatusInput,
            output_schema=VoiceStatusOutput,
            permission_level=PermissionLevel.SAFE,
            handler=status,
        ),
    ]


def register_voice_tools(
    registry: ToolRegistry | None = None,
    voice_manager: Any | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register deterministic voice inspection and announcement tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry. Defaults to global default_registry.
        voice_manager: Optional injected VoiceInteractionManager or VoicePipelineManager.
        replace: Whether to overwrite existing registrations.

    Returns:
        List of registered ToolDefinition objects.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_voice_tools(voice_manager=voice_manager)
    for t in tools:
        target_registry.register(t, replace=replace)
    return tools


__all__ = [
    "VoiceAnnounceInput",
    "VoiceAnnounceOutput",
    "VoiceStatusInput",
    "VoiceStatusOutput",
    "create_voice_tools",
    "register_voice_tools",
]
