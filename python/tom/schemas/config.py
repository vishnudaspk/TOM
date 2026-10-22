"""Pydantic configuration models for TOM.

Adheres to plan.md §45 and skills/coding/validation:
- Strict typing and bounds checking
- Validation fail-fast at startup
- Complete schema covering all subsystems
"""

from typing import Literal

from pydantic import BaseModel, Field


class ModelSpec(BaseModel):
    """Specification for a single AI model."""

    provider: Literal["local", "cloud"] = Field(
        default="local",
        description="Execution target: local hardware or cloud API",
    )
    model_name: str = Field(
        ...,
        description="Name, HuggingFace repo ID, or local file identifier",
    )
    quantization: str | None = Field(
        default="Q4_K_M",
        description="Quantization type (e.g. Q4_K_M, Q5_K_M, FP16)",
    )
    vram_required_mb: int = Field(
        default=0,
        ge=0,
        le=8192,
        description="Expected VRAM requirement in MB (capped by 8GB RTX 4060 limit)",
    )
    context_length: int = Field(
        default=8192,
        gt=0,
        le=32768,
        description="Maximum context window in tokens",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    beam_size: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Beam size for beam search decoding (STT models)",
    )


class ModelsConfig(BaseModel):
    """Model registry configuration."""

    router: ModelSpec = Field(
        default_factory=lambda: ModelSpec(
            model_name="Qwen/Qwen2.5-1.5B-Instruct-GGUF",
            quantization="Q4_K_M",
            vram_required_mb=1200,
            context_length=4096,
            temperature=0.1,
        ),
        description="Lightweight routing model",
    )
    reasoning: ModelSpec = Field(
        default_factory=lambda: ModelSpec(
            model_name="Qwen/Qwen2.5-7B-Instruct-GGUF",
            quantization="Q4_K_M",
            vram_required_mb=5200,
            context_length=8192,
            temperature=0.7,
        ),
        description="Primary reasoning and tool-calling model",
    )
    stt: ModelSpec = Field(
        default_factory=lambda: ModelSpec(
            model_name="C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en",
            quantization="int8",
            vram_required_mb=0,  # Default to CPU placement initially per plan.md
            context_length=448,
        ),
        description="Speech-to-text model",
    )
    tts: ModelSpec = Field(
        default_factory=lambda: ModelSpec(
            model_name="hexgrad/Kokoro-82M",
            quantization="fp32",
            vram_required_mb=0,
            context_length=512,
        ),
        description="Text-to-speech voice generation model",
    )
    vision: ModelSpec = Field(
        default_factory=lambda: ModelSpec(
            model_name="Qwen/Qwen2.5-VL-7B-Instruct-GGUF",
            quantization="Q4_K_M",
            vram_required_mb=5500,
            context_length=4096,
        ),
        description="Vision-Language model",
    )


class VoiceConfig(BaseModel):
    """Voice pipeline configuration."""

    enabled: bool = Field(default=True, description="Enable voice interaction")
    wakeword_phrase: str = Field(default="hey tom", description="Trigger phrase")
    wakeword_sensitivity: float = Field(default=0.6, ge=0.0, le=1.0)
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    silence_duration_ms: int = Field(default=600, ge=200, le=3000)
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    playback_device: str | None = Field(default=None, description="Audio output device")
    capture_device: str | None = Field(default=None, description="Microphone input device")
    energy_threshold: float = Field(
        default=0.003,
        ge=0.0001,
        le=1.0,
        description="Microphone energy VAD threshold",
    )


class ToolsConfig(BaseModel):
    """Deterministic tool execution configuration."""

    default_timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    max_search_results: int = Field(default=10, gt=0, le=100)
    web_search_endpoint: str = Field(
        default="http://127.0.0.1:8080/search",
        min_length=1,
        description="Local or self-hosted SearXNG-compatible search endpoint",
    )
    web_search_timeout_seconds: float = Field(default=10.0, gt=0.0, le=30.0)
    web_search_max_response_bytes: int = Field(
        default=1_000_000,
        gt=0,
        le=1_048_576,
        description="Maximum provider response body size",
    )
    web_fetch_timeout_seconds: float = Field(default=10.0, gt=0.0, le=30.0)
    web_fetch_max_response_bytes: int = Field(
        default=1_048_576,
        gt=0,
        le=10_485_760,
        description="Maximum fetch response body size (default 1 MiB, max 10 MiB)",
    )
    web_fetch_max_redirects: int = Field(
        default=10,
        ge=0,
        le=20,
        description="Maximum number of redirects to follow for web.fetch",
    )
    web_fetch_connect_timeout_seconds: float = Field(
        default=3.0,
        gt=0.0,
        le=10.0,
        description="Connection timeout for web.fetch in seconds",
    )
    max_file_read_bytes: int = Field(default=5_000_000, gt=0, le=50_000_000)
    allowed_directories: list[str] = Field(
        default_factory=lambda: ["."],
        description="Whitelisted filesystem directories for read/write tools",
    )


class PermissionsConfig(BaseModel):
    """Security permission configuration."""

    require_confirmation_for_ask: bool = Field(
        default=True,
        description="Force interactive prompt for ASK_USER category",
    )
    confirmation_timeout_seconds: float = Field(default=30.0, gt=0.0, le=180.0)
    blocked_commands: list[str] = Field(
        default_factory=lambda: [
            "rmdir /s",
            "format",
            "diskpart",
            "reg delete",
            "shutdown",
            "drop database",
        ],
        description="Always-blocked command substrings",
    )


class MemoryConfig(BaseModel):
    """Authoritative and semantic memory configuration."""

    sqlite_db_path: str = Field(
        default="data/memory/tom.db",
        description="Authoritative SQLite database file path",
    )
    qdrant_host: str = Field(default="localhost", description="Qdrant host")
    qdrant_port: int = Field(default=6333, gt=0, le=65535)
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Embedding model for vector retrieval",
    )
    enable_semantic_search: bool = Field(default=True)
    memory_retention_days: int = Field(default=90, gt=0)


class ResourcesConfig(BaseModel):
    """Hardware resource limits for RTX 4060 (8GB VRAM) laptop."""

    max_vram_mb: int = Field(
        default=7200,
        ge=1000,
        le=8192,
        description="Upper limit on VRAM allocation, reserving ~1GB for OS",
    )
    max_ram_mb: int = Field(
        default=12000,
        ge=2000,
        le=16384,
        description="Upper limit on host RAM allocation",
    )
    gpu_device_id: int = Field(default=0, ge=0)
    whisper_on_gpu: bool = Field(
        default=False,
        description="False = run Whisper on CPU to prevent VRAM contention with LLM",
    )


class TaskConfig(BaseModel):
    """Phase 8 task execution configuration."""

    default_max_steps: int = Field(
        default=15,
        ge=1,
        le=30,
        description="Default step budget per task",
    )
    hard_max_steps: int = Field(
        default=30,
        ge=1,
        le=50,
        description="Absolute ceiling on step budget",
    )
    default_timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        le=900.0,
        description="Default wall-clock timeout per task in seconds",
    )
    hard_timeout_seconds: float = Field(
        default=900.0,
        gt=0.0,
        le=3600.0,
        description="Absolute ceiling on task wall-clock timeout",
    )
    max_replans: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum number of replanning attempts per task",
    )
    confirmation_timeout_seconds: float = Field(
        default=120.0,
        gt=0.0,
        le=600.0,
        description="Seconds to wait for ASK_USER confirmation before expiry",
    )
    loop_detection_window: int = Field(
        default=3,
        ge=2,
        le=10,
        description="Consecutive identical tool calls before loop detection triggers",
    )


class IpcConfig(BaseModel):
    """Python <-> Rust IPC configuration."""

    pipe_name: str = Field(
        default=r"\\.\pipe\tom-engine",
        description="Windows Named Pipe address",
    )
    connection_timeout_ms: int = Field(default=3000, gt=100)
    request_timeout_ms: int = Field(default=5000, gt=100)
    max_reconnect_attempts: int = Field(default=5, ge=0)


class VisionConfig(BaseModel):
    """Vision and screen capture configuration."""

    enabled: bool = Field(default=True, description="Enable vision capabilities")
    backend: Literal["auto", "mss", "pillow", "mock"] = Field(
        default="auto",
        description="Screen capture backend: auto, mss, pillow, or mock",
    )
    enable_privacy_shield: bool = Field(
        default=True,
        description="Enforce privacy checks before capturing screen",
    )
    sensitive_window_patterns: list[str] = Field(
        default_factory=lambda: [
            "*password*",
            "*bitwarden*",
            "*1password*",
            "*keepass*",
            "*bank*",
            "*private browsing*",
            "*incognito*",
        ],
        description="Glob/substring patterns for sensitive window titles to block",
    )


class TelemetryConfig(BaseModel):
    """Telemetry and logging configuration."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Minimum log level for file output",
    )
    json_output: bool = Field(
        default=True,
        description="Use structured JSON format for logs",
    )
    health_check_timeout_ms: int = Field(
        default=5000,
        description="Per-check timeout for health check mode (ms)",
    )


class TOMConfig(BaseModel):
    """Master configuration root for TOM."""

    version: str = Field(default="0.1.0")
    environment: Literal["development", "production", "test"] = Field(default="development")
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    ipc: IpcConfig = Field(default_factory=IpcConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    task: TaskConfig = Field(default_factory=TaskConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
