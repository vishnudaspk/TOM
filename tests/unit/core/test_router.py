"""Unit tests for TOM Two-Tier Intent & Model Router (Iteration 4).

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 4)
- Decision 034: Two-Tier Intent and Model Routing Architecture
- skills/testing/python-testing: Deterministic, no network, strict assertions

Coverage:
    - Tier 1 heuristic classification (system, files, reasoning, memory,
      vision, chat, ambiguous, empty, direct tool names)
    - Tier 2 model-backed classification via MockModelProvider
    - Ambiguous/unsupported input handling
    - Provider failure/timeout safe fallback
    - Malformed model output handling
    - RoutingDecision schema validation (confidence bounds, tier bounds)
    - Latency measurement present and non-negative
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from tom.core.router import IntentRouter, _match_tier1
from tom.models.base import ModelConnectionError, ModelProviderError
from tom.models.providers.mock import MockModelProvider
from tom.schemas.router import IntentDomain, RoutingDecision


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tier 1 — Heuristic Classification
# ---------------------------------------------------------------------------


class TestTier1DirectMatch:
    def test_system_keywords(self) -> None:
        cases = [
            "what is my CPU temperature",
            "disk space",
            "GPU info",
            "battery level",
            "list processes",
            "system snapshot",
            "how is the system",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.SYSTEM, f"Failed for: {text}"
            assert tool is None

    def test_files_keywords(self) -> None:
        cases = [
            "read my file",
            "list directory",
            "search for files",
            "write file",
            "delete file",
            "file info",
            "check folder",
            "what is the path",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.FILES, f"Failed for: {text}"
            assert tool is None

    def test_reasoning_keywords(self) -> None:
        cases = [
            "explain why",
            "how does this work",
            "analyze the data",
            "compare options",
            "reason about the problem",
            "think through this",
            "what is the meaning",
            "evaluate my options",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.REASONING, f"Failed for: {text}"
            assert tool is None

    def test_memory_keywords(self) -> None:
        cases = [
            "remember this",
            "recall what I said",
            "what is my memory",
            "what did I tell you",
            "what was that",
            "forget something",
            "my past preferences",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.MEMORY, f"Failed for: {text}"
            assert tool is None

    def test_vision_keywords(self) -> None:
        cases = [
            "analyze this image",
            "look at screenshot",
            "describe the picture",
            "check the photo",
            "vision analysis",
            "camera input",
            "visual inspection",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.VISION, f"Failed for: {text}"
            assert tool is None

    def test_chat_keywords(self) -> None:
        cases = [
            "hello",
            "hey tom",
            "tell me a joke",
            "chat with me",
            "greeting",
        ]
        for text in cases:
            domain, tool = _match_tier1(text)
            assert domain == IntentDomain.CHAT, f"Failed for: {text}"
            assert tool is None

    def test_direct_tool_name_system(self) -> None:
        domain, tool = _match_tier1("system.cpu_info")
        assert domain == IntentDomain.SYSTEM
        assert tool == "system.cpu_info"

    def test_direct_tool_name_files(self) -> None:
        domain, tool = _match_tier1("files.read_file")
        assert domain == IntentDomain.FILES
        assert tool == "files.read_file"

    def test_ambiguous_returns_none(self) -> None:
        domain, tool = _match_tier1("banana")
        assert domain is None
        assert tool is None

    def test_empty_string_returns_none(self) -> None:
        domain, tool = _match_tier1("")
        assert domain is None
        assert tool is None

    def test_whitespace_only_returns_none(self) -> None:
        domain, tool = _match_tier1("   ")
        assert domain is None
        assert tool is None


# ---------------------------------------------------------------------------
# Tier 1 → RoutingDecision via classify()
# ---------------------------------------------------------------------------


class TestTier1ThroughRouter:
    def test_classify_system_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("what is my CPU temperature"))

        assert result.domain == IntentDomain.SYSTEM
        assert result.confidence == 0.95
        assert result.target_tool is None
        assert result.route_tier == 1
        assert result.latency_ms >= 0.0

    def test_classify_files_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("list my files"))

        assert result.domain == IntentDomain.FILES
        assert result.route_tier == 1

    def test_classify_chat_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("hello there"))

        assert result.domain == IntentDomain.CHAT
        assert result.route_tier == 1

    def test_classify_vision_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("analyze this screenshot"))

        assert result.domain == IntentDomain.VISION
        assert result.route_tier == 1

    def test_classify_memory_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("what did I tell you"))

        assert result.domain == IntentDomain.MEMORY
        assert result.route_tier == 1

    def test_classify_reasoning_tier1(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("explain why"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 1

    def test_tier1_latency_under_1ms(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("cpu info"))

        assert result.latency_ms < 1.0, f"Tier 1 took {result.latency_ms}ms, expected < 1ms"

    def test_tier1_no_model_invocation(self) -> None:
        """Tier 1 must never call the model provider."""
        mock = MockModelProvider()
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("what is my cpu"))

        assert result.route_tier == 1
        assert mock.call_count == 0

    def test_tier1_direct_tool_returns_tool_name(self) -> None:
        router = IntentRouter()
        result = run_async(router.classify("system.get_snapshot"))

        assert result.domain == IntentDomain.SYSTEM
        assert result.target_tool == "system.get_snapshot"
        assert result.route_tier == 1

    def test_tier1_ambiguous_falls_to_no_provider(self) -> None:
        """Ambiguous input with no provider → UNKNOWN at Tier 1."""
        router = IntentRouter(model_provider=None)
        result = run_async(router.classify("banana apple"))

        assert result.domain == IntentDomain.UNKNOWN
        assert result.route_tier == 1
        assert result.confidence == 0.3
        assert result.target_tool is None


# ---------------------------------------------------------------------------
# Tier 2 — Model-Backed Classification
# ---------------------------------------------------------------------------


class TestTier2ModelBacked:
    def test_tier2_classifies_system(self) -> None:
        mock = MockModelProvider(responses=["SYSTEM"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("I need help with a task"))

        assert result.domain == IntentDomain.SYSTEM
        assert result.route_tier == 2
        assert result.confidence == 0.5
        assert result.target_tool is None

    def test_tier2_classifies_files(self) -> None:
        mock = MockModelProvider(responses=["FILES"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("can you help me organize"))

        assert result.domain == IntentDomain.FILES
        assert result.route_tier == 2

    def test_tier2_classifies_reasoning(self) -> None:
        mock = MockModelProvider(responses=["REASONING"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("I want to understand something"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2

    def test_tier2_classifies_chat(self) -> None:
        mock = MockModelProvider(responses=["CHAT"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("let's have a conversation"))

        assert result.domain == IntentDomain.CHAT
        assert result.route_tier == 2

    def test_tier2_classifies_memory(self) -> None:
        mock = MockModelProvider(responses=["MEMORY"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("a simple query for later"))

        assert result.domain == IntentDomain.MEMORY
        assert result.route_tier == 2

    def test_tier2_classifies_vision(self) -> None:
        mock = MockModelProvider(responses=["VISION"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("I want help with a task"))

        assert result.domain == IntentDomain.VISION
        assert result.route_tier == 2

    def test_tier2_classifies_unknown(self) -> None:
        mock = MockModelProvider(responses=["UNKNOWN"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("xyzabc"))

        assert result.domain == IntentDomain.UNKNOWN
        assert result.route_tier == 2

    def test_tier2_uses_provider(self) -> None:
        mock = MockModelProvider(responses=["SYSTEM"])
        router = IntentRouter(model_provider=mock)
        run_async(router.classify("test"))
        assert mock.call_count == 1


# ---------------------------------------------------------------------------
# Ambiguous / Unsupported Input
# ---------------------------------------------------------------------------


class TestAmbiguousInput:
    def test_completely_ambiguous_no_provider(self) -> None:
        router = IntentRouter(model_provider=None)
        result = run_async(router.classify("asdf qwerty banana"))

        assert result.domain == IntentDomain.UNKNOWN
        assert result.route_tier == 1

    def test_tier2_falls_to_reasoning_on_ambiguous_model(self) -> None:
        """Model returns unrecognizable text → REASONING safe fallback."""
        mock = MockModelProvider(responses=["banana purple"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("what should I do"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2

    def test_tier2_falls_to_reasoning_on_low_confidence(self) -> None:
        mock = MockModelProvider(responses=["something weird"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.REASONING


# ---------------------------------------------------------------------------
# Provider Failure / Timeout
# ---------------------------------------------------------------------------


class TestProviderFailure:
    def test_provider_connection_error_falls_back(self) -> None:
        mock = MockModelProvider(responses=[ModelConnectionError("endpoint down")])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("a b c d"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2

    def test_provider_api_error_falls_back(self) -> None:
        mock = MockModelProvider(responses=[ModelProviderError("bad")])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("a b c d"))

        assert result.domain == IntentDomain.REASONING

    def test_provider_timeout_falls_back(self) -> None:
        mock = MockModelProvider(responses=["SYSTEM"], latency_seconds=10.0)
        router = IntentRouter(model_provider=mock, timeout_seconds=0.1)
        result = run_async(router.classify("what should I do"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2

    def test_provider_exception_falls_back(self) -> None:
        mock = MockModelProvider(responses=[RuntimeError("unexpected")])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("a b c d"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2


# ---------------------------------------------------------------------------
# Malformed Model Output
# ---------------------------------------------------------------------------


class TestMalformedModelOutput:
    def test_garbage_text_falls_back(self) -> None:
        mock = MockModelProvider(responses=["asdf1234 !@#$%"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.REASONING
        assert result.route_tier == 2

    def test_empty_response_falls_back(self) -> None:
        mock = MockModelProvider(responses=[""])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.REASONING

    def test_lowercase_domain_parsed(self) -> None:
        mock = MockModelProvider(responses=["system"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.SYSTEM

    def test_extra_text_after_domain_parsed(self) -> None:
        mock = MockModelProvider(responses=["FILES - here you go"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.FILES

    def test_mixed_case_domain_parsed(self) -> None:
        mock = MockModelProvider(responses=["Vision"])
        router = IntentRouter(model_provider=mock)
        result = run_async(router.classify("test"))

        assert result.domain == IntentDomain.VISION


# ---------------------------------------------------------------------------
# RoutingDecision Schema Validation
# ---------------------------------------------------------------------------


class TestRoutingDecisionSchema:
    def test_valid_decision(self) -> None:
        decision = RoutingDecision(
            domain=IntentDomain.SYSTEM,
            confidence=0.9,
            target_tool=None,
            route_tier=1,
            latency_ms=0.5,
        )
        assert decision.domain == IntentDomain.SYSTEM
        assert decision.confidence == 0.9

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=1.5,
                route_tier=1,
                latency_ms=0.0,
            )

    def test_confidence_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=-0.1,
                route_tier=1,
                latency_ms=0.0,
            )

    def test_route_tier_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=0.5,
                route_tier=3,
                latency_ms=0.0,
            )

    def test_route_tier_min(self) -> None:
        with pytest.raises(ValidationError):
            RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=0.5,
                route_tier=0,
                latency_ms=0.0,
            )

    def test_latency_non_negative(self) -> None:
        decision = RoutingDecision(
            domain=IntentDomain.SYSTEM,
            confidence=0.5,
            route_tier=1,
            latency_ms=0.0,
        )
        assert decision.latency_ms == 0.0

    def test_optional_target_tool(self) -> None:
        decision = RoutingDecision(
            domain=IntentDomain.SYSTEM,
            confidence=0.5,
            target_tool="system.cpu_info",
            route_tier=1,
            latency_ms=0.5,
        )
        assert decision.target_tool == "system.cpu_info"

        decision2 = RoutingDecision(
            domain=IntentDomain.SYSTEM,
            confidence=0.5,
            target_tool=None,
            route_tier=1,
            latency_ms=0.5,
        )
        assert decision2.target_tool is None

    def test_extra_fields_ignored(self) -> None:
        decision = RoutingDecision.model_validate(
            {
                "domain": "SYSTEM",
                "confidence": 0.8,
                "target_tool": None,
                "route_tier": 2,
                "latency_ms": 1.0,
                "extra_field": "should be ignored",
            }
        )
        assert decision.domain == IntentDomain.SYSTEM
