"""Two-Tier Intent & Model Router (Iteration 4).

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 4)
- Decision 034: Two-Tier Intent and Model Routing Architecture

Tier 1: Fast deterministic heuristic classification (< 1ms) for obvious
        requests using keyword and pattern matching.
Tier 2: Model-backed classification via LLMProvider for ambiguous or
        complex requests (< 350ms target).
"""

from __future__ import annotations

import asyncio
import re
import time

from tom.models.base import LLMProvider, ModelProviderError, ModelResponseError
from tom.schemas.agent import Message, Role
from tom.schemas.models import ModelRequest
from tom.schemas.router import IntentDomain, RoutingDecision
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tier 1 — Heuristic Classification
# ---------------------------------------------------------------------------

_TOOL_NAME_RE = re.compile(r"^system\.\w+$")
_FILES_TOOL_RE = re.compile(r"^files\.\w+$")

_KEYWORD_MAP: list[tuple[list[str], IntentDomain]] = [
    (
        [
            "cpu",
            "ram",
            "gpu",
            "battery",
            "disk",
            "process",
            "temperature",
            "utilization",
            "snapshot",
            "system",
        ],
        IntentDomain.SYSTEM,
    ),
    (
        [
            "file",
            "files",
            "read",
            "write",
            "delete",
            "list",
            "directory",
            "folder",
            "path",
            "search",
        ],
        IntentDomain.FILES,
    ),
    (
        [
            "remember",
            "recall",
            "past",
            "previously",
            "history",
            "what did i",
            "what was",
            "forget",
            "memory",
        ],
        IntentDomain.MEMORY,
    ),
    (
        ["image", "screenshot", "picture", "photo", "vision", "camera", "visual", "analyze image"],
        IntentDomain.VISION,
    ),
    (
        ["hello", "hey", "joke", "story", "chat", "greeting"],
        IntentDomain.CHAT,
    ),
    (
        ["explain", "why", "how", "analyze", "compare", "reason", "think", "evaluate", "what is"],
        IntentDomain.REASONING,
    ),
]


def _match_tier1(user_input: str) -> tuple[IntentDomain | None, str | None]:
    """Attempt fast Tier 1 classification.

    Returns (domain, target_tool) or (None, None) if the request
    is ambiguous and Tier 2 should be tried.
    """
    text = user_input.lower().strip()
    if not text:
        return None, None

    if _TOOL_NAME_RE.match(user_input.strip()):
        return IntentDomain.SYSTEM, user_input.strip()

    if _FILES_TOOL_RE.match(user_input.strip()):
        return IntentDomain.FILES, user_input.strip()

    best_domain: IntentDomain | None = None
    best_score = 0

    for keywords, domain in _KEYWORD_MAP:
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_domain = domain

    if best_domain is not None and best_score >= 1:
        return best_domain, None

    return None, None


# ---------------------------------------------------------------------------
# Tier 2 — Model-Backed Classification
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = (
    "Classify the following user request into exactly one of these domains: "
    "SYSTEM, FILES, REASONING, MEMORY, VISION, CHAT, UNKNOWN. "
    "Respond with ONLY the domain name, nothing else.\n\n"
    "User request: {input}"
)

_VALID_DOMAINS = {d.value for d in IntentDomain}


def _parse_model_response(text: str) -> IntentDomain | None:
    """Parse a model classification response into an IntentDomain.

    Returns None if the response cannot be mapped to a valid domain.
    """
    cleaned = text.strip().upper()
    for domain_value in _VALID_DOMAINS:
        if cleaned.startswith(domain_value):
            return IntentDomain(domain_value)
    first_word = cleaned.split()[0] if cleaned.split() else ""
    if first_word in _VALID_DOMAINS:
        return IntentDomain(first_word)
    return None


# ---------------------------------------------------------------------------
# IntentRouter
# ---------------------------------------------------------------------------


class IntentRouter:
    """Two-tier intent classifier for TOM user requests.

    Tier 1 (fast heuristic): keyword/pattern matching for obvious requests,
    completing in sub-millisecond time without any model invocation.

    Tier 2 (model-backed): classifies ambiguous or complex prompts via an
    optional LLMProvider. When no provider is configured, ambiguous requests
    fall back to a safe default.
    """

    def __init__(
        self,
        model_provider: LLMProvider | None = None,
        timeout_seconds: float = 3.5,
    ) -> None:
        self._provider = model_provider
        self._timeout = timeout_seconds

    @property
    def model_provider(self) -> LLMProvider | None:
        """Return the configured model provider, or None."""
        return self._provider

    async def classify(self, user_input: str) -> RoutingDecision:
        """Classify a user request into an intent domain.

        Returns a RoutingDecision with domain, confidence, target_tool,
        route_tier, and latency_ms.

        Safe handling:
            - Model failure, timeout, or malformed output falls back to
              REASONING with low confidence and route_tier=2.
            - Empty or unrecognizable input defaults to UNKNOWN.
        """
        t_start = time.perf_counter()

        domain, target_tool = _match_tier1(user_input)
        if domain is not None:
            latency_ms = (time.perf_counter() - t_start) * 1000.0
            logger.debug(
                "router_tier1_classified",
                domain=domain.value,
                latency_ms=latency_ms,
            )
            return RoutingDecision(
                domain=domain,
                confidence=0.95,
                target_tool=target_tool,
                route_tier=1,
                latency_ms=latency_ms,
            )

        if self._provider is None:
            latency_ms = (time.perf_counter() - t_start) * 1000.0
            return RoutingDecision(
                domain=IntentDomain.UNKNOWN,
                confidence=0.3,
                target_tool=None,
                route_tier=1,
                latency_ms=latency_ms,
            )

        try:
            domain = await self._classify_with_model(user_input)
        except (ModelProviderError, TimeoutError) as exc:
            logger.warning("router_tier2_failed", error=str(exc))
            domain = IntentDomain.REASONING
        except Exception as exc:
            logger.warning("router_tier2_unexpected", error=str(exc))
            domain = IntentDomain.REASONING

        latency_ms = (time.perf_counter() - t_start) * 1000.0
        return RoutingDecision(
            domain=domain,
            confidence=0.5,
            target_tool=None,
            route_tier=2,
            latency_ms=latency_ms,
        )

    async def _classify_with_model(self, user_input: str) -> IntentDomain:
        """Send a classification request to the model provider."""
        prompt = _CLASSIFY_PROMPT.format(input=user_input)

        req = ModelRequest(
            model="router",
            messages=[Message(role=Role.USER, content=prompt)],
            max_tokens=64,
            temperature=0.1,
        )

        response = await asyncio.wait_for(
            self._provider.generate(req),
            timeout=self._timeout,
        )

        parsed = _parse_model_response(response.content)
        if parsed is not None:
            return parsed
        raise ModelResponseError(f"Model returned unrecognized domain: {response.content[:100]}")
