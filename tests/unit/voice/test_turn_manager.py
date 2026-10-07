"""Unit tests for Phase 9 Iteration 9.4: ConversationTurnManager.

Deterministic and offline. No LLM, network, GPU, or model weights.
"""

from __future__ import annotations

from typing import Any

from tom.agents.task_manager import TaskManager
from tom.schemas.task import TaskState
from tom.schemas.turn import (
    BackgroundTaskSnapshot,
    EntityKind,
    EntityReferent,
    ResponseAction,
    TurnContext,
    TurnRecord,
    TurnStatus,
)
from tom.voice.turn_manager import ConversationTurnManager, contains_anaphoric_reference


def make_manager(**kwargs: Any) -> ConversationTurnManager:
    return ConversationTurnManager(**kwargs)


def snapshot(
    task_id: str = "task_1",
    goal: str = "Search the repository",
    state: TaskState = TaskState.EXECUTING,
) -> BackgroundTaskSnapshot:
    return BackgroundTaskSnapshot(task_id=task_id, goal=goal, state=state, max_steps=8)


# ---------------------------------------------------------------------------
# Turn state
# ---------------------------------------------------------------------------


def test_start_turn_opens_an_active_context_and_closes_it() -> None:
    manager = make_manager()

    ctx = manager.start_turn("Hey TOM, are you ready?")
    assert isinstance(ctx, TurnContext)
    assert ctx.status is TurnStatus.ACTIVE
    assert ctx.turn_index == 1
    assert manager.current is ctx

    record = manager.record_assistant_reply(ctx, "Ready and listening.")
    assert isinstance(record, TurnRecord)
    assert record.status is TurnStatus.COMPLETED
    assert record.user_text == "Hey TOM, are you ready?"
    assert record.assistant_text == "Ready and listening."
    assert manager.current is None
    assert len(manager.history) == 1


def test_turn_index_increments_monotonically() -> None:
    manager = make_manager()
    indexes = []
    for i in range(3):
        ctx = manager.start_turn(f"turn number {i}")
        indexes.append(ctx.turn_index)
        manager.record_assistant_reply(ctx, "ack")
    assert indexes == [1, 2, 3]


def test_interrupted_turn_is_recorded_and_marked() -> None:
    manager = make_manager()
    ctx = manager.start_turn("Summarize the entire Phase 8 architecture.")
    record = manager.interrupt_turn(ctx, partial_text="Phase 8 delivers")
    assert record.status is TurnStatus.INTERRUPTED
    assert record.interrupted is True
    assert record.assistant_text == "Phase 8 delivers"


def test_manager_rejects_invalid_history_bound() -> None:
    try:
        ConversationTurnManager(max_history_turns=0)
    except ValueError as exc:
        assert "max_history_turns" in str(exc)
    else:  # pragma: no cover - validation must hold
        raise AssertionError("max_history_turns must be validated")


# ---------------------------------------------------------------------------
# Bounded history
# ---------------------------------------------------------------------------


def test_history_is_bounded_to_configured_window() -> None:
    manager = make_manager(max_history_turns=3)
    for i in range(10):
        ctx = manager.start_turn(f"question {i}")
        manager.record_assistant_reply(ctx, f"answer {i}")

    assert len(manager) == 3
    history = manager.history
    assert [r.user_text for r in history] == ["question 7", "question 8", "question 9"]
    assert [r.turn_index for r in history] == [8, 9, 10]


def test_context_history_snapshot_respects_the_same_window() -> None:
    manager = make_manager(max_history_turns=2)
    for text in ("first", "second", "third"):
        ctx = manager.start_turn(text)
        manager.record_assistant_reply(ctx, "ok")

    ctx = manager.start_turn("fourth")
    assert [r.user_text for r in ctx.history] == ["second", "third"]


def test_history_property_returns_a_copy() -> None:
    manager = make_manager()
    ctx = manager.start_turn("hello there")
    manager.record_assistant_reply(ctx, "hi")
    snapshot_history = manager.history
    snapshot_history.clear()
    assert len(manager.history) == 1


def test_reset_clears_history_referents_and_current() -> None:
    manager = make_manager()
    ctx = manager.start_turn("read the config")
    manager.register_referent("last_file", EntityKind.FILE, "pyproject.toml", context=ctx)
    manager.record_assistant_reply(ctx, "done")
    manager.reset()
    assert manager.history == []
    assert manager.referents == {}
    assert manager.current is None


# ---------------------------------------------------------------------------
# Entity referents
# ---------------------------------------------------------------------------


def test_tool_call_creates_structured_file_referent() -> None:
    manager = make_manager()
    ctx = manager.start_turn("open the streaming tts module")

    ref = manager.record_tool_call(
        ctx,
        "files.read_file",
        {"path": "python/tom/voice/streaming_tts.py"},
    )

    assert ref is not None
    assert ref.name == "last_file"
    assert ref.kind is EntityKind.FILE
    assert ref.value == "python/tom/voice/streaming_tts.py"
    assert ref.turn_id == ctx.turn_id
    assert manager.referent("last_file") is not None
    assert manager.referent("last_tool").value == "files.read_file"


def test_referents_are_preserved_across_turns() -> None:
    manager = make_manager()

    turn1 = manager.start_turn("Search my repository for the resource manager.")
    manager.record_tool_call(turn1, "files.read_file", {"path": "python/tom/resources/manager.py"})
    manager.record_assistant_reply(turn1, "It lives in resources/manager.py.")
    assert manager.referent("last_file").value == "python/tom/resources/manager.py"

    turn2 = manager.start_turn("How many lines was that file?")
    assert turn2.referents["last_file"].value == "python/tom/resources/manager.py"
    assert turn2.referent("last_file") is not None

    manager.record_assistant_reply(turn2, "It has 512 lines.")
    turn3 = manager.start_turn("And the directory above it?")
    assert turn3.referent("last_file").value == "python/tom/resources/manager.py"
    assert [r.user_text for r in turn3.history][-1] == "How many lines was that file?"


def test_referent_snapshot_in_history_records_state_at_close() -> None:
    manager = make_manager()
    ctx = manager.start_turn("remember the release notes")
    ref = manager.register_referent(
        "last_memory", EntityKind.MEMORY, "release notes v1", context=ctx
    )
    record = manager.record_assistant_reply(ctx, "Stored it.")
    assert record.referents["last_memory"].value == ref.value


def test_tool_rules_cover_directory_query_and_screen_slots() -> None:
    manager = make_manager()
    ctx = manager.start_turn("inspect things")

    manager.record_tool_call(ctx, "files.list_directory", {"path": "docs/development"})
    manager.record_tool_call(ctx, "memory.search", {"query": "resource manager"})
    manager.record_tool_call(ctx, "vision.ocr", {"monitor": 0})

    assert manager.referent("last_directory").value == "docs/development"
    assert manager.referent("last_query").value == "resource manager"
    assert manager.referent("last_screen").kind is EntityKind.SCREEN


def test_tool_call_without_usable_value_creates_no_entity_referent() -> None:
    manager = make_manager()
    ctx = manager.start_turn("do something vague")
    assert manager.record_tool_call(ctx, "files.read_file", {"path": "  "}) is None
    assert manager.record_tool_call(ctx, "totally.unknown_tool", {}) is None
    assert manager.referent("last_file") is None
    assert manager.referent("last_tool").value == "totally.unknown_tool"


def test_explicit_referent_registration_overwrites_slot() -> None:
    manager = make_manager()
    ctx = manager.start_turn("first")
    manager.register_referent("last_file", EntityKind.FILE, "a.py", context=ctx)
    manager.register_referent("last_file", EntityKind.FILE, "b.py", context=ctx)
    assert manager.referent("last_file").value == "b.py"
    assert ctx.referents["last_file"].value == "b.py"


def test_start_turn_accepts_preloaded_referents() -> None:
    manager = make_manager()
    ref = EntityReferent(name="last_file", kind=EntityKind.FILE, value="c.py")
    ctx = manager.start_turn("follow up", referents={"last_file": ref})
    assert manager.referent("last_file") is ref
    assert ctx.referents["last_file"] is ref


def test_referent_describe_is_model_readable() -> None:
    ref = EntityReferent(name="last_file", kind=EntityKind.FILE, label="last file", value="a.py")
    assert ref.describe() == "last file: a.py"
    bare = EntityReferent(name="last_query", kind=EntityKind.QUERY, value="")
    assert bare.describe() == "last query"


# ---------------------------------------------------------------------------
# Anaphora signal
# ---------------------------------------------------------------------------


def test_anaphora_detection_is_deterministic() -> None:
    assert contains_anaphoric_reference("How many lines was that file?") is True
    assert contains_anaphoric_reference("Repeat the previous answer") is True
    assert contains_anaphoric_reference("Summarize this document") is True
    assert contains_anaphoric_reference("Hey TOM, are you ready?") is False
    assert contains_anaphoric_reference("Thanks TOM, that's all for now.") is False
    assert contains_anaphoric_reference("") is False


def test_follow_up_flag_requires_marker_and_context() -> None:
    manager = make_manager()
    cold = manager.start_turn("Summarize that document")
    assert cold.has_anaphoric_reference is True
    assert cold.is_follow_up is False

    warm = manager.start_turn("Summarize that document")
    manager.register_referent("last_file", EntityKind.FILE, "a.py", context=warm)
    later = manager.start_turn("Summarize that document")
    assert later.is_follow_up is True


# ---------------------------------------------------------------------------
# Phase 8 background task awareness
# ---------------------------------------------------------------------------


def test_background_tasks_are_snapshotted_from_task_manager() -> None:
    task_manager = TaskManager(db_path=":memory:")
    manager = make_manager(task_manager=task_manager)

    assert manager.sync_background_tasks() == ()

    task = task_manager.create("Compile the project")
    task_manager.transition(task.task_id, TaskState.ROUTING)
    task_manager.transition(task.task_id, TaskState.EXECUTING)

    ctx = manager.start_turn("what is happening?")
    assert ctx.has_running_task is True
    assert [t.task_id for t in ctx.background_tasks] == [task.task_id]
    assert ctx.background_tasks[0].goal == "Compile the project"
    assert ctx.background_tasks[0].state is TaskState.EXECUTING
    task_manager.close()


def test_completed_background_tasks_drop_out_of_the_snapshot() -> None:
    task_manager = TaskManager(db_path=":memory:")
    manager = make_manager(task_manager=task_manager)
    task = task_manager.create("Finish me")
    task_manager.transition(task.task_id, TaskState.ROUTING)
    task_manager.transition(task.task_id, TaskState.EXECUTING)
    manager.sync_background_tasks()
    assert manager.background_tasks

    task_manager.transition(task.task_id, TaskState.COMPLETING)
    task_manager.transition(task.task_id, TaskState.COMPLETED)
    assert manager.sync_background_tasks() == ()
    task_manager.close()


def test_background_snapshot_is_bounded() -> None:
    task_manager = TaskManager(db_path=":memory:")
    manager = make_manager(task_manager=task_manager, max_background_tasks=2)
    for i in range(5):
        task_manager.create(f"task {i}")
    assert len(manager.sync_background_tasks()) == 2
    task_manager.close()


def test_background_sync_is_resilient_to_manager_failure() -> None:
    class BrokenManager:
        def list_active(self) -> list[Any]:
            raise RuntimeError("database locked")

    manager = ConversationTurnManager(task_manager=BrokenManager())  # type: ignore[arg-type]
    assert manager.sync_background_tasks() == ()


def test_explicit_background_snapshot_is_carried_into_the_context() -> None:
    manager = make_manager()
    ctx = manager.start_turn("status?", background_tasks=(snapshot(),))
    assert ctx.has_running_task is True
    assert ctx.background_tasks[0].goal == "Search the repository"


# ---------------------------------------------------------------------------
# Model-facing structured context
# ---------------------------------------------------------------------------


def test_model_context_exposes_referents_history_and_tasks() -> None:
    manager = make_manager()
    ctx = manager.start_turn("read the config", planned_tools=("files.read_file",))
    manager.record_tool_call(ctx, "files.read_file", {"path": "pyproject.toml"})
    manager.record_assistant_reply(
        ctx, "Here is the config.", response_action=ResponseAction.SPEAK_DIRECT
    )

    follow_up = manager.start_turn("and the readme?", background_tasks=(snapshot(),))
    payload = follow_up.to_model_context()

    assert payload["user_text"] == "and the readme?"
    assert payload["referents"]["last_file"]["value"] == "pyproject.toml"
    assert payload["recent_turns"][0]["user_text"] == "read the config"
    assert payload["background_tasks"][0]["state"] == "EXECUTING"
    assert payload["planned_tools"] == []


def test_rendered_model_context_is_human_and_model_readable() -> None:
    manager = make_manager()
    ctx = manager.start_turn("read the config")
    manager.record_tool_call(ctx, "files.read_file", {"path": "pyproject.toml"})
    manager.record_assistant_reply(ctx, "Here it is.")

    rendered = manager.render_model_context()
    assert "Recent conversation:" in rendered
    assert "read the config" in rendered
    assert "Known referents:" in rendered
    assert "pyproject.toml" in rendered


def test_build_model_context_handles_empty_session() -> None:
    manager = make_manager()
    payload = manager.build_model_context()
    assert payload == {"turn": None, "referents": {}, "history": [], "background_tasks": []}


def test_metadata_flows_from_start_turn_into_context() -> None:
    manager = make_manager()
    ctx = manager.start_turn("go", metadata={"estimated_latency_ms": 2500})
    assert ctx.metadata["estimated_latency_ms"] == 2500
    assert ctx.to_model_context()["metadata"]["estimated_latency_ms"] == 2500


def test_referent_property_snapshot_is_isolated() -> None:
    manager = make_manager()
    ctx = manager.start_turn("x")
    manager.register_referent("last_file", EntityKind.FILE, "a.py", context=ctx)
    copied = manager.referents
    copied["last_file"] = None
    assert manager.referent("last_file") is not None


def test_turn_context_serialises_to_json() -> None:
    import json

    manager = make_manager()
    ctx = manager.start_turn("hello there")
    manager.record_tool_call(ctx, "files.read_file", {"path": "a.py"})
    payload = json.loads(ctx.model_dump_json())
    assert payload["turn_id"] == ctx.turn_id
    assert payload["referents"]["last_tool"]["value"] == "files.read_file"
