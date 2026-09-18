"""Unit tests for Agent context, message models, and conversation history.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 1)
- skills/coding/validation: Pydantic v2 validation, schema constraints.
- skills/testing/python-testing: Strict assertions, boundary conditions, serialization.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tom.schemas.agent import (
    ConversationHistory,
    Message,
    Role,
    ToolCallMetadata,
)


class TestRoleEnum:
    """Verifies the Role enumeration completeness and string values."""

    def test_all_expected_roles_exist(self) -> None:
        expected = {"SYSTEM", "USER", "ASSISTANT", "TOOL_CALL", "TOOL_RESULT"}
        actual = {role.value for role in Role}
        assert actual == expected

    def test_role_is_strenum(self) -> None:
        assert Role.USER == "USER"
        assert Role.ASSISTANT == "ASSISTANT"


class TestMessageModel:
    """Verifies Message validation, timestamps, and metadata handling."""

    def test_create_valid_message(self) -> None:
        msg = Message(role=Role.USER, content="Hello TOM")
        assert msg.role == Role.USER
        assert msg.content == "Hello TOM"
        assert msg.timestamp.tzinfo is not None
        assert msg.name is None
        assert msg.tool_call_id is None
        assert msg.tool_metadata is None

    def test_message_with_naive_timestamp_becomes_utc(self) -> None:
        naive = datetime(2026, 9, 18, 12, 0, 0)
        msg = Message(role=Role.SYSTEM, content="System prompt", timestamp=naive)
        assert msg.timestamp.tzinfo == UTC

    def test_message_with_tool_metadata_model(self) -> None:
        meta = ToolCallMetadata(
            tool_name="system.cpu_info",
            tool_call_id="call_123",
            parameters={"detail": True},
            execution_time_ms=1.45,
            success=True,
        )
        msg = Message(
            role=Role.TOOL_RESULT,
            content='{"usage_percent": 15.2}',
            name="system.cpu_info",
            tool_call_id="call_123",
            tool_metadata=meta,
        )
        assert msg.role == Role.TOOL_RESULT
        assert msg.name == "system.cpu_info"
        assert msg.tool_call_id == "call_123"
        assert isinstance(msg.tool_metadata, ToolCallMetadata)
        assert msg.tool_metadata.execution_time_ms == 1.45

    def test_message_with_tool_metadata_dict(self) -> None:
        msg = Message(
            role=Role.TOOL_CALL,
            content="call system.cpu_info",
            tool_metadata={"tool_name": "system.cpu_info"},
        )
        assert isinstance(msg.tool_metadata, ToolCallMetadata)
        assert msg.tool_metadata.tool_name == "system.cpu_info"

    def test_message_invalid_role_raises(self) -> None:
        with pytest.raises(ValidationError):
            Message(role="INVALID_ROLE", content="test")  # type: ignore[arg-type]

    def test_message_missing_content_raises(self) -> None:
        with pytest.raises(ValidationError):
            Message(role=Role.USER)  # type: ignore[call-arg]


class TestConversationHistory:
    """Verifies ConversationHistory storage, bounded trimming, and serialization."""

    def test_append_message_object(self) -> None:
        history = ConversationHistory()
        msg = Message(role=Role.USER, content="Query 1")
        history.append(msg)
        assert len(history) == 1
        assert history[0].content == "Query 1"

    def test_append_dict_validates(self) -> None:
        history = ConversationHistory()
        history.append({"role": "ASSISTANT", "content": "Response 1"})
        assert len(history) == 1
        assert history[0].role == Role.ASSISTANT
        assert history[0].content == "Response 1"

    def test_append_invalid_type_raises(self) -> None:
        history = ConversationHistory()
        with pytest.raises(TypeError):
            history.append(12345)  # type: ignore[arg-type]

    def test_ordering_preserved(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.SYSTEM, content="System"))
        history.append(Message(role=Role.USER, content="User 1"))
        history.append(Message(role=Role.ASSISTANT, content="Assistant 1"))
        history.append(Message(role=Role.USER, content="User 2"))

        contents = [m.content for m in history]
        assert contents == ["System", "User 1", "Assistant 1", "User 2"]

    def test_bounded_history_trims_oldest_non_system(self) -> None:
        history = ConversationHistory(max_messages=4)
        history.append(Message(role=Role.SYSTEM, content="System Prompt"))
        history.append(Message(role=Role.USER, content="U1"))
        history.append(Message(role=Role.ASSISTANT, content="A1"))
        history.append(Message(role=Role.USER, content="U2"))
        assert len(history) == 4

        # Adding 5th message exceeds max_messages=4
        history.append(Message(role=Role.ASSISTANT, content="A2"))
        assert len(history) == 4
        # System prompt preserved; U1 trimmed
        assert history[0].content == "System Prompt"
        assert history[1].content == "A1"
        assert history[2].content == "U2"
        assert history[3].content == "A2"

    def test_bounded_history_without_system_trims_oldest(self) -> None:
        history = ConversationHistory(max_messages=3)
        history.append(Message(role=Role.USER, content="U1"))
        history.append(Message(role=Role.USER, content="U2"))
        history.append(Message(role=Role.USER, content="U3"))
        assert len(history) == 3

        history.append(Message(role=Role.USER, content="U4"))
        assert len(history) == 3
        assert [m.content for m in history] == ["U2", "U3", "U4"]

    def test_trim_method_explicit(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.SYSTEM, content="Sys"))
        for i in range(10):
            history.append(Message(role=Role.USER, content=f"Msg {i}"))

        assert len(history) == 11
        removed = history.trim(5)
        assert removed == 6
        assert len(history) == 5
        assert history[0].content == "Sys"
        # The remaining 4 should be the latest messages (6, 7, 8, 9)
        assert [m.content for m in history[1:]] == ["Msg 6", "Msg 7", "Msg 8", "Msg 9"]

    def test_trim_to_zero_or_negative(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.USER, content="U1"))
        history.append(Message(role=Role.USER, content="U2"))

        removed = history.trim(0)
        assert removed == 2
        assert len(history) == 0

    def test_trim_when_already_under_limit(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.USER, content="U1"))
        removed = history.trim(10)
        assert removed == 0
        assert len(history) == 1

    def test_clear_preserves_system_by_default(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.SYSTEM, content="Sys"))
        history.append(Message(role=Role.USER, content="U1"))
        history.append(Message(role=Role.ASSISTANT, content="A1"))

        history.clear(keep_system=True)
        assert len(history) == 1
        assert history[0].role == Role.SYSTEM

        history.clear(keep_system=False)
        assert len(history) == 0

    def test_get_messages_returns_shallow_copy(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.USER, content="U1"))
        msgs = history.get_messages()
        msgs.append(Message(role=Role.USER, content="U2"))
        assert len(history) == 1

    def test_serialization_and_deserialization_roundtrip(self) -> None:
        history = ConversationHistory(max_messages=50)
        history.append(Message(role=Role.SYSTEM, content="Sys"))
        history.append(
            Message(
                role=Role.TOOL_RESULT,
                content="result data",
                name="files.read_file",
                tool_call_id="call_abc",
                tool_metadata=ToolCallMetadata(
                    tool_name="files.read_file",
                    execution_time_ms=2.5,
                    success=True,
                ),
            )
        )

        # Dump to JSON
        json_data = history.model_dump_json()
        assert isinstance(json_data, str)

        # Validate back
        restored = ConversationHistory.model_validate_json(json_data)
        assert len(restored) == 2
        assert restored.max_messages == 50
        assert restored[0].role == Role.SYSTEM
        assert restored[0].content == "Sys"
        assert restored[1].role == Role.TOOL_RESULT
        assert restored[1].tool_call_id == "call_abc"
        assert restored[1].tool_metadata is not None
        assert isinstance(restored[1].tool_metadata, ToolCallMetadata)
        assert restored[1].tool_metadata.tool_name == "files.read_file"
        assert restored[1].tool_metadata.execution_time_ms == 2.5

    def test_dict_serialization_roundtrip(self) -> None:
        history = ConversationHistory()
        history.append(Message(role=Role.USER, content="User message"))
        dumped = history.model_dump()
        assert "messages" in dumped
        assert dumped["messages"][0]["content"] == "User message"

        restored = ConversationHistory.model_validate(dumped)
        assert len(restored) == 1
        assert restored[0].content == "User message"
