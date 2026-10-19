"""Shared resource types to avoid circular imports."""

from __future__ import annotations

from enum import StrEnum


class ModelRole(StrEnum):
    """Model execution roles with varying resource requirements."""

    ROUTER = "router"
    REASONING = "reasoning"
    VLM = "vlm"
    GENERAL = "general"
