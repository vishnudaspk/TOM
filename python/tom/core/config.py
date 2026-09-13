"""Configuration loader and manager for TOM.

Loads, merges, and validates configuration files from config/ directory
into strongly typed Pydantic models.
"""

import os
from pathlib import Path
from typing import Any

import yaml

from tom.schemas.config import TOMConfig
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="core.config")

_global_config: TOMConfig | None = None


def find_project_root() -> Path:
    """Locate the project root directory by searching for markers."""
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() or (parent / "plan.md").exists():
            return parent
    return Path.cwd()


def load_yaml_file(file_path: Path) -> dict[str, Any]:
    """Load a single YAML file safely, returning empty dict if missing."""
    if not file_path.exists():
        logger.debug("config_file_not_found", path=str(file_path))
        return {}
    try:
        with open(file_path, encoding="utf-8") as f:
            content = yaml.safe_load(f)
            return content if isinstance(content, dict) else {}
    except Exception as e:
        logger.error("config_yaml_parse_error", path=str(file_path), error=str(e))
        raise ValueError(f"Failed to parse YAML file at {file_path}: {e}") from e


def load_config(config_dir: Path | None = None, force_reload: bool = False) -> TOMConfig:
    """Load and assemble the master TOM configuration.

    Merges individual subsystem configs if present:
    - tom.yaml
    - models.yaml
    - tools.yaml
    - permissions.yaml
    - voice.yaml
    - memory.yaml
    - resources.yaml

    Returns:
        Validated TOMConfig instance.
    """
    global _global_config
    if _global_config is not None and not force_reload:
        return _global_config

    root = find_project_root()
    cfg_dir = config_dir or (root / "config")

    assembled_dict: dict[str, Any] = {}

    # Load master tom.yaml
    main_cfg = load_yaml_file(cfg_dir / "tom.yaml")
    assembled_dict.update(main_cfg)

    # Load component YAMLs if they exist and merge them
    component_files = {
        "models": "models.yaml",
        "voice": "voice.yaml",
        "tools": "tools.yaml",
        "permissions": "permissions.yaml",
        "memory": "memory.yaml",
        "resources": "resources.yaml",
    }

    for section_name, filename in component_files.items():
        comp_data = load_yaml_file(cfg_dir / filename)
        if comp_data:
            assembled_dict[section_name] = comp_data

    # Environment variable overrides
    env_override = os.environ.get("TOM_ENV")
    if env_override:
        assembled_dict["environment"] = env_override

    try:
        config = TOMConfig.model_validate(assembled_dict)
        _global_config = config
        logger.info(
            "config_loaded_successfully",
            environment=config.environment,
            models=list(config.models.model_dump().keys()),
        )
        return config
    except Exception as exc:
        logger.error("config_validation_failed", error=str(exc))
        raise


def get_config() -> TOMConfig:
    """Retrieve the cached configuration or load it if not yet initialized."""
    global _global_config
    if _global_config is None:
        return load_config()
    return _global_config
