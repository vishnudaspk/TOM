"""Unit tests for TOM Model Provider layer (Iteration 3).

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- skills/testing/python-testing: Deterministic, no network, strict assertions

Coverage:
    - LLMProvider abstract interface enforcement
    - ModelRequest / ModelResponse / StreamChunk / TokenUsage schemas
    - MockModelProvider scripted responses, error injection, streaming
    - HttpModelProvider request building and response parsing (mocked via httpx)
    - Agent/AgentDependencies integration with injected ModelProvider
    - Existing agent tests remain unaffected (no network required)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError
from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.models.base import (
    LLMProvider,
    ModelAPIError,
    ModelConnectionError,
    ModelResponseError,
    ModelTimeoutError,
)
from tom.models.providers.http import (
    HttpModelProvider,
    LMStudioProvider,
    _build_request_payload,
    _message_to_wire,
    _parse_finish_reason,
    _parse_usage,
)
from tom.models.providers.mock import MockModelProvider
from tom.schemas.agent import Message, Role
from tom.schemas.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolCallRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_request(model: str = "test-model", content: str = "Hello") -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[Message(role=Role.USER, content=content)],
    )


def _make_oai_response(
    content: str = "Hi there!",
    reasoning: str = "",
    finish: str = "stop",
    prompt_tokens: int = 5,
    completion_tokens: int = 3,
) -> dict[str, Any]:
    """Build a minimal OpenAI-compatible completion response dict."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning,
                    "tool_calls": [],
                },
                "logprobs": None,
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
        "stats": {},
        "system_fingerprint": "test-model",
    }


def _sse_line(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _make_sse_chunk(
    content: str = "",
    reasoning: str = "",
    finish: str | None = None,
) -> dict[str, Any]:
    """Build a minimal SSE streaming chunk dict."""
    chunk: dict[str, Any] = {
        "id": "chatcmpl-stream-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": content,
                    "reasoning_content": reasoning,
                },
                "finish_reason": finish,
            }
        ],
    }
    return chunk


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------


class TestModelRequestSchema:
    def test_valid_construction(self) -> None:
        req = ModelRequest(
            model="qwen3-8b",
            messages=[Message(role=Role.USER, content="test")],
        )
        assert req.model == "qwen3-8b"
        assert len(req.messages) == 1
        assert req.max_tokens == 1024
        assert req.temperature == 0.7
        assert req.stream is False

    def test_requires_at_least_one_message(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(model="m", messages=[])

    def test_temperature_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(
                model="m",
                messages=[Message(role=Role.USER, content="x")],
                temperature=3.0,  # > 2.0
            )

    def test_max_tokens_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(
                model="m",
                messages=[Message(role=Role.USER, content="x")],
                max_tokens=0,  # < 1
            )

    def test_extra_params_passthrough(self) -> None:
        req = ModelRequest(
            model="m",
            messages=[Message(role=Role.USER, content="x")],
            extra_params={"top_p": 0.9},
        )
        assert req.extra_params["top_p"] == 0.9


class TestModelResponseSchema:
    def test_valid_construction(self) -> None:
        resp = ModelResponse(
            content="Hello!",
            finish_reason=FinishReason.STOP,
        )
        assert resp.content == "Hello!"
        assert resp.finish_reason == FinishReason.STOP
        assert resp.has_tool_calls is False
        assert resp.is_complete is True

    def test_defaults(self) -> None:
        resp = ModelResponse()
        assert resp.content == ""
        assert resp.finish_reason == FinishReason.UNKNOWN
        assert resp.usage.total_tokens == 0

    def test_tool_call_detection(self) -> None:
        resp = ModelResponse(
            tool_calls=[ToolCallRequest(id="tc1", name="system.cpu_info", arguments={})],
            finish_reason=FinishReason.TOOL_CALL,
        )
        assert resp.has_tool_calls is True
        assert resp.is_complete is True

    def test_length_finish_is_not_complete(self) -> None:
        resp = ModelResponse(finish_reason=FinishReason.LENGTH)
        assert resp.is_complete is False


class TestStreamChunkSchema:
    def test_intermediate_chunk(self) -> None:
        chunk = StreamChunk(content="Hello", is_final=False)
        assert chunk.content == "Hello"
        assert chunk.is_final is False
        assert chunk.finish_reason is None

    def test_final_chunk(self) -> None:
        chunk = StreamChunk(
            content="",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            is_final=True,
        )
        assert chunk.is_final is True
        assert chunk.finish_reason == FinishReason.STOP
        assert chunk.usage is not None
        assert chunk.usage.total_tokens == 8


class TestTokenUsageSchema:
    def test_valid(self) -> None:
        u = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert u.total_tokens == 30

    def test_reasoning_tokens(self) -> None:
        u = TokenUsage(reasoning_tokens=8)
        assert u.reasoning_tokens == 8


# ---------------------------------------------------------------------------
# Abstract Interface Enforcement
# ---------------------------------------------------------------------------


class TestLLMProviderAbstract:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_mock_satisfies_interface(self) -> None:
        provider = MockModelProvider()
        assert isinstance(provider, LLMProvider)

    def test_http_satisfies_interface(self) -> None:
        provider = HttpModelProvider()
        assert isinstance(provider, LLMProvider)

    def test_lmstudio_satisfies_interface(self) -> None:
        provider = LMStudioProvider()
        assert isinstance(provider, LLMProvider)


# ---------------------------------------------------------------------------
# MockModelProvider Tests
# ---------------------------------------------------------------------------


class TestMockModelProvider:
    def test_plain_string_response(self) -> None:
        mock = MockModelProvider(responses=["Hello world"])
        req = _make_request()
        result = run_async(mock.generate(req))
        assert result.content == "Hello world"
        assert result.finish_reason == FinishReason.STOP
        assert mock.call_count == 1

    def test_model_response_passthrough(self) -> None:
        expected = ModelResponse(
            content="Scripted response",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        )
        mock = MockModelProvider(responses=[expected])
        result = run_async(mock.generate(_make_request()))
        assert result.content == "Scripted response"
        assert result.usage.total_tokens == 7

    def test_error_injection(self) -> None:
        mock = MockModelProvider(responses=[ModelConnectionError("endpoint down")])
        with pytest.raises(ModelConnectionError, match="endpoint down"):
            run_async(mock.generate(_make_request()))

    def test_queue_exhausted_raises(self) -> None:
        mock = MockModelProvider(responses=["one"])
        run_async(mock.generate(_make_request()))
        with pytest.raises(ModelResponseError, match="exhausted"):
            run_async(mock.generate(_make_request()))

    def test_loop_mode(self) -> None:
        mock = MockModelProvider(responses=["A", "B"], loop=True)
        r1 = run_async(mock.generate(_make_request()))
        r2 = run_async(mock.generate(_make_request()))
        r3 = run_async(mock.generate(_make_request()))
        assert r1.content == "A"
        assert r2.content == "B"
        assert r3.content == "A"

    def test_multiple_sequential_responses(self) -> None:
        mock = MockModelProvider(responses=["First", "Second", "Third"])
        results = [run_async(mock.generate(_make_request())) for _ in range(3)]
        assert [r.content for r in results] == ["First", "Second", "Third"]

    def test_call_count_tracked(self) -> None:
        mock = MockModelProvider(responses=["A", "B"])
        run_async(mock.generate(_make_request()))
        run_async(mock.generate(_make_request()))
        assert mock.call_count == 2

    def test_reset(self) -> None:
        mock = MockModelProvider(responses=["A"])
        run_async(mock.generate(_make_request()))
        mock.reset()
        assert mock.call_count == 0
        result = run_async(mock.generate(_make_request()))
        assert result.content == "A"

    def test_add_tool_call_response(self) -> None:
        mock = MockModelProvider()
        mock.add_tool_call_response("system.cpu_info", {})
        result = run_async(mock.generate(_make_request()))
        assert result.has_tool_calls is True
        assert result.tool_calls[0].name == "system.cpu_info"
        assert result.finish_reason == FinishReason.TOOL_CALL

    def test_health_check_true(self) -> None:
        mock = MockModelProvider(health=True)
        assert run_async(mock.check_health()) is True

    def test_health_check_false(self) -> None:
        mock = MockModelProvider(health=False)
        assert run_async(mock.check_health()) is False


class TestMockModelProviderStreaming:
    def test_stream_yields_chunks(self) -> None:
        mock = MockModelProvider(responses=["Hello world"])
        req = _make_request()

        async def collect() -> list[StreamChunk]:
            chunks = []
            # stream() is an async generator — iterate directly, no await
            async for chunk in mock.stream(req):
                chunks.append(chunk)
            return chunks

        chunks = run_async(collect())
        assert len(chunks) >= 2  # at least one content chunk + final
        assert chunks[-1].is_final is True
        assert chunks[-1].finish_reason == FinishReason.STOP

    def test_stream_full_text_reassembled(self) -> None:
        mock = MockModelProvider(responses=["Hello world"])

        async def reassemble() -> str:
            return "".join(
                [c.content async for c in mock.stream(_make_request()) if not c.is_final]
            ).strip()

        text = run_async(reassemble())
        assert "Hello" in text
        assert "world" in text

    def test_stream_error_injection(self) -> None:
        mock = MockModelProvider(responses=[ModelAPIError("bad gateway", 502)])

        async def run() -> None:
            async for _ in mock.stream(_make_request()):
                pass

        with pytest.raises(ModelAPIError):
            run_async(run())

    def test_stream_count_tracked(self) -> None:
        mock = MockModelProvider(responses=["A", "B"])

        async def run() -> None:
            async for _ in mock.stream(_make_request()):
                pass

        run_async(run())
        assert mock.call_count == 1


# ---------------------------------------------------------------------------
# HttpModelProvider — Request Building (no network)
# ---------------------------------------------------------------------------


class TestHttpProviderRequestBuilding:
    def test_build_payload_basic(self) -> None:
        req = ModelRequest(
            model="qwen3-8b",
            messages=[
                Message(role=Role.SYSTEM, content="You are helpful."),
                Message(role=Role.USER, content="Hello"),
            ],
            max_tokens=256,
            temperature=0.5,
        )
        payload = _build_request_payload(req, stream=False)
        assert payload["model"] == "qwen3-8b"
        assert payload["stream"] is False
        assert payload["max_tokens"] == 256
        assert payload["temperature"] == 0.5
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"

    def test_build_payload_stream_flag(self) -> None:
        req = _make_request()
        payload = _build_request_payload(req, stream=True)
        assert payload["stream"] is True

    def test_build_payload_extra_params(self) -> None:
        req = ModelRequest(
            model="m",
            messages=[Message(role=Role.USER, content="x")],
            extra_params={"top_p": 0.95, "seed": 42},
        )
        payload = _build_request_payload(req, stream=False)
        assert payload["top_p"] == 0.95
        assert payload["seed"] == 42

    def test_message_role_mapping(self) -> None:
        cases = [
            (Role.SYSTEM, "system"),
            (Role.USER, "user"),
            (Role.ASSISTANT, "assistant"),
            (Role.TOOL_RESULT, "tool"),
        ]
        for role, expected in cases:
            wire = _message_to_wire(Message(role=role, content="x"))
            assert wire["role"] == expected

    def test_parse_finish_reason(self) -> None:
        assert _parse_finish_reason("stop") == FinishReason.STOP
        assert _parse_finish_reason("length") == FinishReason.LENGTH
        assert _parse_finish_reason("tool_calls") == FinishReason.TOOL_CALL
        assert _parse_finish_reason(None) == FinishReason.UNKNOWN
        assert _parse_finish_reason("unknown_value") == FinishReason.UNKNOWN

    def test_parse_usage(self) -> None:
        raw = {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
        usage = _parse_usage(raw)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30
        assert usage.reasoning_tokens == 5

    def test_parse_usage_none(self) -> None:
        usage = _parse_usage(None)
        assert usage.total_tokens == 0


# ---------------------------------------------------------------------------
# HttpModelProvider — generate() with mocked HTTP
# ---------------------------------------------------------------------------


class TestHttpProviderGenerate:
    def _mock_response(self, data: dict[str, Any], status: int = 200) -> MagicMock:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = status
        mock_resp.json.return_value = data
        mock_resp.raise_for_status = MagicMock()
        if status >= 400:
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"HTTP {status}",
                request=MagicMock(),
                response=mock_resp,
            )
            mock_resp.text = json.dumps({"error": "bad request"})
        return mock_resp

    def test_successful_generate(self) -> None:
        provider = HttpModelProvider()
        oai_data = _make_oai_response("Hello from model!")
        req = _make_request(model="qwen3-8b")

        mock_resp = self._mock_response(oai_data)

        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = run_async(provider.generate(req))

        assert result.content == "Hello from model!"
        assert result.finish_reason == FinishReason.STOP
        assert result.model_id == "test-model"

    def test_generate_with_reasoning_content(self) -> None:
        provider = HttpModelProvider()
        oai_data = _make_oai_response(content="Answer", reasoning="Let me think...")
        req = _make_request()

        mock_resp = self._mock_response(oai_data)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = run_async(provider.generate(req))

        assert result.reasoning_content == "Let me think..."
        assert result.content == "Answer"

    def test_generate_http_500_raises_api_error(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()

        mock_resp = self._mock_response({}, status=500)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            with pytest.raises(ModelAPIError) as exc_info:
                run_async(provider.generate(req))
        assert exc_info.value.status_code == 500

    def test_generate_connection_error(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(ModelConnectionError):
                run_async(provider.generate(req))

    def test_generate_timeout_raises(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()

        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timed out"),
        ):
            with pytest.raises(ModelTimeoutError):
                run_async(provider.generate(req))

    def test_generate_empty_choices_raises(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()
        oai_data = {"id": "x", "choices": [], "usage": {}}

        mock_resp = self._mock_response(oai_data)
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            with pytest.raises(ModelResponseError, match="no choices"):
                run_async(provider.generate(req))

    def test_lmstudio_provider_default_url(self) -> None:
        provider = LMStudioProvider()
        assert "127.0.0.1:1234" in provider._base_url

    def test_lmstudio_is_http_provider(self) -> None:
        assert issubclass(LMStudioProvider, HttpModelProvider)


# ---------------------------------------------------------------------------
# HttpModelProvider — streaming with mocked SSE
# ---------------------------------------------------------------------------


class TestHttpProviderStreaming:
    def _make_sse_response(self, chunks: list[dict[str, Any]]) -> list[str]:
        """Build list of SSE lines from chunk dicts, terminated with [DONE]."""
        lines = [f"data: {json.dumps(c)}" for c in chunks]
        lines.append("data: [DONE]")
        return lines

    async def _collect_stream(
        self,
        provider: HttpModelProvider,
        request: ModelRequest,
        sse_lines: list[str],
    ) -> list[StreamChunk]:
        """Collect all chunks from a mocked streaming response."""
        chunks: list[StreamChunk] = []

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_stream_resp = MagicMock()  # sync MagicMock so raise_for_status() is sync
        mock_stream_resp.status_code = 200
        mock_stream_resp.raise_for_status = MagicMock()  # plain sync call
        mock_stream_resp.aiter_lines = mock_aiter_lines

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        # HttpModelProvider.stream() is a regular def returning an async generator
        with patch.object(httpx.AsyncClient, "stream", return_value=mock_cm):
            async for chunk in provider.stream(request):
                chunks.append(chunk)

        return chunks

    def test_stream_yields_content_chunks(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()
        sse_lines = self._make_sse_response(
            [
                _make_sse_chunk("Hello"),
                _make_sse_chunk(" world"),
                _make_sse_chunk("", finish="stop"),
            ]
        )

        chunks = run_async(self._collect_stream(provider, req, sse_lines))

        # At least 2 content chunks + final sentinel
        content_chunks = [c for c in chunks if c.content and not c.is_final]
        final_chunks = [c for c in chunks if c.is_final]
        assert len(content_chunks) >= 1
        assert len(final_chunks) == 1
        assert final_chunks[0].finish_reason == FinishReason.STOP

    def test_stream_text_reassembled(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()
        sse_lines = self._make_sse_response(
            [
                _make_sse_chunk("Hello"),
                _make_sse_chunk(" world"),
                _make_sse_chunk("", finish="stop"),
            ]
        )

        chunks = run_async(self._collect_stream(provider, req, sse_lines))
        full_text = "".join(c.content for c in chunks if not c.is_final)
        assert "Hello" in full_text
        assert "world" in full_text

    def test_stream_malformed_json_raises(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()
        bad_lines = ["data: {this is not valid json}"]

        with pytest.raises(ModelResponseError, match="Malformed"):
            run_async(self._collect_stream(provider, req, bad_lines))

    def test_stream_http_error_raises(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()

        # Use plain MagicMock so raise_for_status() is a synchronous raise
        mock_stream_resp = MagicMock()
        mock_stream_resp.status_code = 503
        error_resp = MagicMock()
        error_resp.status_code = 503
        mock_stream_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="Service Unavailable",
            request=MagicMock(),
            response=error_resp,
        )

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        async def run_stream() -> None:
            # stream() is a regular def returning an async generator — no await
            with patch.object(httpx.AsyncClient, "stream", return_value=mock_cm):
                async for _ in provider.stream(req):
                    pass

        with pytest.raises(ModelAPIError) as exc_info:
            run_async(run_stream())
        assert exc_info.value.status_code == 503

    def test_stream_skips_empty_lines(self) -> None:
        provider = HttpModelProvider()
        req = _make_request()
        # Mix of empty lines, comment lines, and real data
        sse_lines = [
            "",
            ": keep-alive",
            f"data: {json.dumps(_make_sse_chunk('Hi'))}",
            "",
            "data: [DONE]",
        ]
        chunks = run_async(self._collect_stream(provider, req, sse_lines))
        content = "".join(c.content for c in chunks if not c.is_final)
        assert "Hi" in content


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_check_success(self) -> None:
        provider = HttpModelProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(provider.check_health())
        assert result is True

    def test_health_check_failure_returns_false(self) -> None:
        provider = HttpModelProvider()
        with patch.object(
            httpx.AsyncClient,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = run_async(provider.check_health())
        assert result is False

    def test_health_check_non_200_returns_false(self) -> None:
        provider = HttpModelProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(provider.check_health())
        assert result is False


# ---------------------------------------------------------------------------
# Agent Integration — Provider Injection
# ---------------------------------------------------------------------------


class TestAgentModelProviderIntegration:
    def test_agent_accepts_mock_provider(self) -> None:
        mock = MockModelProvider(responses=["Hello!"])
        deps = AgentDependencies(model_provider=mock)
        agent = Agent(dependencies=deps)
        assert agent.dependencies.model_provider is mock

    def test_agent_accepts_http_provider(self) -> None:
        http = HttpModelProvider(base_url="http://localhost:9999/v1")
        deps = AgentDependencies(model_provider=http)
        agent = Agent(dependencies=deps)
        assert isinstance(agent.dependencies.model_provider, HttpModelProvider)

    def test_agent_without_provider_is_valid(self) -> None:
        """Constructing an Agent without a ModelProvider must remain valid (no network)."""
        agent = Agent()
        assert agent.dependencies.model_provider is None

    def test_provider_dependency_accepts_any_llmprovider_subclass(self) -> None:
        """AgentDependencies must accept any LLMProvider implementation."""

        class CustomProvider(LLMProvider):
            async def generate(self, request):
                return ModelResponse(content="custom", finish_reason=FinishReason.STOP)

            async def stream(self, request):  # type: ignore[override]
                yield StreamChunk(content="", is_final=True, finish_reason=FinishReason.STOP)

            async def check_health(self) -> bool:
                return True

        deps = AgentDependencies(model_provider=CustomProvider())
        agent = Agent(dependencies=deps)
        assert isinstance(agent.dependencies.model_provider, LLMProvider)

    def test_existing_tool_execution_unaffected(self) -> None:
        """Existing agent tool tests must not regress with model_provider added."""
        from tom.tools.bootstrap import setup_default_tools
        from tom.tools.registry import ToolRegistry

        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        mock_provider = MockModelProvider(responses=["ok"])
        deps = AgentDependencies(registry=reg, model_provider=mock_provider)
        agent = Agent(dependencies=deps)

        result = run_async(agent.execute_tool("system.cpu_info"))
        assert result.success is True
        assert agent.dependencies.model_provider is mock_provider

    def test_provider_not_coupled_to_lmstudio(self) -> None:
        """Agent must depend on the abstract LLMProvider, not LMStudioProvider."""
        import inspect

        import tom.agents.base as agent_base_module

        source = inspect.getsource(agent_base_module)
        assert "LMStudioProvider" not in source
        assert "HttpModelProvider" not in source
        assert "lmstudio" not in source.lower()
        # Should only reference the abstract base
        assert "LLMProvider" not in source  # Agent doesn't need to import it either
