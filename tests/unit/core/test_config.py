"""Unit tests for TOM configuration loading and validation."""

import pytest
from pydantic import ValidationError
from tom.core.config import find_project_root, get_config, load_config
from tom.schemas.config import ModelSpec, ResourcesConfig, TOMConfig


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
    assert config.models.reasoning.vram_required_mb == 5200
    assert config.resources.max_vram_mb == 7200
    assert config.resources.whisper_on_gpu is False
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
