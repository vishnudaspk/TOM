"""Unit tests for TOM configuration loading and validation."""

import pytest
from pydantic import ValidationError
from tom.core.config import find_project_root, get_config, load_config
from tom.schemas.config import ModelSpec, ResourcesConfig, TOMConfig, VoiceConfig


def test_load_default_config():
    """Verify that default TOMConfig model instantiates with valid defaults."""
    config = TOMConfig()
    assert config.version == "0.1.0"
    assert config.environment == "development"
    assert config.resources.max_vram_mb <= 8192
    assert config.models.reasoning.model_name is not None
    assert config.voice.wakeword_phrase == "hey tom"


def test_load_yaml_configs():
    """Verify loading from actual config/ directory."""
    root = find_project_root()
    cfg_dir = root / "config"
    config = load_config(config_dir=cfg_dir, force_reload=True)

    assert isinstance(config, TOMConfig)
    assert config.models.router.vram_required_mb == 1200
    assert config.models.reasoning.vram_required_mb == 4531
    assert config.resources.max_vram_mb == 7200
    assert "." in config.tools.allowed_directories


def test_vram_bounds_validation():
    """Verify that VRAM allocation exceeding physical GPU capacity is rejected."""
    # RTX 4060 has 8192 MB; requesting 16000 MB should fail validation
    with pytest.raises(ValidationError):
        ResourcesConfig(max_vram_mb=16000)

    # Negative VRAM should fail
    with pytest.raises(ValidationError):
        ResourcesConfig(max_vram_mb=-100)


def test_model_spec_validation():
    """Verify ModelSpec validates required fields and bounds."""
    spec = ModelSpec(
        model_name="test-model",
        vram_required_mb=4000,
        context_length=8192,
        temperature=0.5,
    )
    assert spec.model_name == "test-model"
    assert spec.vram_required_mb == 4000

    # Temperature > 2.0 should fail
    with pytest.raises(ValidationError):
        ModelSpec(model_name="test", temperature=3.5)


def test_get_config_caching():
    """Verify get_config returns singleton instance."""
    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


def test_voice_config_new_fields():
    """Verify VoiceConfig includes all new STEP 1 fields with correct defaults."""
    voice = VoiceConfig()
    assert voice.playback_tail_ms == 400
    assert voice.barge_in_min_rms_multiplier == 4.0
    assert voice.barge_in_min_duration_ms == 300
    assert voice.min_speech_ms == 400
    assert voice.smart_turn_max_incomplete_wait_ms == 1800

    # Verify backward compatibility: omitting fields uses defaults
    yaml_dict = {
        "enabled": True,
        "silence_duration_ms": 600,
    }
    voice2 = VoiceConfig(**yaml_dict)
    assert voice2.playback_tail_ms == 400
    assert voice2.barge_in_min_rms_multiplier == 4.0
    assert voice2.barge_in_min_duration_ms == 300
    assert voice2.min_speech_ms == 400
    assert voice2.smart_turn_max_incomplete_wait_ms == 1800


def test_voice_config_yaml_loads_new_fields():
    """Verify loading from actual config/voice.yaml includes new fields."""
    root = find_project_root()
    voice_path = root / "config" / "voice.yaml"
    assert voice_path.exists()

    import yaml
    with open(voice_path) as f:
        data = yaml.safe_load(f)

    voice = VoiceConfig(**data)
    assert voice.playback_tail_ms == 400
    assert voice.barge_in_min_rms_multiplier == 4.0
    assert voice.barge_in_min_duration_ms == 300
    assert voice.min_speech_ms == 400
    assert voice.smart_turn_max_incomplete_wait_ms == 1800
