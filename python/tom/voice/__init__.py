"""TOM voice subsystem."""

from tom.schemas.turn import (
    BackgroundTaskSnapshot,
    EntityKind,
    EntityReferent,
    ResponseAction,
    ResponseDecision,
    TurnContext,
    TurnRecord,
    TurnStatus,
)
from tom.schemas.voice import VoicePipelineState, VoiceTurnResult
from tom.voice.audio_utils import pcm16_bytes_to_samples, samples_to_pcm16_bytes
from tom.voice.events import AudioFrameEvent, SpeechEndEvent, SpeechStartEvent, VoiceEvent
from tom.voice.formatter import SpeechFormatter
from tom.voice.interaction import VoiceInteractionManager
from tom.voice.interaction_core import (
    InteractionCore,
    PlaybackStopGuard,
    TurnEpochGuard,
    TurnExecutionResult,
    TurnExecutor,
    TurnProgress,
)
from tom.voice.latency import (
    LATENCY_SLAS,
    InteractionLatencyTracer,
    LatencyMetric,
    LatencyReport,
    LatencyStage,
)
from tom.voice.microphone import MicrophoneConfig, MicrophoneLoop
from tom.voice.pipeline import (
    InvalidStateTransitionError,
    VoicePipelineBusyError,
    VoicePipelineError,
    VoicePipelineManager,
)
from tom.voice.response_policy import ResponsePolicy
from tom.voice.sentence_streamer import SentenceStreamer
from tom.voice.smart_turn import (
    SmartTurnV3Arbiter,
    TurnArbitrationDecision,
    TurnArbiter,
)
from tom.voice.streaming_stt import (
    FasterWhisperStreamingAdapter,
    PartialTranscript,
    StreamingSTTProvider,
)
from tom.voice.streaming_tts import (
    IncrementalTTSStreamer,
    StreamingTTSProvider,
    SynthesisChunk,
)
from tom.voice.stt import (
    FasterWhisperSTTProvider,
    MockSTTProvider,
    STTConfig,
    STTError,
    STTProvider,
    STTTranscriptionError,
    STTUnavailableError,
)
from tom.voice.tts import (
    KokoroTTSProvider,
    MockTTSProvider,
    TTSConfig,
    TTSError,
    TTSProvider,
    TTSSynthesisError,
    TTSUnavailableError,
)
from tom.voice.turn_manager import ConversationTurnManager
from tom.voice.vad import EnergyVADDetector, MockVADDetector, VADDetector

__all__ = [
    "LATENCY_SLAS",
    "AudioFrameEvent",
    "BackgroundTaskSnapshot",
    "ConversationTurnManager",
    "EnergyVADDetector",
    "EntityKind",
    "EntityReferent",
    "FasterWhisperSTTProvider",
    "FasterWhisperStreamingAdapter",
    "IncrementalTTSStreamer",
    "InteractionCore",
    "InteractionLatencyTracer",
    "InvalidStateTransitionError",
    "KokoroTTSProvider",
    "LATENCY_SLAS",
    "LatencyMetric",
    "LatencyReport",
    "LatencyStage",
    "MicrophoneConfig",
    "MicrophoneLoop",
    "MockSTTProvider",
    "MockTTSProvider",
    "MockVADDetector",
    "PartialTranscript",
    "PlaybackStopGuard",
    "ResponseAction",
    "ResponseDecision",
    "ResponsePolicy",
    "SmartTurnV3Arbiter",
    "STTConfig",
    "STTError",
    "STTProvider",
    "STTTranscriptionError",
    "STTUnavailableError",
    "StreamingSTTProvider",
    "StreamingTTSProvider",
    "SpeechEndEvent",
    "SentenceStreamer",
    "SpeechFormatter",
    "SpeechStartEvent",
    "SynthesisChunk",
    "TTSConfig",
    "TTSError",
    "TTSProvider",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "TurnContext",
    "TurnEpochGuard",
    "TurnExecutionResult",
    "TurnExecutor",
    "TurnProgress",
    "TurnRecord",
    "TurnStatus",
    "TurnArbitrationDecision",
    "TurnArbiter",
    "VADDetector",
    "VoiceEvent",
    "VoiceInteractionManager",
    "VoicePipelineBusyError",
    "VoicePipelineError",
    "VoicePipelineManager",
    "VoicePipelineState",
    "VoiceTurnResult",
    "pcm16_bytes_to_samples",
    "samples_to_pcm16_bytes",
]
