"""Live smoke test for LM Studio / Bionic OpenAI-compatible endpoint.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Prompt §19: Optional LM Studio Live Smoke Test
- Skips gracefully if LM Studio is not running (zero network dependencies in CI)
- Dynamically discovers loaded model ID (no hardcoded model names)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from tom.models.lmstudio import LMStudioProvider
from tom.schemas.agent import Message, Role
from tom.schemas.models import ModelRequest

BASE_URL = "http://localhost:1234/v1"


def run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


def _is_lmstudio_running() -> tuple[bool, str]:
    """Check if LM Studio is reachable and retrieve the first available chat model."""
    try:
        resp = httpx.get(f"{BASE_URL}/models", timeout=1.5)
        if resp.status_code != 200:
            return False, ""
        data = resp.json()
        models = data.get("data", [])
        if not models:
            return False, ""
        chat_models = [m["id"] for m in models if "embedding" not in m.get("id", "").lower()]
        model_id = chat_models[0] if chat_models else models[0]["id"]
        return True, model_id
    except Exception:
        return False, ""


@pytest.fixture(scope="module")
def lmstudio_available() -> str:
    """Fixture ensuring LM Studio is running, returning the discovered model ID."""
    running, model_id = _is_lmstudio_running()
    if not running or not model_id:
        pytest.skip(f"LM Studio not reachable at {BASE_URL} — skipping live smoke test")
    return model_id


def test_live_lmstudio_health_check() -> None:
    """Verify check_health() returns True when LM Studio is running."""
    running, _ = _is_lmstudio_running()
    if not running:
        pytest.skip(f"LM Studio not reachable at {BASE_URL}")

    async def _test() -> None:
        provider = LMStudioProvider(base_url=BASE_URL)
        is_healthy = await provider.check_health()
        assert is_healthy is True

    run_async(_test())


def test_live_lmstudio_generate(lmstudio_available: str) -> None:
    """Send a minimal request to live LM Studio and verify valid response."""
    model_id = lmstudio_available
    provider = LMStudioProvider(base_url=BASE_URL)

    request = ModelRequest(
        model=model_id,
        messages=[
            Message(role=Role.USER, content="Reply with exactly the single word: PONG"),
        ],
        temperature=0.0,
        max_tokens=150,
    )

    response = run_async(provider.generate(request))
    # Models may emit text in content or reasoning_content (e.g. Qwen3-8B thinking tokens)
    generated = (response.content + response.reasoning_content).strip()
    assert len(generated) > 0
    assert response.finish_reason is not None


def test_live_lmstudio_stream(lmstudio_available: str) -> None:
    """Stream a minimal completion from live LM Studio and verify incremental chunks."""
    model_id = lmstudio_available
    provider = LMStudioProvider(base_url=BASE_URL)

    request = ModelRequest(
        model=model_id,
        messages=[
            Message(role=Role.USER, content="Count from 1 to 3: 1, 2, 3"),
        ],
        temperature=0.0,
        max_tokens=150,
    )

    async def _stream() -> list[Any]:
        chunks = []
        async for chunk in provider.stream(request):
            chunks.append(chunk)
        return chunks

    chunks = run_async(_stream())
    assert len(chunks) >= 1
    # Check that chunks deliver text delta (content or thinking/reasoning delta)
    assert any((c.content or c.reasoning_content) for c in chunks)
    assert chunks[-1].is_final is True
