"""Pydantic schemas for TOM Intent & Model Router (Iteration 4).

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 4)
- Decision 034: Two-Tier Intent and Model Routing Architecture
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IntentDomain(StrEnum):
    """Typed intent domains for user request classification."""

    SYSTEM = "SYSTEM"
    FILES = "FILES"
    REASONING = "REASONING"
    MEMORY = "MEMORY"
    VISION = "VISION"
    CHAT = "CHAT"
    UNKNOWN = "UNKNOWN"


class RoutingDecision(BaseModel):
    """Typed result of intent classification.

    Contains the classified domain, confidence score, optional direct
    tool target, the route tier that was used (1 = fast heuristic,
    2 = model-backed), and the measured latency in milliseconds.
    """

    model_config = ConfigDict(extra="ignore")

    domain: IntentDomain = Field(..., description="Classified intent domain")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Classification confidence score between 0.0 and 1.0",
    )
    target_tool: str | None = Field(
        default=None,
        description="Direct tool name when Tier 1 matches a deterministic tool",
    )
    route_tier: int = Field(
        ...,
        ge=1,
        le=2,
        description="Routing tier used: 1 (fast heuristic) or 2 (model-backed)",
    )
    latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Time spent performing classification in milliseconds",
    )
