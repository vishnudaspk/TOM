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
            model_name="Systran/faster-whisper-large-v3-turbo",
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


class ToolsConfig(BaseModel):
    """Deterministic tool execution configuration."""

    default_timeout_seconds: float = Field(default=15.0, gt=0.0, le=300.0)
    max_search_results: int = Field(default=10, gt=0, le=100)
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


class IpcConfig(BaseModel):
    """Python <-> Rust IPC configuration."""

    pipe_name: str = Field(
        default=r"\\.\pipe\tom-engine",
        description="Windows Named Pipe address",
    )
    connection_timeout_ms: int = Field(default=3000, gt=100)
    request_timeout_ms: int = Field(default=5000, gt=100)
    max_reconnect_attempts: int = Field(default=5, ge=0)


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
