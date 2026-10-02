"""Unit tests for TaskManager (Phase 8 Iteration 0).

Tests the 9-state task lifecycle, transition enforcement,
cancellation signalling, step recording, and SQLite persistence.
All tests run offline with an in-memory SQLite database.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tom.agents.task_manager import TaskManager, TaskNotFoundError
from tom.schemas.task import (
    TASK_TERMINAL_STATES,
    TASK_VALID_TRANSITIONS,
    InvalidTaskTransitionError,
    StepResult,
    Task,
    TaskState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manager() -> TaskManager:
    """Return a TaskManager backed by an in-memory SQLite database."""
    return TaskManager(db_path=":memory:")


# ---------------------------------------------------------------------------
# Schema: TaskState & transition matrix
# ---------------------------------------------------------------------------


class TestTaskStateEnum:
    def test_all_nine_states_exist(self) -> None:
        states = {s.value for s in TaskState}
        expected = {
            "CREATED",
            "ROUTING",
            "PLANNING",
            "EXECUTING",
            "WAITING_CONFIRMATION",
            "COMPLETING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert states == expected

    def test_terminal_states(self) -> None:
        assert TaskState.COMPLETED in TASK_TERMINAL_STATES
        assert TaskState.FAILED in TASK_TERMINAL_STATES
        assert TaskState.CANCELLED in TASK_TERMINAL_STATES
        # Non-terminal states must not be in the frozenset
        assert TaskState.EXECUTING not in TASK_TERMINAL_STATES
        assert TaskState.PLANNING not in TASK_TERMINAL_STATES

    def test_completed_has_no_outgoing_transitions(self) -> None:
        assert TASK_VALID_TRANSITIONS[TaskState.COMPLETED] == set()

    def test_failed_has_no_outgoing_transitions(self) -> None:
        assert TASK_VALID_TRANSITIONS[TaskState.FAILED] == set()

    def test_cancelled_has_no_outgoing_transitions(self) -> None:
        assert TASK_VALID_TRANSITIONS[TaskState.CANCELLED] == set()

    def test_executing_can_replan(self) -> None:
        assert TaskState.PLANNING in TASK_VALID_TRANSITIONS[TaskState.EXECUTING]

    def test_executing_can_reach_waiting_confirmation(self) -> None:
        assert TaskState.WAITING_CONFIRMATION in TASK_VALID_TRANSITIONS[TaskState.EXECUTING]


# ---------------------------------------------------------------------------
# Schema: Task model
# ---------------------------------------------------------------------------


class TestTaskModel:
    def test_default_state_is_created(self) -> None:
        task = Task(goal="do something")
        assert task.state == TaskState.CREATED

    def test_task_id_generated(self) -> None:
        task = Task(goal="do something")
        assert task.task_id.startswith("task_")
        assert len(task.task_id) > 5

    def test_goal_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Task(goal="")

    def test_max_steps_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Task(goal="x", max_steps=0)
        with pytest.raises(ValidationError):
            Task(goal="x", max_steps=31)

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Task(goal="x", timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            Task(goal="x", timeout_seconds=901.0)


# ---------------------------------------------------------------------------
# TaskManager: creation
# ---------------------------------------------------------------------------


class TestTaskManagerCreate:
    def test_create_returns_task(self) -> None:
        mgr = make_manager()
        task = mgr.create("Summarise my Downloads folder")
        assert task.state == TaskState.CREATED
        assert task.goal == "Summarise my Downloads folder"

    def test_created_task_retrievable(self) -> None:
        mgr = make_manager()
        task = mgr.create("list files")
        fetched = mgr.get(task.task_id)
        assert fetched.task_id == task.task_id

    def test_get_unknown_raises(self) -> None:
        mgr = make_manager()
        with pytest.raises(TaskNotFoundError):
            mgr.get("nonexistent_id")

    def test_created_task_in_list_active(self) -> None:
        mgr = make_manager()
        task = mgr.create("active task")
        active = mgr.list_active()
        ids = [t.task_id for t in active]
        assert task.task_id in ids

    def test_custom_budgets_preserved(self) -> None:
        mgr = make_manager()
        task = mgr.create("work", max_steps=5, timeout_seconds=60.0)
        assert task.max_steps == 5
        assert task.timeout_seconds == 60.0


# ---------------------------------------------------------------------------
# TaskManager: state transitions
# ---------------------------------------------------------------------------


class TestTaskManagerTransitions:
    def test_created_to_routing(self) -> None:
        mgr = make_manager()
        task = mgr.create("route me")
        updated = mgr.transition(task.task_id, TaskState.ROUTING)
        assert updated.state == TaskState.ROUTING

    def test_routing_to_planning(self) -> None:
        mgr = make_manager()
        task = mgr.create("plan something")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.PLANNING)
        assert mgr.get(task.task_id).state == TaskState.PLANNING

    def test_routing_to_executing(self) -> None:
        mgr = make_manager()
        task = mgr.create("direct tool")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        assert mgr.get(task.task_id).state == TaskState.EXECUTING

    def test_invalid_transition_raises(self) -> None:
        mgr = make_manager()
        task = mgr.create("x")
        with pytest.raises(InvalidTaskTransitionError):
            # Cannot jump from CREATED directly to EXECUTING
            mgr.transition(task.task_id, TaskState.EXECUTING)

    def test_cannot_transition_from_completed(self) -> None:
        mgr = make_manager()
        task = mgr.create("finish")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        mgr.transition(task.task_id, TaskState.COMPLETING)
        mgr.transition(task.task_id, TaskState.COMPLETED)
        with pytest.raises(InvalidTaskTransitionError):
            mgr.transition(task.task_id, TaskState.ROUTING)

    def test_cannot_transition_from_failed(self) -> None:
        mgr = make_manager()
        task = mgr.create("fail")
        mgr.fail(task.task_id, "boom")
        with pytest.raises(InvalidTaskTransitionError):
            mgr.transition(task.task_id, TaskState.ROUTING)

    def test_transition_unknown_task_raises(self) -> None:
        mgr = make_manager()
        with pytest.raises(TaskNotFoundError):
            mgr.transition("ghost_task", TaskState.ROUTING)

    def test_executing_to_waiting_confirmation(self) -> None:
        mgr = make_manager()
        task = mgr.create("click something")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        mgr.transition(task.task_id, TaskState.WAITING_CONFIRMATION)
        assert mgr.get(task.task_id).state == TaskState.WAITING_CONFIRMATION

    def test_waiting_confirmation_back_to_executing(self) -> None:
        mgr = make_manager()
        task = mgr.create("confirm")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        mgr.transition(task.task_id, TaskState.WAITING_CONFIRMATION)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        assert mgr.get(task.task_id).state == TaskState.EXECUTING

    def test_executing_can_replan(self) -> None:
        mgr = make_manager()
        task = mgr.create("replan")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        mgr.transition(task.task_id, TaskState.PLANNING)
        assert mgr.get(task.task_id).state == TaskState.PLANNING


# ---------------------------------------------------------------------------
# TaskManager: cancellation
# ---------------------------------------------------------------------------


class TestTaskManagerCancellation:
    def test_cancel_transitions_to_cancelled(self) -> None:
        mgr = make_manager()
        task = mgr.create("cancel me")
        mgr.cancel(task.task_id)
        assert mgr.get(task.task_id).state == TaskState.CANCELLED

    def test_cancel_signals_token(self) -> None:
        mgr = make_manager()
        task = mgr.create("cancel token")
        token = mgr.get_token(task.task_id)
        assert not token.is_cancelled()
        mgr.cancel(task.task_id)
        assert token.is_cancelled()

    def test_cancel_is_idempotent(self) -> None:
        mgr = make_manager()
        task = mgr.create("idempotent cancel")
        mgr.cancel(task.task_id)
        mgr.cancel(task.task_id)  # should not raise
        assert mgr.get(task.task_id).state == TaskState.CANCELLED

    def test_cancel_removes_from_active_list(self) -> None:
        mgr = make_manager()
        task = mgr.create("remove from active")
        mgr.cancel(task.task_id)
        active_ids = [t.task_id for t in mgr.list_active()]
        assert task.task_id not in active_ids

    def test_cancel_unknown_raises(self) -> None:
        mgr = make_manager()
        with pytest.raises(TaskNotFoundError):
            mgr.cancel("ghost")


# ---------------------------------------------------------------------------
# TaskManager: fail
# ---------------------------------------------------------------------------


class TestTaskManagerFail:
    def test_fail_transitions_to_failed(self) -> None:
        mgr = make_manager()
        task = mgr.create("fail me")
        mgr.fail(task.task_id, "something went wrong")
        assert mgr.get(task.task_id).state == TaskState.FAILED

    def test_fail_records_error(self) -> None:
        mgr = make_manager()
        task = mgr.create("error capture")
        mgr.fail(task.task_id, "disk full")
        fetched = mgr.get(task.task_id)
        assert fetched.error == "disk full"

    def test_fail_from_executing(self) -> None:
        mgr = make_manager()
        task = mgr.create("fail mid-exec")
        mgr.transition(task.task_id, TaskState.ROUTING)
        mgr.transition(task.task_id, TaskState.EXECUTING)
        mgr.fail(task.task_id, "tool error")
        assert mgr.get(task.task_id).state == TaskState.FAILED


# ---------------------------------------------------------------------------
# TaskManager: step recording
# ---------------------------------------------------------------------------


class TestTaskManagerStepRecording:
    def test_record_step_advances_index(self) -> None:
        mgr = make_manager()
        task = mgr.create("multi-step")
        step = StepResult(
            step_id="step_aaa",
            step_index=0,
            tool_name="system.ping",
            success=True,
            output={"ok": True},
            execution_time_ms=12.3,
        )
        mgr.record_step(task.task_id, step)
        fetched = mgr.get(task.task_id)
        assert fetched.current_step_index == 1

    def test_record_step_unknown_task_raises(self) -> None:
        mgr = make_manager()
        step = StepResult(
            step_id="s",
            step_index=0,
            tool_name="files.read_file",
            success=False,
        )
        with pytest.raises(TaskNotFoundError):
            mgr.record_step("ghost", step)


# ---------------------------------------------------------------------------
# TaskManager: listing
# ---------------------------------------------------------------------------


class TestTaskManagerListing:
    def test_list_all_includes_terminal(self) -> None:
        mgr = make_manager()
        t1 = mgr.create("active")
        t2 = mgr.create("done")
        mgr.cancel(t2.task_id)
        all_ids = [t.task_id for t in mgr.list_all()]
        assert t1.task_id in all_ids
        assert t2.task_id in all_ids

    def test_list_active_excludes_terminal(self) -> None:
        mgr = make_manager()
        t1 = mgr.create("active")
        t2 = mgr.create("cancelled")
        mgr.cancel(t2.task_id)
        active_ids = [t.task_id for t in mgr.list_active()]
        assert t1.task_id in active_ids
        assert t2.task_id not in active_ids


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class TestTaskConfig:
    def test_task_config_loads_from_tomconfig(self) -> None:
        from tom.schemas.config import TOMConfig

        cfg = TOMConfig()
        assert cfg.task.default_max_steps == 15
        assert cfg.task.hard_max_steps == 30
        assert cfg.task.max_replans == 2
        assert cfg.task.confirmation_timeout_seconds == 120.0
        assert cfg.task.loop_detection_window == 3

    def test_task_config_bounds(self) -> None:
        from tom.schemas.config import TaskConfig

        with pytest.raises(ValidationError):
            TaskConfig(default_max_steps=0)
        with pytest.raises(ValidationError):
            TaskConfig(hard_max_steps=51)
        with pytest.raises(ValidationError):
            TaskConfig(loop_detection_window=1)
