"""Unit tests for consistent assistant identity across TOM."""

from datetime import datetime

from tom.agents.prompts import (
    CLI_SYSTEM_PROMPT,
    VOICE_SYSTEM_PROMPT,
    build_system_prompt,
    get_temporal_context,
    refresh_prompt_temporal_context,
)
from tom.identity import (
    ASSISTANT_DESCRIPTION,
    ASSISTANT_NAME,
    ASSISTANT_ROLE,
    USER_ROLE,
)
from tom.schemas.agent import AgentConfig, ConversationHistory, Message, Role


def test_authoritative_identity_constants() -> None:
    """Identity configuration has canonical definitions."""
    assert ASSISTANT_NAME == "TOM"
    assert ASSISTANT_ROLE == "assistant"
    assert USER_ROLE == "user"
    assert "TOM" in ASSISTANT_DESCRIPTION


def test_prompts_do_not_contain_alex_or_wrong_identities() -> None:
    """System prompts declare TOM and do not contain 'Alex' or confuse roles."""
    assert "You are TOM" in VOICE_SYSTEM_PROMPT
    assert "You are TOM" in CLI_SYSTEM_PROMPT

    for prompt_name, prompt in [("VOICE", VOICE_SYSTEM_PROMPT), ("CLI", CLI_SYSTEM_PROMPT)]:
        assert "Alex" not in prompt, f"Found 'Alex' in {prompt_name} system prompt"
        # Verify the prompt does not identify the assistant as 'user'
        assert "You are user" not in prompt


def test_build_system_prompt_consumes_canonical_identity() -> None:
    """build_system_prompt returns prompts with canonical ASSISTANT_NAME."""
    voice_prompt = build_system_prompt("voice")
    cli_prompt = build_system_prompt("cli")
    assert f"You are {ASSISTANT_NAME}" in voice_prompt
    assert f"You are {ASSISTANT_NAME}" in cli_prompt


def test_agent_config_default_identity() -> None:
    """AgentConfig defaults to TOM identity."""
    config = AgentConfig()
    assert "TOM" in config.system_prompt
    assert "Alex" not in config.system_prompt


def test_conversation_history_roles_preservation() -> None:
    """ConversationHistory preserves user and assistant roles correctly."""
    history = ConversationHistory()
    history.append(Message(role=Role.USER, content="Hello TOM"))
    history.append(Message(role=Role.ASSISTANT, content="Hello, I am TOM."))

    assert history.messages[0].role == Role.USER
    assert history.messages[1].role == Role.ASSISTANT
    assert "TOM" in history.messages[1].content


def test_temporal_context_anchoring() -> None:
    """Verify temporal context contains accurate dates and anti-cutoff disclaimer."""
    fixed_time = datetime(2026, 11, 6, 16, 15, 0)
    ctx = get_temporal_context(fixed_time)

    assert "CURRENT TEMPORAL CONTEXT:" in ctx
    assert "Friday, November 06, 2026" in ctx
    assert "2026" in ctx
    assert "You are NOT in 2023" in ctx

    voice_prompt = build_system_prompt("voice", now=fixed_time)
    assert "CURRENT TEMPORAL CONTEXT:" in voice_prompt
    assert "Friday, November 06, 2026" in voice_prompt

    cli_prompt = build_system_prompt("cli", now=fixed_time)
    assert "CURRENT TEMPORAL CONTEXT:" in cli_prompt
    assert "Friday, November 06, 2026" in cli_prompt


def test_refresh_prompt_temporal_context_idempotence() -> None:
    """Refreshing an existing prompt updates the date without duplicating the header."""
    t1 = datetime(2026, 11, 6, 10, 0, 0)
    p1 = build_system_prompt("voice", now=t1)
    assert p1.count("CURRENT TEMPORAL CONTEXT:") == 1
    assert "10:00 AM" in p1

    t2 = datetime(2026, 11, 6, 16, 30, 0)
    p2 = refresh_prompt_temporal_context(p1, now=t2)
    assert p2.count("CURRENT TEMPORAL CONTEXT:") == 1
    assert "04:30 PM" in p2
    assert "10:00 AM" not in p2
