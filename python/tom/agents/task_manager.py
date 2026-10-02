"""TaskManager — global task lifecycle coordinator for TOM Phase 8.

Adheres to:
- Phase 8 Architecture (Task Lifecycle & TaskManager)
- Decision 051: Task Lifecycle State Machine
- Decision 052: SQLite as authoritative task audit store

Responsibilities:
- Create tasks and assign UUIDs
- Enforce the valid task state transition matrix
- Persist task state to SQLite (non-blocking, best-effort)
- Signal cooperative CancellationToken on cancel
- Query tasks by ID and list active tasks
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tom.core.context import CancellationToken
from tom.schemas.task import (
    TASK_TERMINAL_STATES,
    TASK_VALID_TRANSITIONS,
    InvalidTaskTransitionError,
    StepResult,
    Task,
    TaskResult,
    TaskState,
)
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)


class TaskNotFoundError(Exception):
    """Raised when a task_id is not found in the registry."""


class TaskManager:
    """Global task lifecycle coordinator.

    Manages task creation, state transitions, cancellation signalling,
    and SQLite-backed audit persistence. Thread-safe via a single RLock.

    Usage:
        manager = TaskManager(db_path=":memory:")
        task = manager.create("Summarise my Downloads folder")
        manager.transition(task.task_id, TaskState.ROUTING)
        manager.cancel(task.task_id)
    """

    def __init__(self, db_path: str | Path = "data/memory/tom.db") -> None:
        self._db_path = str(db_path)
        self._lock = threading.RLock()
        # In-memory registry: task_id -> (Task, CancellationToken)
        self._registry: dict[str, tuple[Task, CancellationToken]] = {}
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        goal: str,
        max_steps: int = 15,
        timeout_seconds: float = 300.0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new task in CREATED state and register it.

        Returns the new Task instance.
        """
        task = Task(
            goal=goal,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            metadata=metadata or {},
        )
        token = CancellationToken()
        with self._lock:
            self._registry[task.task_id] = (task, token)
            self._persist_task(task)
        logger.info("task_created", task_id=task.task_id, goal=goal)
        return task

    def get(self, task_id: str) -> Task:
        """Return the current Task snapshot for task_id.

        Raises TaskNotFoundError if unknown.
        """
        with self._lock:
            entry = self._registry.get(task_id)
        if entry is None:
            raise TaskNotFoundError(task_id)
        return entry[0]

    def get_token(self, task_id: str) -> CancellationToken:
        """Return the CancellationToken bound to a task."""
        with self._lock:
            entry = self._registry.get(task_id)
        if entry is None:
            raise TaskNotFoundError(task_id)
        return entry[1]

    def transition(self, task_id: str, new_state: TaskState) -> Task:
        """Transition a task to new_state, enforcing the valid transition matrix.

        Raises InvalidTaskTransitionError on illegal transitions.
        Raises TaskNotFoundError if unknown task.
        """
        with self._lock:
            entry = self._registry.get(task_id)
            if entry is None:
                raise TaskNotFoundError(task_id)
            task, token = entry
            allowed = TASK_VALID_TRANSITIONS.get(task.state, set())
            if new_state not in allowed:
                raise InvalidTaskTransitionError(task.state, new_state)
            task.state = new_state
            task.updated_at = datetime.now(UTC)
            self._persist_task(task)
        logger.info(
            "task_transition",
            task_id=task_id,
            new_state=new_state.value,
        )
        return task

    def record_step(self, task_id: str, result: StepResult) -> None:
        """Record a step execution result for a task."""
        with self._lock:
            entry = self._registry.get(task_id)
            if entry is None:
                raise TaskNotFoundError(task_id)
            task = entry[0]
            task.current_step_index = result.step_index + 1
            task.updated_at = datetime.now(UTC)
            self._persist_step(task_id, result)

    def fail(self, task_id: str, error: str) -> Task:
        """Transition task to FAILED with an error message."""
        with self._lock:
            entry = self._registry.get(task_id)
            if entry is None:
                raise TaskNotFoundError(task_id)
            task, _ = entry
            # Allow transition from any non-terminal state to FAILED
            if task.state not in TASK_TERMINAL_STATES:
                # Bypass strict matrix only for fail-safe; emit to FAILED directly
                task.state = TaskState.FAILED
                task.error = error
                task.updated_at = datetime.now(UTC)
                self._persist_task(task)
        logger.warning("task_failed", task_id=task_id, error=error)
        return task

    def cancel(self, task_id: str) -> Task:
        """Signal cooperative cancellation and transition task to CANCELLED.

        Safe to call multiple times — idempotent once already cancelled.
        """
        with self._lock:
            entry = self._registry.get(task_id)
            if entry is None:
                raise TaskNotFoundError(task_id)
            task, token = entry
            token.cancel()
            if task.state not in TASK_TERMINAL_STATES:
                task.state = TaskState.CANCELLED
                task.updated_at = datetime.now(UTC)
                self._persist_task(task)
        logger.info("task_cancelled", task_id=task_id)
        return task

    def complete(self, task_id: str, result: TaskResult) -> Task:
        """Transition task through COMPLETING → COMPLETED with a TaskResult."""
        with self._lock:
            entry = self._registry.get(task_id)
            if entry is None:
                raise TaskNotFoundError(task_id)
            task, _ = entry
            # Must go via COMPLETING first
            if task.state == TaskState.EXECUTING:
                task.state = TaskState.COMPLETING
            if task.state == TaskState.COMPLETING:
                task.state = TaskState.COMPLETED
                task.updated_at = datetime.now(UTC)
                self._persist_task(task)
        logger.info("task_completed", task_id=task_id)
        return task

    def list_active(self) -> list[Task]:
        """Return all non-terminal tasks."""
        with self._lock:
            return [
                entry[0]
                for entry in self._registry.values()
                if entry[0].state not in TASK_TERMINAL_STATES
            ]

    def list_all(self) -> list[Task]:
        """Return all tracked tasks."""
        with self._lock:
            return [entry[0] for entry in self._registry.values()]

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    state TEXT NOT NULL,
                    max_steps INTEGER NOT NULL,
                    timeout_seconds REAL NOT NULL,
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    replan_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    step_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    output TEXT,
                    error TEXT,
                    execution_time_ms REAL NOT NULL DEFAULT 0,
                    executed_at TEXT NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id);"
            )

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            if self._db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def _persist_task(self, task: Task) -> None:
        try:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO tasks
                        (task_id, goal, state, max_steps, timeout_seconds,
                         current_step_index, replan_count, error, metadata,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        state=excluded.state,
                        current_step_index=excluded.current_step_index,
                        replan_count=excluded.replan_count,
                        error=excluded.error,
                        metadata=excluded.metadata,
                        updated_at=excluded.updated_at;
                    """,
                    (
                        task.task_id,
                        task.goal,
                        task.state.value,
                        task.max_steps,
                        task.timeout_seconds,
                        task.current_step_index,
                        task.replan_count,
                        task.error,
                        json.dumps(task.metadata),
                        task.created_at.isoformat(),
                        task.updated_at.isoformat(),
                    ),
                )
        except Exception as exc:
            # Non-blocking: log and continue; in-memory registry is authoritative
            logger.error("task_persist_failed", task_id=task.task_id, error=str(exc))

    def _persist_step(self, task_id: str, result: StepResult) -> None:
        try:
            conn = self._get_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO task_steps
                        (task_id, step_id, step_index, tool_name, success,
                         output, error, execution_time_ms, executed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        task_id,
                        result.step_id,
                        result.step_index,
                        result.tool_name,
                        int(result.success),
                        json.dumps(result.output) if result.output is not None else None,
                        result.error,
                        result.execution_time_ms,
                        result.executed_at.isoformat(),
                    ),
                )
        except Exception as exc:
            logger.error("step_persist_failed", task_id=task_id, error=str(exc))

    def close(self) -> None:
        """Close the SQLite connection. Call on shutdown."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
