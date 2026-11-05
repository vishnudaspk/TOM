"""CORE-004 — Canonical Structured Conversation History and Recall Verification.

Verifies:
1. Tool relationship preservation during trimming (never orphan tool results).
2. Token-based context budgeting (enforces token budget while keeping system prompt).
3. 12+ conversational turns without nested prompt duplication.
4. First-turn and last-turn recall integrity.
5. Session isolation (distinct histories do not leak state).
"""

from __future__ import annotations

import pytest
from tom.schemas.agent import ConversationHistory, Message, ToolCallMetadata
from tom.schemas.models import Role


def test_tool_call_result_pairing_preserved_during_trim() -> None:
    """Trimming must never leave an orphaned TOOL_RESULT at the start of history."""
    history = ConversationHistory(max_messages=10, max_tokens=10000)
    history.append(Message(role=Role.SYSTEM, content="System prompt"))
    history.append(Message(role=Role.USER, content="Run tool 1"))
    history.append(
        Message(
            role=Role.ASSISTANT,
            content="Running...",
            tool_call_id="call_1",
            tool_metadata=ToolCallMetadata(tool_name="tool_1"),
        )
    )
    history.append(
        Message(
            role=Role.TOOL_RESULT,
            content="Result 1",
            tool_call_id="call_1",
        )
    )
    history.append(Message(role=Role.ASSISTANT, content="Done tool 1"))
    history.append(Message(role=Role.USER, content="Follow up"))
    history.append(Message(role=Role.ASSISTANT, content="Follow up answer"))

    # If we trim to 4 messages (system + 3 messages):
    # Oldest candidate would land on TOOL_RESULT or ASSISTANT tool call.
    history.trim(max_count=4)

    messages = history.get_messages()
    assert messages[0].role == Role.SYSTEM
    # First non-system message must NOT be a TOOL_RESULT
    assert messages[1].role != Role.TOOL_RESULT


def test_token_based_context_budgeting() -> None:
    """Trimming by max_tokens preserves system prompt and keeps newest messages within budget."""
    history = ConversationHistory(max_messages=100, max_tokens=60)
    history.append(Message(role=Role.SYSTEM, content="System"))

    # Add several messages with known token sizes
    history.append(Message(role=Role.USER, content="Short question 1"))
    history.append(Message(role=Role.ASSISTANT, content="Short answer 1"))
    history.append(Message(role=Role.USER, content="Short question 2"))
    history.append(Message(role=Role.ASSISTANT, content="Short answer 2"))
    history.append(Message(role=Role.USER, content="Latest user question"))

    # Total tokens must not exceed budget
    assert history.total_tokens() <= 60
    messages = history.get_messages()
    assert messages[0].role == Role.SYSTEM
    assert messages[-1].content == "Latest user question"


def test_twelve_turn_conversation_recall_without_nested_duplication() -> None:
    """A 12-turn dialogue maintains clean linear history without nested prompt prefix bloat."""
    history = ConversationHistory(max_messages=50, max_tokens=8192)
    history.append(Message(role=Role.SYSTEM, content="You are TOM."))

    secret_key = "KANGAROO-99"
    history.append(Message(role=Role.USER, content=f"Remember this secret key: {secret_key}"))
    history.append(
        Message(role=Role.ASSISTANT, content=f"I have remembered the secret key: {secret_key}.")
    )

    # Run 11 additional turns
    for i in range(2, 13):
        history.append(Message(role=Role.USER, content=f"Turn {i} question: What is {i} * 2?"))
        history.append(Message(role=Role.ASSISTANT, content=f"Turn {i} answer: {i * 2}."))

    assert len(history) == 25  # 1 system + 12 user + 12 assistant

    # First turn is preserved
    first_user_turn = history.messages[1]
    assert secret_key in first_user_turn.content
    assert "Recent conversation:" not in first_user_turn.content

    # Twelfth turn has no nested prompt duplication
    twelfth_user_turn = history.messages[-2]
    assert "Turn 12 question" in twelfth_user_turn.content
    assert "Recent conversation:" not in twelfth_user_turn.content


def test_session_isolation_no_leakage() -> None:
    """Distinct ConversationHistory instances do not leak state or messages."""
    session_a = ConversationHistory(max_messages=10)
    session_b = ConversationHistory(max_messages=10)

    session_a.append(Message(role=Role.USER, content="Secret A"))
    session_b.append(Message(role=Role.USER, content="Secret B"))

    assert len(session_a) == 1
    assert len(session_b) == 1
    assert session_a.messages[0].content == "Secret A"
    assert session_b.messages[0].content == "Secret B"
    assert "Secret B" not in [m.content for m in session_a.messages]
    assert "Secret A" not in [m.content for m in session_b.messages]


@pytest.mark.asyncio
async def test_cli_repro_conversation_continuity() -> None:
    """CLI repro: user stores a secret across turns, agent recalls it without stale answer repetition."""
    from unittest.mock import AsyncMock, MagicMock

    from tom.agents.base import Agent
    from tom.agents.dependencies import AgentDependencies
    from tom.agents.orchestrator import AgentOrchestrator
    from tom.models.base import LLMProvider
    from tom.schemas.agent import AgentConfig
    from tom.schemas.models import ModelRequest, ModelResponse
    from tom.schemas.router import IntentDomain, RoutingDecision

    class ScriptedLLM(LLMProvider):
        async def generate(self, req: ModelRequest) -> ModelResponse:
            history_text = " ".join(m.content for m in req.messages)
            if "What was the word" in req.messages[-1].content:
                if "KANGAROO" in history_text:
                    return ModelResponse(content="The word was KANGAROO.", model="test")
                return ModelResponse(content="I do not remember.", model="test")
            return ModelResponse(content="I will remember that.", model="test")

        async def stream(self, req):
            yield None

        async def check_health(self):
            return True

        def get_model_info(self):
            return {}

    deps = AgentDependencies(model_provider=ScriptedLLM())
    agent = Agent(config=AgentConfig(system_prompt="You are TOM."), dependencies=deps)
    router = MagicMock()
    router.classify = AsyncMock(
        return_value=RoutingDecision(
            domain=IntentDomain.REASONING, confidence=1.0, route_tier=1, latency_ms=1.0
        )
    )
    orch = AgentOrchestrator(agent=agent, router=router)

    r1 = await orch.run("Remember the word KANGAROO.")
    assert "remember" in r1.lower()
    r2 = await orch.run("What was the word I asked you to remember?")
    assert "KANGAROO" in r2
    assert len(agent.history) == 5  # 1 system + 2 user + 2 assistant
