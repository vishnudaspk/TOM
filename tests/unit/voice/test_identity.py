"""Unit tests for consistent assistant identity across TOM."""

from tom.agents.prompts import CLI_SYSTEM_PROMPT, VOICE_SYSTEM_PROMPT, build_system_prompt
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
