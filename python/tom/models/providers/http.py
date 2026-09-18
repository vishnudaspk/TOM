"""HttpModelProvider — OpenAI-compatible HTTP provider for TOM.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- skills/lm-studio: Endpoint at http://127.0.0.1:1234/v1, standard OpenAI-compatible API
- skills/coding/structured-logging: Secret redaction on Authorization headers

This provider communicates with any OpenAI-compatible /v1/chat/completions endpoint.
Tested against LM Studio (qwen3-8b) at http://127.0.0.1:1234/v1.

Wire format observed from LM Studio (qwen3-8b):
    Non-streaming response shape:
        {
            "id": "chatcmpl-...",
            "object": "chat.completion",
            "created": <unix_ts>,
            "model": "qwen3-8b",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "<text>",
                    "reasoning_content": "<thinking>",
                    "tool_calls": []
                },
                "logprobs": null,
                "finish_reason": "stop" | "length" | "tool_calls"
            }],
            "usage": {
                "prompt_tokens": N,
                "completion_tokens": N,
                "total_tokens": N,
                "completion_tokens_details": {"reasoning_tokens": N}
            },
            "stats": {},
            "system_fingerprint": "qwen3-8b"
        }

    Streaming SSE format (data: <json>\\n\\n, terminated by data: [DONE]):
        Each chunk "delta" carries incremental "content" and "reasoning_content".
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from tom.models.base import (
    LLMProvider,
    ModelAPIError,
    ModelConnectionError,
    ModelResponseError,
    ModelTimeoutError,
)
from tom.schemas.agent import Message, Role
from tom.schemas.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolCallRequest,
)
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)

# Timeout defaults (seconds)
_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_READ_TIMEOUT = 120.0
_DEFAULT_STREAM_TIMEOUT = 300.0


def _role_to_wire(role: Role) -> str:
    """Map TOM Role enum to OpenAI wire role string."""
    mapping: dict[Role, str] = {
        Role.SYSTEM: "system",
        Role.USER: "user",
        Role.ASSISTANT: "assistant",
        Role.TOOL_CALL: "assistant",  # tool-call message from assistant
        Role.TOOL_RESULT: "tool",
    }
    return mapping.get(role, "user")


def _message_to_wire(msg: Message) -> dict[str, Any]:
    """Convert a TOM Message to an OpenAI-compatible wire dict."""
    wire: dict[str, Any] = {
        "role": _role_to_wire(msg.role),
        "content": msg.content or "",
    }
    if msg.name:
        wire["name"] = msg.name
    if msg.tool_call_id and msg.role == Role.TOOL_RESULT:
        wire["tool_call_id"] = msg.tool_call_id
    return wire


def _parse_finish_reason(raw: str | None) -> FinishReason:
    """Convert a provider finish_reason string to TOM FinishReason."""
    if not raw:
        return FinishReason.UNKNOWN
    mapping = {
        "stop": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "tool_calls": FinishReason.TOOL_CALL,
        "content_filter": FinishReason.CONTENT_FILTER,
    }
    return mapping.get(raw.lower(), FinishReason.UNKNOWN)


def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCallRequest]:
    """Parse OpenAI-compatible tool_calls array into TOM ToolCallRequest list."""
    result: list[ToolCallRequest] = []
    for tc in raw_calls:
        try:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            if isinstance(args_raw, str):
                args = json.loads(args_raw)
            else:
                args = args_raw
            result.append(
                ToolCallRequest(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("http_provider_tool_call_parse_error", error=str(exc))
    return result


def _parse_usage(raw: dict[str, Any] | None) -> TokenUsage:
    """Parse OpenAI-compatible usage dict into TOM TokenUsage."""
    if not raw:
        return TokenUsage()
    details = raw.get("completion_tokens_details") or {}
    return TokenUsage(
        prompt_tokens=raw.get("prompt_tokens", 0),
        completion_tokens=raw.get("completion_tokens", 0),
        total_tokens=raw.get("total_tokens", 0),
        reasoning_tokens=details.get("reasoning_tokens", 0),
    )


def _build_request_payload(request: ModelRequest, *, stream: bool) -> dict[str, Any]:
    """Build the OpenAI-compatible HTTP request body from a TOM ModelRequest."""
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [_message_to_wire(m) for m in request.messages],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "stream": stream,
    }
    if request.tools:
        payload["tools"] = request.tools
        payload["tool_choice"] = "auto"
    # Pass through extra provider-specific params
    payload.update(request.extra_params)
    return payload


class HttpModelProvider(LLMProvider):
    """OpenAI-compatible HTTP model provider.

    Communicates with any OpenAI-compatible /v1/chat/completions endpoint.
    Designed for local LM Studio / Bionic at http://127.0.0.1:1234/v1 but
    fully configurable for cloud or other local runtimes.

    The base_url and model must be set at construction time; neither is baked
    into the architecture (Decision 033).

    Authorization headers are automatically redacted from logs.

    Args:
        base_url: Base URL of the OpenAI-compatible API (no trailing slash).
        api_key: Optional API key for cloud endpoints. Redacted in logs.
        connect_timeout: Seconds before connection attempt gives up.
        read_timeout: Seconds before a non-streaming read gives up.
        stream_timeout: Seconds before a streaming read gives up.
        headers: Optional extra HTTP headers.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        *,
        api_key: str | None = None,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        stream_timeout: float = _DEFAULT_STREAM_TIMEOUT,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._stream_timeout = stream_timeout

        base_headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            base_headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            base_headers.update(headers)
        self._headers = base_headers

    def _client(self, *, stream: bool = False) -> httpx.AsyncClient:
        """Construct a fresh AsyncClient with appropriate timeouts."""
        timeout = httpx.Timeout(
            connect=self._connect_timeout,
            read=self._stream_timeout if stream else self._read_timeout,
            write=30.0,
            pool=5.0,
        )
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=timeout,
            follow_redirects=True,
        )

    def _wrap_http_error(
        self, exc: Exception, context: str
    ) -> ModelConnectionError | ModelAPIError | ModelTimeoutError:
        """Convert httpx exceptions to TOM provider errors."""
        if isinstance(exc, httpx.TimeoutException):
            return ModelTimeoutError(f"{context}: request timed out — {exc}")
        if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError)):
            return ModelConnectionError(f"{context}: connection failed — {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            return ModelAPIError(
                f"{context}: HTTP {exc.response.status_code} — {exc.response.text[:200]}",
                status_code=exc.response.status_code,
            )
        if isinstance(exc, httpx.RequestError):
            return ModelConnectionError(f"{context}: request error — {exc}")
        # Unknown httpx error
        return ModelConnectionError(f"{context}: unexpected httpx error — {exc}")

    def _parse_completion_response(
        self,
        data: dict[str, Any],
        request: ModelRequest,
    ) -> ModelResponse:
        """Convert a raw OpenAI-compatible completion dict to ModelResponse."""
        try:
            choices = data.get("choices", [])
            if not choices:
                raise ModelResponseError("Provider returned no choices in response")

            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            raw_tool_calls = message.get("tool_calls") or []
            finish_reason = _parse_finish_reason(choice.get("finish_reason"))

            return ModelResponse(
                content=content,
                reasoning_content=reasoning,
                tool_calls=_parse_tool_calls(raw_tool_calls),
                finish_reason=finish_reason,
                usage=_parse_usage(data.get("usage")),
                model_id=data.get("model", request.model),
                provider_metadata={
                    "id": data.get("id", ""),
                    "system_fingerprint": data.get("system_fingerprint", ""),
                },
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelResponseError(
                f"Failed to parse provider response: {exc}\nRaw: {str(data)[:500]}"
            ) from exc

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Perform a non-streaming completion via POST /chat/completions.

        Args:
            request: Typed TOM model request.

        Returns:
            Fully assembled ModelResponse.

        Raises:
            ModelConnectionError: Endpoint not reachable.
            ModelAPIError: Provider returned HTTP error.
            ModelTimeoutError: Request exceeded timeout.
            ModelResponseError: Response payload is malformed.
            asyncio.CancelledError: Caller cancelled (re-raised cleanly).
        """
        payload = _build_request_payload(request, stream=False)
        t_start = time.perf_counter()

        logger.info(
            "http_provider_generate_start",
            model=request.model,
            base_url=self._base_url,
            messages_count=len(request.messages),
        )

        try:
            async with self._client(stream=False) as client:
                resp = await client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except asyncio.CancelledError:
            logger.info("http_provider_generate_cancelled", model=request.model)
            raise
        except httpx.HTTPStatusError as exc:
            raise ModelAPIError(
                f"HTTP {exc.response.status_code} from {self._base_url}: {exc.response.text[:300]}",
                status_code=exc.response.status_code,
            ) from exc
        except Exception as exc:
            if isinstance(
                exc, (ModelConnectionError, ModelAPIError, ModelTimeoutError, ModelResponseError)
            ):
                raise
            raise self._wrap_http_error(exc, "generate") from exc

        latency_ms = int((time.perf_counter() - t_start) * 1000)
        response = self._parse_completion_response(data, request)

        logger.info(
            "http_provider_generate_complete",
            model=request.model,
            latency_ms=latency_ms,
            finish_reason=response.finish_reason.value,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        return response

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:  # type: ignore[override]
        """Perform a streaming completion via POST /chat/completions with stream=true.

        Returns an async generator that yields StreamChunk objects.
        This is intentionally a regular def (not async def) so that callers can use:
            async for chunk in provider.stream(request): ...

        Parses Server-Sent Events (SSE) format:
            data: <json>\\n\\n
            data: [DONE]\\n\\n

        Args:
            request: Typed TOM model request.

        Yields:
            StreamChunk objects with incremental content.

        Raises:
            ModelConnectionError: Endpoint not reachable.
            ModelAPIError: Provider returned HTTP error.
            ModelTimeoutError: Connection/stream timed out.
            ModelResponseError: SSE chunk is malformed.
            asyncio.CancelledError: Caller cancelled (re-raised cleanly).
        """
        payload = _build_request_payload(request, stream=True)

        logger.info(
            "http_provider_stream_start",
            model=request.model,
            base_url=self._base_url,
        )

        return self._stream_generator(request, payload)

    async def _stream_generator(
        self,
        request: ModelRequest,
        payload: dict[str, Any],
    ) -> AsyncIterator[StreamChunk]:
        """Internal async generator that parses SSE chunks."""
        t_start = time.perf_counter()
        chunks_yielded = 0
        final_usage: TokenUsage | None = None
        final_finish: FinishReason = FinishReason.UNKNOWN

        try:
            async with self._client(stream=True) as client:
                async with client.stream("POST", "/chat/completions", json=payload) as resp:
                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise ModelAPIError(
                            f"HTTP {exc.response.status_code} from {self._base_url}",
                            status_code=exc.response.status_code,
                        ) from exc

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue

                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data_str)
                        except json.JSONDecodeError as exc:
                            logger.warning(
                                "http_provider_stream_malformed_chunk",
                                raw=data_str[:200],
                                error=str(exc),
                            )
                            raise ModelResponseError(
                                f"Malformed SSE chunk: {exc}\nRaw: {data_str[:200]}"
                            ) from exc

                        choices = chunk_data.get("choices", [])
                        if not choices:
                            # May carry usage on final chunk (some providers)
                            if chunk_data.get("usage"):
                                final_usage = _parse_usage(chunk_data["usage"])
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})
                        finish_raw = choice.get("finish_reason")

                        content_delta = delta.get("content") or ""
                        reasoning_delta = delta.get("reasoning_content") or ""
                        raw_tool_calls = delta.get("tool_calls") or []

                        # Track final state
                        if finish_raw:
                            final_finish = _parse_finish_reason(finish_raw)
                        if chunk_data.get("usage"):
                            final_usage = _parse_usage(chunk_data["usage"])

                        # Yield intermediate chunk (non-final)
                        if content_delta or reasoning_delta or raw_tool_calls:
                            chunks_yielded += 1
                            yield StreamChunk(
                                content=content_delta,
                                reasoning_content=reasoning_delta,
                                tool_call_delta=_parse_tool_calls(raw_tool_calls)[0]
                                if raw_tool_calls
                                else None,
                                finish_reason=None,
                                is_final=False,
                            )

        except asyncio.CancelledError:
            logger.info(
                "http_provider_stream_cancelled",
                model=request.model,
                chunks_yielded=chunks_yielded,
            )
            raise
        except (ModelAPIError, ModelConnectionError, ModelTimeoutError, ModelResponseError):
            raise
        except Exception as exc:
            raise self._wrap_http_error(exc, "stream") from exc

        # Final sentinel chunk
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        logger.info(
            "http_provider_stream_complete",
            model=request.model,
            latency_ms=latency_ms,
            chunks_yielded=chunks_yielded,
            finish_reason=final_finish.value,
        )
        yield StreamChunk(
            content="",
            finish_reason=final_finish,
            usage=final_usage,
            is_final=True,
        )

    async def check_health(self) -> bool:
        """Return True if the endpoint is reachable and responding.

        Calls GET /models — a lightweight, read-only discovery endpoint.
        Never raises; returns False on any failure.
        """
        try:
            async with self._client(stream=False) as client:
                resp = await client.get("/models")
                return resp.status_code == 200
        except Exception as exc:
            logger.info("http_provider_health_check_failed", error=str(exc))
            return False


# Convenience alias: LMStudioProvider IS HttpModelProvider with default LM Studio URL.
# The alias makes intent clear in code that specifically targets LM Studio.
# Any code that should remain provider-agnostic should use HttpModelProvider.
class LMStudioProvider(HttpModelProvider):
    """HttpModelProvider pre-configured for local LM Studio.

    Default base_url: http://127.0.0.1:1234/v1

    LM Studio exposes a standard OpenAI-compatible /v1/chat/completions endpoint.
    Model identifier must be provided explicitly — never hardcoded.

    Example:
        provider = LMStudioProvider()
        request = ModelRequest(model="qwen3-8b", messages=[...])
        response = await provider.generate(request)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        *,
        api_key: str | None = None,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        stream_timeout: float = _DEFAULT_STREAM_TIMEOUT,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            stream_timeout=stream_timeout,
        )
