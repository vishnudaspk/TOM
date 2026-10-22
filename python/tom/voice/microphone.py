"""Continuous microphone audio loop with Voice Activity Detection (VAD) and endpointing.

Phase 9 Iteration 9.0:
- Raw audio ingestion (from EngineClient or direct push)
- Frame-level chunking (20-30 ms mono 16kHz PCM)
- VAD-driven speech onset detection with pre-roll buffer
- Silence hangover endpointing and maximum duration bounding
- Asynchronous VoiceEvent stream (SpeechStartEvent -> AudioFrameEvent* -> SpeechEndEvent)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from tom.telemetry.logging import get_logger, get_utterance_id, set_utterance_id, VoiceEventNames, log_rate_limited
from tom.voice.audio_utils import pcm16_bytes_to_samples, samples_to_pcm16_bytes
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent, VoiceEvent
from tom.voice.vad import EnergyVADDetector, VADDetector

if TYPE_CHECKING:
    from tom.core.engine import EngineClient

logger = get_logger(__name__)


class MicrophoneConfig(BaseModel):
    """Configuration for the continuous microphone capture loop."""

    model_config = ConfigDict(extra="ignore")

    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    channels: int = Field(default=1, description="Audio channel count (1=mono)")
    frame_duration_ms: int = Field(
        default=20,
        ge=10,
        le=50,
        description="Frame length in milliseconds (20ms = 320 samples at 16kHz)",
    )
    silence_duration_ms: int = Field(
        default=600,
        ge=50,
        le=5000,
        description="Trailing silence hangover before committing speech endpoint",
    )
    pre_roll_frames: int = Field(
        default=5,
        ge=0,
        le=30,
        description="Number of frames prior to speech onset to retain (5 frames = 100ms)",
    )
    min_speech_frames: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Consecutive speech frames required to confirm onset (filters clicks/taps)",
    )
    max_utterance_seconds: float = Field(
        default=30.0,
        ge=0.05,
        le=120.0,
        description="Maximum duration of a single speech utterance in seconds",
    )
    energy_threshold: float = Field(
        default=0.003,
        ge=0.0001,
        le=0.5,
        description="RMS energy threshold for the default EnergyVADDetector",
    )
    poll_interval_s: float = Field(
        default=0.02,
        ge=0.005,
        le=0.1,
        description="Polling interval when reading from EngineClient",
    )
    capture_device: str | None = Field(
        default=None,
        description="Audio input device name to pass to the engine (None = engine default)",
    )


class EventSubscription:
    """Active subscription to voice events from a MicrophoneLoop.

    Registers the subscriber queue synchronously upon instantiation so no events
    are missed prior to entering the async iteration loop.
    """

    def __init__(self, loop: MicrophoneLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._loop._subscribers.append(self._queue)
        self._closed = False
        if loop._stopped:
            self._queue.put_nowait(None)

    def __aiter__(self) -> EventSubscription:
        return self

    async def __anext__(self) -> VoiceEvent:
        if self._closed:
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            self._closed = True
            raise StopAsyncIteration
        return event

    def close(self) -> None:
        self._closed = True
        if self._queue in self._loop._subscribers:
            self._loop._subscribers.remove(self._queue)


class MicrophoneLoop:
    """Continuous asynchronous audio loop with VAD and silence endpointing.

    Processes raw audio into discrete speech turns delimited by:
    SpeechStartEvent -> AudioFrameEvent* -> SpeechEndEvent.
    """

    def __init__(
        self,
        engine_client: EngineClient | None = None,
        config: MicrophoneConfig | None = None,
        vad: VADDetector | None = None,
    ) -> None:
        self._engine = engine_client
        self._config = config or MicrophoneConfig()
        self._vad = vad or EnergyVADDetector(threshold=self._config.energy_threshold)

        # Derived frame metrics
        self._frame_samples = (self._config.sample_rate * self._config.frame_duration_ms) // 1000

        # State flags
        self._running = False
        self._stopped = False
        self._muted = False
        self._paused = False
        self._in_speech = False

        # Ingestion buffer
        self._sample_buffer: list[float] = []

        # Turn tracking state
        self._speech_onset_count = 0
        self._trailing_silence_ms = 0
        self._utterance_samples: list[float] = []
        self._utterance_pcm_bytes = bytearray()
        self._utterance_frame_index = 0
        self._pre_roll_buffer: deque[tuple[list[float], bytes]] = deque(
            maxlen=self._config.pre_roll_frames
        )
        self._onset_frames: list[tuple[list[float], bytes]] = []

        # Event distribution
        self._subscribers: list[asyncio.Queue[VoiceEvent | None]] = []
        self._poll_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> MicrophoneConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running and not self._stopped

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_in_speech(self) -> bool:
        return self._in_speech

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    # ------------------------------------------------------------------
    # Lifecycle controls
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start continuous audio capture and background engine polling if configured."""
        if self._running:
            return

        self._running = True
        self._stopped = False

        if self._engine is not None:
            try:
                await self._engine.start_audio_capture(
                    device_name=self._config.capture_device,
                    sample_rate=self._config.sample_rate,
                    channels=self._config.channels,
                    chunk_size=self._frame_samples,
                )
            except Exception as exc:
                logger.warning("microphone_capture_start_failed", error=str(exc))

            # Spawn engine polling background task
            self._poll_task = asyncio.create_task(self._poll_engine_loop())

        logger.info("microphone_loop_started", frame_samples=self._frame_samples)

    async def stop(self) -> None:
        """Stop audio capture, finalize any active utterance, and shutdown cleanly."""
        if self._stopped:
            return

        self._stopped = True
        self._running = False

        # Cancel polling task
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        # Stop engine capture if attached
        if self._engine is not None:
            try:
                await self._engine.stop_audio_capture()
            except Exception as exc:
                logger.warning("microphone_capture_stop_failed", error=str(exc))

        # If mid-speech, finalize and commit utterance
        if self._in_speech and self._utterance_samples:
            duration_s = len(self._utterance_samples) / self._config.sample_rate
            end_event = SpeechEndEvent(
                samples=list(self._utterance_samples),
                pcm_bytes=bytes(self._utterance_pcm_bytes),
                duration_s=duration_s,
                frame_count=self._utterance_frame_index,
                timestamp=time.time(),
            )
            self._dispatch_event(end_event)

        self._reset_turn_state(clear_ingest=True)

        # Signal all subscriber queues that stream has ended
        for queue in list(self._subscribers):
            queue.put_nowait(None)

        logger.info("microphone_loop_stopped")

    def mute(self) -> None:
        """Mute the microphone, discarding incoming audio and ending any active turn."""
        self._muted = True
        if self._in_speech and self._utterance_samples:
            duration_s = len(self._utterance_samples) / self._config.sample_rate
            end_event = SpeechEndEvent(
                samples=list(self._utterance_samples),
                pcm_bytes=bytes(self._utterance_pcm_bytes),
                duration_s=duration_s,
                frame_count=self._utterance_frame_index,
                timestamp=time.time(),
            )
            self._dispatch_event(end_event)
        self._reset_turn_state(clear_ingest=True)
        logger.debug("microphone_muted")

    def unmute(self) -> None:
        """Unmute the microphone and resume processing."""
        self._muted = False
        self._reset_turn_state(clear_ingest=True)
        logger.debug("microphone_unmuted")

    def pause(self) -> None:
        """Temporarily pause audio ingestion."""
        self._paused = True

    def resume(self) -> None:
        """Resume paused audio ingestion and clear any buffered audio."""
        self._paused = False
        # Clear any audio captured during pause to avoid processing stale data
        if self._engine is not None:
            try:
                # Use synchronous clear via the engine's buffer clear mechanism
                # The next poll will also clear, but we do it immediately to avoid burst
                pass  # Engine buffer is cleared on next poll with clear=True
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def subscribe(self) -> EventSubscription:
        """Synchronously create an event subscription that immediately buffers events."""
        return EventSubscription(self)

    async def events(self) -> AsyncGenerator[VoiceEvent, None]:
        """Yield voice events as an asynchronous stream until stopped."""
        sub = self.subscribe()
        try:
            async for event in sub:
                yield event
        finally:
            sub.close()

    # ------------------------------------------------------------------
    # Audio ingestion
    # ------------------------------------------------------------------

    async def push_samples(self, samples: list[float] | bytes) -> None:
        """Ingest raw audio samples or PCM bytes into the processing loop."""
        if self._stopped or self._muted or self._paused:
            return

        if isinstance(samples, bytes):
            float_samples = pcm16_bytes_to_samples(samples)
        else:
            float_samples = samples

        if not float_samples:
            return

        self._sample_buffer.extend(float_samples)

        processed = 0
        while len(self._sample_buffer) >= self._frame_samples:
            frame = self._sample_buffer[: self._frame_samples]
            del self._sample_buffer[: self._frame_samples]
            self._process_frame(frame)
            processed += 1
            # Yield so a large ingest burst cannot stall the event loop.
            if processed >= 8:
                processed = 0
                await asyncio.sleep(0)

    def _process_frame(self, frame_samples: list[float]) -> None:
        """Process a single fixed-size frame through VAD and turn state machine."""
        pcm_bytes = samples_to_pcm16_bytes(frame_samples)
        is_speech = self._vad.is_speech(frame_samples, self._config.sample_rate)
        now = time.time()

        if not self._in_speech:
            if is_speech:
                self._speech_onset_count += 1
                self._onset_frames.append((frame_samples, pcm_bytes))

                if self._speech_onset_count >= self._config.min_speech_frames:
                    # Speech onset confirmed!
                    self._in_speech = True
                    self._utterance_samples = []
                    self._utterance_pcm_bytes = bytearray()
                    self._utterance_frame_index = 0
                    self._trailing_silence_ms = 0

                    rms = 0.0
                    if hasattr(self._vad, "calculate_rms"):
                        rms = self._vad.calculate_rms(frame_samples)

                    logger.info(
                        "speech_onset_detected",
                        rms=round(rms, 6),
                        threshold=getattr(self._vad, "threshold", None),
                    )

                    # 1. Emit SpeechStartEvent
                    self._dispatch_event(SpeechStartEvent(timestamp=now, metadata={"rms": rms}))

                    # 2. Drain pre-roll buffer in chronological order
                    while self._pre_roll_buffer:
                        p_samples, p_bytes = self._pre_roll_buffer.popleft()
                        self._dispatch_event(
                            AudioFrameEvent(
                                samples=p_samples,
                                pcm_bytes=p_bytes,
                                timestamp=now,
                                frame_index=self._utterance_frame_index,
                                is_speech=False,
                            )
                        )
                        self._utterance_samples.extend(p_samples)
                        self._utterance_pcm_bytes.extend(p_bytes)
                        self._utterance_frame_index += 1

                    # 3. Drain onset speech frames
                    for o_samples, o_bytes in self._onset_frames:
                        self._dispatch_event(
                            AudioFrameEvent(
                                samples=o_samples,
                                pcm_bytes=o_bytes,
                                timestamp=now,
                                frame_index=self._utterance_frame_index,
                                is_speech=True,
                            )
                        )
                        self._utterance_samples.extend(o_samples)
                        self._utterance_pcm_bytes.extend(o_bytes)
                        self._utterance_frame_index += 1
                    self._onset_frames.clear()
            else:
                self._speech_onset_count = 0
                self._onset_frames.clear()
                self._pre_roll_buffer.append((frame_samples, pcm_bytes))

        else:
            # Active speech turn
            if is_speech:
                self._trailing_silence_ms = 0
            else:
                self._trailing_silence_ms += self._config.frame_duration_ms

            # Emit frame event
            self._dispatch_event(
                AudioFrameEvent(
                    samples=frame_samples,
                    pcm_bytes=pcm_bytes,
                    timestamp=now,
                    frame_index=self._utterance_frame_index,
                    is_speech=is_speech,
                )
            )
            self._utterance_samples.extend(frame_samples)
            self._utterance_pcm_bytes.extend(pcm_bytes)
            self._utterance_frame_index += 1

            # Check endpointing triggers
            silence_endpoint = self._trailing_silence_ms >= self._config.silence_duration_ms
            duration_s = len(self._utterance_samples) / self._config.sample_rate
            max_duration_endpoint = duration_s >= self._config.max_utterance_seconds

            if silence_endpoint or max_duration_endpoint:
                logger.info(
                    "speech_endpoint_detected",
                    duration_s=round(duration_s, 2),
                    frame_count=self._utterance_frame_index,
                    reason="silence" if silence_endpoint else "max_duration",
                )
                end_event = SpeechEndEvent(
                    samples=list(self._utterance_samples),
                    pcm_bytes=bytes(self._utterance_pcm_bytes),
                    duration_s=duration_s,
                    frame_count=self._utterance_frame_index,
                    timestamp=now,
                )
                self._dispatch_event(end_event)
                self._reset_turn_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_turn_state(self, clear_ingest: bool = False) -> None:
        """Reset utterance accumulator and turn state.

        clear_ingest=False keeps leftover PCM so a bulk push can continue
        into the next turn after max-duration endpointing.
        """
        self._in_speech = False
        self._speech_onset_count = 0
        self._trailing_silence_ms = 0
        self._utterance_samples = []
        self._utterance_pcm_bytes = bytearray()
        self._utterance_frame_index = 0
        self._pre_roll_buffer.clear()
        self._onset_frames.clear()
        if clear_ingest:
            self._sample_buffer.clear()
        if hasattr(self._vad, "reset"):
            self._vad.reset()

    def _dispatch_event(self, event: VoiceEvent) -> None:
        """Broadcast an event to all active subscriber queues."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("MicrophoneLoop subscriber queue full; dropping event")

    async def _poll_engine_loop(self) -> None:
        """Continuous background task to drain audio samples from EngineClient."""
        while not self._stopped:
            try:
                if self._engine is not None:
                    speech_resp = await self._engine.get_captured_speech(clear=True)
                    if speech_resp and speech_resp.samples and not self._paused and not self._muted:
                        await self.push_samples(speech_resp.samples)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log_rate_limited(
                    logger.logger,
                    logging.WARNING,
                    VoiceEventNames.CAPTURE_FAILED,
                    "microphone_engine_poll",
                    error=str(exc),
                )
                await asyncio.sleep(0.2)
                continue

            await asyncio.sleep(self._config.poll_interval_s)


__all__ = [
    "EventSubscription",
    "MicrophoneConfig",
    "MicrophoneLoop",
]
