"""Voice Interaction Layer and Session Coordination.

Architecture (Decision 045):
- Outer interaction layer wrapping VoicePipelineManager.
- Maintains multi-turn conversational context in an ephemeral, bounded ConversationHistory.
- Does NOT dump raw voice turns into persistent memory (SQLite/Qdrant), preventing vector space
  pollution and high-frequency embedding overhead.
- Supports background/urgent announcements (voice.announce) and diagnostic inspection (voice.status).
- Passes user transcript into Agent or AgentOrchestrator, records Role.USER and Role.ASSISTANT
  messages into history, and sends the assistant response through SpeechFormatter and TTS.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from tom.schemas.agent import ConversationHistory, Message, Role
from tom.schemas.voice import (
    SynthesisRequest,
    VoicePipelineState,
    VoiceTurnResult,
)
from tom.telemetry.logging import get_logger
from tom.voice.pipeline import VoicePipelineError, VoicePipelineManager

if TYPE_CHECKING:
    from tom.agents.base import Agent
    from tom.agents.orchestrator import AgentOrchestrator

logger = get_logger(__name__)


class VoiceInteractionManager:
    """Session and interaction coordinator sitting above VoicePipelineManager.

    Responsibilities:
    1. Holds reference to VoicePipelineManager and an ephemeral ConversationHistory.
    2. Coordinates conversation turns: Appends user transcript as Role.USER, invokes
       agent/orchestrator, appends assistant response as Role.ASSISTANT.
    3. Provides direct `announce(message)` execution with SpeechFormatter, TTS synthesis,
       and playback through EngineClient (with optional barge-in / interrupt support).
    4. Provides diagnostic `get_status()` querying pipeline state and engine audio device health.
    5. Rate-limits background announcements to avoid audio spam.
    """

    def __init__(
        self,
        pipeline_manager: VoicePipelineManager,
        agent: Agent | AgentOrchestrator | None = None,
        history: ConversationHistory | None = None,
        min_announce_interval_seconds: float = 1.0,
    ) -> None:
        self._pipeline = pipeline_manager
        self._agent = agent
        self._history = history if history is not None else ConversationHistory(max_messages=100)
        self._min_announce_interval = min_announce_interval_seconds
        self._last_announce_time: float = 0.0

    @property
    def pipeline(self) -> VoicePipelineManager:
        """Return the underlying VoicePipelineManager."""
        return self._pipeline

    @property
    def history(self) -> ConversationHistory:
        """Return the ephemeral session ConversationHistory."""
        return self._history

    @property
    def state(self) -> VoicePipelineState:
        """Return current pipeline state."""
        return self._pipeline.state

    async def run_turn(
        self,
        audio_samples: list[float] | None = None,
        sample_rate: int = 16000,
        raise_on_error: bool = True,
    ) -> VoiceTurnResult:
        """Execute a coordinated multi-turn voice interaction.

        1. Runs the underlying VoicePipelineManager turn (Capture -> STT -> Agent -> TTS -> Playback).
        2. Automatically records transcript and response into session ConversationHistory.
        """
        # Define turn execution callback if agent is provided and pipeline has no custom handler
        original_handler = self._pipeline._agent_handler
        if self._agent is not None:
            self._pipeline._agent_handler = self._handle_agent_step

        try:
            result = await self._pipeline.run_turn(
                audio_samples=audio_samples,
                sample_rate=sample_rate,
                raise_on_error=raise_on_error,
            )
            # If agent handler wasn't routed through _handle_agent_step (e.g. mock pipeline or pre-set handler),
            # ensure valid turns are recorded in history if not already present.
            if result.transcript and not result.interrupted and not result.error:
                # Check if last message matches
                messages = self._history.get_messages()
                if not messages or messages[-1].content != result.response_text:
                    if not (messages and messages[-1].content == result.transcript):
                        self._history.append(Message(role=Role.USER, content=result.transcript))
                    if result.response_text:
                        self._history.append(
                            Message(role=Role.ASSISTANT, content=result.response_text)
                        )

            return result
        finally:
            self._pipeline._agent_handler = original_handler

    async def _handle_agent_step(self, transcript: str) -> str:
        """Execute agent step and record to ephemeral ConversationHistory."""
        self._history.append(Message(role=Role.USER, content=transcript))

        response_text = ""
        if self._agent is None:
            response_text = f"Echo: {transcript}"
        elif hasattr(self._agent, "run"):
            # AgentOrchestrator or Agent.run
            res = await self._agent.run(transcript)
            response_text = str(res)
        elif hasattr(self._agent, "step"):
            step_res = await self._agent.step(transcript)
            if hasattr(step_res, "text"):
                response_text = str(step_res.text)
            else:
                response_text = str(step_res)
        elif callable(self._agent):
            res = self._agent(transcript)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                response_text = str(await res)
            else:
                response_text = str(res)
        else:
            response_text = f"Received: {transcript}"

        if response_text:
            self._history.append(Message(role=Role.ASSISTANT, content=response_text))

        return response_text

    async def announce(
        self,
        message: str,
        priority: bool = False,
    ) -> bool:
        """Speak an urgent announcement or notification aloud.

        Args:
            message: Text message to speak.
            priority: If True, barge-in / interrupt existing speech if active.

        Returns:
            bool: True if announcement successfully synthesized and played.
        """
        clean_text = message.strip()
        if not clean_text:
            return False

        # Enforce rate-limiting
        now = time.monotonic()
        if now - self._last_announce_time < self._min_announce_interval:
            logger.warning("announce_rate_limited", elapsed=now - self._last_announce_time)
            raise VoicePipelineError(
                f"Announce rate limit exceeded. Must wait at least {self._min_announce_interval:.1f}s between announcements."
            )

        if self._pipeline.is_speaking:
            if priority:
                await self._pipeline.handle_barge_in()
            else:
                raise VoicePipelineError("Voice pipeline is currently speaking.")
        elif not self._pipeline.is_idle and not self._pipeline.is_interrupted:
            raise VoicePipelineError(
                f"Voice pipeline is busy in state '{self._pipeline.state.value}'."
            )

        self._last_announce_time = time.monotonic()

        # Format text for speech
        formatted = self._pipeline._formatter.format(clean_text)
        if not formatted.strip():
            return False

        # Transition pipeline through valid states: IDLE → PROCESSING → SPEAKING.
        # IDLE → SPEAKING is not a valid transition in the state machine matrix.
        if self._pipeline.is_idle or self._pipeline.is_interrupted:
            self._pipeline.transition_to(VoicePipelineState.PROCESSING)
        try:
            synth_req = SynthesisRequest(text=formatted)
            synth_res = await self._pipeline._tts.synthesize(synth_req)

            self._pipeline.transition_to(VoicePipelineState.SPEAKING)
            await self._pipeline._engine.play_audio_buffer(
                samples=synth_res.samples,
                sample_rate=synth_res.sample_rate,
                channels=synth_res.channels,
            )
            return True
        finally:
            # Always return to IDLE from any intermediate state.
            if not self._pipeline.is_idle:
                try:
                    self._pipeline.transition_to(VoicePipelineState.IDLE)
                except Exception:
                    pass  # Best-effort cleanup; do not mask the original exception.

    async def get_status(self) -> dict[str, Any]:
        """Retrieve diagnostic status covering pipeline state, audio engine, and history."""
        audio_status: dict[str, Any] = {}
        try:
            engine_status = await self._pipeline._engine.get_audio_status()
            audio_status = {
                "capture_active": getattr(engine_status, "capture_active", False),
                "playback_active": getattr(engine_status, "playback_active", False),
                "sample_rate": getattr(engine_status, "sample_rate", 16000),
                "buffered_samples": getattr(engine_status, "buffered_samples", 0),
            }
        except Exception as exc:
            audio_status = {"error": str(exc)}

        return {
            "pipeline_state": self._pipeline.state.value,
            "is_idle": self._pipeline.is_idle,
            "is_speaking": self._pipeline.is_speaking,
            "is_listening": self._pipeline.is_listening,
            "history_message_count": len(self._history),
            "audio_engine": audio_status,
        }


__all__ = [
    "VoiceInteractionManager",
]
