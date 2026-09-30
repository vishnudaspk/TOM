"""Voice pipeline orchestration, state machine, and barge-in interruption.

Architecture (Decision 041, Decision 042, Decision 043, Decision 044):
- Orchestrates: Capture -> STT -> Agent/LLM -> SpeechFormatter -> TTS -> Playback
- State machine: IDLE -> LISTENING -> PROCESSING -> SPEAKING
- Barge-in: SPEAKING -> INTERRUPTED (stops playback immediately, cancels active tasks,
  invalidates turn epoch to prevent stale playback, and returns to IDLE/LISTENING)
- Decoupled from concrete models: injects STTProvider, TTSProvider, SpeechFormatter,
  EngineClient, and Agent callback/seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from tom.schemas.voice import (
    SynthesisRequest,
    TranscriptionRequest,
    VoicePipelineState,
    VoiceTurnResult,
)
from tom.telemetry.logging import get_logger
from tom.voice.formatter import SpeechFormatter

if TYPE_CHECKING:
    from tom.core.engine import EngineClient
    from tom.voice.stt import STTProvider
    from tom.voice.tts import TTSProvider

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VoicePipelineError(Exception):
    """Base class for voice pipeline errors."""


class VoicePipelineBusyError(VoicePipelineError):
    """Raised when a new turn is requested while the pipeline is already active."""


class InvalidStateTransitionError(VoicePipelineError):
    """Raised when an invalid state transition is attempted."""


# ---------------------------------------------------------------------------
# State transition matrix
# ---------------------------------------------------------------------------

VALID_PIPELINE_TRANSITIONS: dict[VoicePipelineState, set[VoicePipelineState]] = {
    VoicePipelineState.IDLE: {
        VoicePipelineState.LISTENING,
        VoicePipelineState.PROCESSING,
        VoicePipelineState.ERROR,
    },
    VoicePipelineState.LISTENING: {
        VoicePipelineState.PROCESSING,
        VoicePipelineState.IDLE,
        VoicePipelineState.INTERRUPTED,
        VoicePipelineState.ERROR,
    },
    VoicePipelineState.PROCESSING: {
        VoicePipelineState.SPEAKING,
        VoicePipelineState.IDLE,
        VoicePipelineState.INTERRUPTED,
        VoicePipelineState.ERROR,
    },
    VoicePipelineState.SPEAKING: {
        VoicePipelineState.IDLE,
        VoicePipelineState.INTERRUPTED,
        VoicePipelineState.ERROR,
    },
    VoicePipelineState.INTERRUPTED: {
        VoicePipelineState.IDLE,
        VoicePipelineState.LISTENING,
        VoicePipelineState.ERROR,
    },
    VoicePipelineState.ERROR: {
        VoicePipelineState.IDLE,
    },
}


# ---------------------------------------------------------------------------
# VoicePipelineManager
# ---------------------------------------------------------------------------


class VoicePipelineManager:
    """Manages the full voice interaction lifecycle and barge-in state machine.

    Injected with abstract providers (STTProvider, TTSProvider), the audio engine client,
    the speech formatter, and an agent handler callback.
    """

    def __init__(
        self,
        stt_provider: STTProvider,
        tts_provider: TTSProvider,
        engine_client: EngineClient,
        formatter: SpeechFormatter | None = None,
        agent_handler: Callable[[str], Coroutine[Any, Any, str]] | Any | None = None,
    ) -> None:
        self._stt = stt_provider
        self._tts = tts_provider
        self._engine = engine_client
        self._formatter = formatter or SpeechFormatter()
        self._agent_handler = agent_handler

        self._state: VoicePipelineState = VoicePipelineState.IDLE
        self._state_callbacks: list[
            Callable[[VoicePipelineState, VoicePipelineState, dict[str, Any]], None]
        ] = []

        self._interrupted: bool = False
        self._current_turn_id: int = 0
        self._active_turn_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # State inspection & properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> VoicePipelineState:
        """Current state of the voice pipeline."""
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state == VoicePipelineState.IDLE

    @property
    def is_listening(self) -> bool:
        return self._state == VoicePipelineState.LISTENING

    @property
    def is_processing(self) -> bool:
        return self._state == VoicePipelineState.PROCESSING

    @property
    def is_speaking(self) -> bool:
        return self._state == VoicePipelineState.SPEAKING

    @property
    def is_interrupted(self) -> bool:
        return self._state == VoicePipelineState.INTERRUPTED

    @property
    def is_error(self) -> bool:
        return self._state == VoicePipelineState.ERROR

    # ------------------------------------------------------------------
    # State machine transition & callbacks
    # ------------------------------------------------------------------

    def transition_to(
        self,
        new_state: VoicePipelineState,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Transition the pipeline to a new state with matrix validation.

        Raises:
            InvalidStateTransitionError: If the transition is not allowed.
        """
        if new_state == self._state:
            return

        allowed = VALID_PIPELINE_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid voice pipeline state transition: {self._state} -> {new_state}"
            )

        old_state = self._state
        self._state = new_state
        meta = metadata or {}

        logger.debug(
            "voice_pipeline_state_changed",
            old_state=str(old_state),
            new_state=str(new_state),
            **meta,
        )

        for callback in self._state_callbacks:
            try:
                callback(old_state, new_state, meta)
            except Exception as exc:
                logger.warning(f"Error in state callback: {exc}")

    def add_state_callback(
        self,
        callback: Callable[[VoicePipelineState, VoicePipelineState, dict[str, Any]], None],
    ) -> None:
        """Register a callback for state transition notifications."""
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def remove_state_callback(
        self,
        callback: Callable[[VoicePipelineState, VoicePipelineState, dict[str, Any]], None],
    ) -> None:
        """Unregister a state transition callback."""
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)

    # ------------------------------------------------------------------
    # Agent execution helper
    # ------------------------------------------------------------------

    async def _execute_agent(self, transcript: str) -> str:
        """Invoke the injected agent or handler seam."""
        if self._agent_handler is None:
            return f"Echo: {transcript}"
        if hasattr(self._agent_handler, "step"):
            step_res = await self._agent_handler.step(transcript)
            if hasattr(step_res, "text"):
                return str(step_res.text)
            return str(step_res)
        if hasattr(self._agent_handler, "run"):
            run_res = await self._agent_handler.run(transcript)
            return str(run_res)
        if callable(self._agent_handler):
            res = self._agent_handler(transcript)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                return str(await res)
            return str(res)
        raise VoicePipelineError(f"Unsupported agent_handler type: {type(self._agent_handler)}")

    # ------------------------------------------------------------------
    # Barge-In & Interruption
    # ------------------------------------------------------------------

    async def handle_barge_in(self) -> None:
        """Stop current speech immediately and transition through INTERRUPTED.

        Key requirements (Decision 044):
        1. Call EngineClient.stop_audio_playback() promptly.
        2. Cancel the active synthesis/playback task where appropriate.
        3. Invalidate the turn epoch so stale background tasks cannot play audio.
        4. Transition through INTERRUPTED.
        5. Idempotent and safe for repeated/rapid invocation.
        """
        logger.info("voice_barge_in_triggered", current_state=str(self._state))

        self._interrupted = True
        self._current_turn_id += 1  # Invalidate turn epoch

        # Flush engine playback buffer immediately
        try:
            await self._engine.stop_audio_playback()
        except Exception as exc:
            logger.warning(f"Error stopping audio playback during barge-in: {exc}")

        # Cancel active turn task if running and not self
        active_task = self._active_turn_task
        if active_task is not None and not active_task.done():
            if active_task != asyncio.current_task():
                active_task.cancel()

        # State transition through INTERRUPTED
        if self._state in (
            VoicePipelineState.SPEAKING,
            VoicePipelineState.PROCESSING,
            VoicePipelineState.LISTENING,
        ):
            self.transition_to(VoicePipelineState.INTERRUPTED)

    async def reset(self) -> None:
        """Reset the pipeline to IDLE, stopping all audio and canceling active tasks."""
        self._interrupted = False
        self._current_turn_id += 1

        active_task = self._active_turn_task
        if active_task is not None and not active_task.done():
            if active_task != asyncio.current_task():
                active_task.cancel()

        try:
            await self._engine.stop_audio_playback()
        except Exception:
            pass
        try:
            await self._engine.stop_audio_capture()
        except Exception:
            pass

        if self._state != VoicePipelineState.IDLE:
            self.transition_to(VoicePipelineState.IDLE)

    # ------------------------------------------------------------------
    # Main turn execution
    # ------------------------------------------------------------------

    async def run_turn(
        self,
        audio_samples: list[float] | None = None,
        sample_rate: int = 16000,
        raise_on_error: bool = True,
    ) -> VoiceTurnResult:
        """Execute a full interaction turn: Capture -> STT -> Agent -> TTS -> Playback.

        Args:
            audio_samples: Optional pre-captured audio samples. If None, captures
                audio via EngineClient.start_audio_capture() / get_captured_speech().
            sample_rate: Sample rate for audio_samples (default 16000).
            raise_on_error: If True, raises exceptions on STT/TTS/Agent failures
                after cleaning up state; if False, returns VoiceTurnResult(error=...).

        Returns:
            VoiceTurnResult with transcript, response text, formatted text, and final state.

        Raises:
            VoicePipelineBusyError: If called while another turn is active.
            asyncio.CancelledError: If cancelled by caller (always re-raised).
        """
        # 1. Repeated start protection
        if self._state not in (VoicePipelineState.IDLE, VoicePipelineState.INTERRUPTED):
            raise VoicePipelineBusyError(
                f"Cannot start turn while voice pipeline is in state {self._state}"
            )

        # Clear interrupted state if restarting
        if self._state == VoicePipelineState.INTERRUPTED:
            self.transition_to(VoicePipelineState.IDLE)

        self._interrupted = False
        self._current_turn_id += 1
        turn_id = self._current_turn_id
        self._active_turn_task = asyncio.current_task()

        try:
            # 2. LISTENING — Audio Capture
            self.transition_to(VoicePipelineState.LISTENING)

            if audio_samples is not None:
                samples = audio_samples
                sr = sample_rate
            else:
                await self._engine.start_audio_capture()
                speech = await self._engine.get_captured_speech(clear=True)
                await self._engine.stop_audio_capture()
                samples = speech.samples
                sr = speech.sample_rate

            if self._interrupted or self._current_turn_id != turn_id:
                return VoiceTurnResult(interrupted=True, state=self._state)

            # Empty capture / no-speech handling
            if not samples:
                self.transition_to(VoicePipelineState.IDLE)
                return VoiceTurnResult(
                    transcript="",
                    state=VoicePipelineState.IDLE,
                )

            # 3. PROCESSING — STT + Agent/LLM + Formatting
            self.transition_to(VoicePipelineState.PROCESSING)

            trans_req = TranscriptionRequest(samples=samples, sample_rate=sr)
            trans_res = await self._stt.transcribe(trans_req)
            transcript = trans_res.text.strip()

            if self._interrupted or self._current_turn_id != turn_id:
                return VoiceTurnResult(
                    transcript=transcript,
                    interrupted=True,
                    state=self._state,
                )

            # Empty transcription text
            if not transcript:
                self.transition_to(VoicePipelineState.IDLE)
                return VoiceTurnResult(
                    transcript="",
                    state=VoicePipelineState.IDLE,
                )

            # Agent / LLM invocation
            response_text = await self._execute_agent(transcript)
            raw_response = response_text.strip()

            if self._interrupted or self._current_turn_id != turn_id:
                return VoiceTurnResult(
                    transcript=transcript,
                    response_text=raw_response,
                    interrupted=True,
                    state=self._state,
                )

            if not raw_response:
                self.transition_to(VoicePipelineState.IDLE)
                return VoiceTurnResult(
                    transcript=transcript,
                    response_text="",
                    state=VoicePipelineState.IDLE,
                )

            # Speech Formatting
            formatted_text = self._formatter.format(raw_response)

            if not formatted_text.strip():
                self.transition_to(VoicePipelineState.IDLE)
                return VoiceTurnResult(
                    transcript=transcript,
                    response_text=raw_response,
                    formatted_text="",
                    state=VoicePipelineState.IDLE,
                )

            # 4. SPEAKING — TTS Synthesis + Audio Playback
            self.transition_to(VoicePipelineState.SPEAKING)

            synth_req = SynthesisRequest(text=formatted_text)
            synth_res = await self._tts.synthesize(synth_req)

            # Stale task / barge-in check before playback
            if self._interrupted or self._current_turn_id != turn_id:
                return VoiceTurnResult(
                    transcript=transcript,
                    response_text=raw_response,
                    formatted_text=formatted_text,
                    interrupted=True,
                    state=self._state,
                )

            # Playback through EngineClient
            await self._engine.play_audio_buffer(
                samples=synth_res.samples,
                sample_rate=synth_res.sample_rate,
                channels=synth_res.channels,
            )

            # Stale task / barge-in check after playback
            if self._interrupted or self._current_turn_id != turn_id:
                return VoiceTurnResult(
                    transcript=transcript,
                    response_text=raw_response,
                    formatted_text=formatted_text,
                    interrupted=True,
                    state=self._state,
                )

            # Turn completed successfully
            self.transition_to(VoicePipelineState.IDLE)
            return VoiceTurnResult(
                transcript=transcript,
                response_text=raw_response,
                formatted_text=formatted_text,
                state=VoicePipelineState.IDLE,
            )

        except asyncio.CancelledError:
            self._interrupted = True
            self._current_turn_id += 1
            try:
                await self._engine.stop_audio_playback()
            except Exception:
                pass
            try:
                await self._engine.stop_audio_capture()
            except Exception:
                pass

            if self._state == VoicePipelineState.SPEAKING:
                self.transition_to(VoicePipelineState.INTERRUPTED)
            elif (
                self._state != VoicePipelineState.INTERRUPTED
                and VoicePipelineState.IDLE in VALID_PIPELINE_TRANSITIONS.get(self._state, set())
            ):
                self.transition_to(VoicePipelineState.IDLE)
            raise

        except Exception as exc:
            self._current_turn_id += 1
            try:
                await self._engine.stop_audio_playback()
            except Exception:
                pass
            try:
                await self._engine.stop_audio_capture()
            except Exception:
                pass

            if VoicePipelineState.ERROR in VALID_PIPELINE_TRANSITIONS.get(self._state, set()):
                self.transition_to(VoicePipelineState.ERROR, metadata={"error": str(exc)})

            if raise_on_error:
                raise

            return VoiceTurnResult(
                error=str(exc),
                state=self._state,
            )

        finally:
            if self._active_turn_task == asyncio.current_task():
                self._active_turn_task = None


__all__ = [
    "InvalidStateTransitionError",
    "VALID_PIPELINE_TRANSITIONS",
    "VoicePipelineBusyError",
    "VoicePipelineError",
    "VoicePipelineManager",
]
