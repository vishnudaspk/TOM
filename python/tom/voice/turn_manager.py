"""Conversation turn state, bounded history, and entity referents.

Phase 9 Iteration 9.4.

Design constraints (Phase 9 Architectural Principles 4 and 5):
- **No coreference engine.** The manager records *structured* facts — what was
  said, which tools ran, which entities are now addressable — and hands them to
  the existing model. Resolving "that" / "the previous one" stays the model's
  job.
- **Deterministic.** Referent extraction is a fixed table lookup on tool
  metadata; no LLM, no network, no locking.
- **Ephemeral.** Everything here is in-process RAM. Voice turns stay out of
  SQLite/Qdrant (Decision 045, STATE invariant 5).

``TaskManager`` (Phase 8) is read-only from this module: the manager snapshots
active background tasks so ``ResponsePolicy`` can choose ``REPORT_PROGRESS``.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from tom.schemas.task import Task
from tom.schemas.turn import (
    BackgroundTaskSnapshot,
    EntityKind,
    EntityReferent,
    ResponseAction,
    TurnContext,
    TurnRecord,
    TurnStatus,
)
from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    from tom.agents.task_manager import TaskManager

logger = get_logger(__name__)


# Back-reference markers. Presence is reported as a *fact*
# (``TurnContext.has_anaphoric_reference``); resolution is left to the model.
# Contraction forms ("that's", "it's") are excluded: they are not object
# back-references.
ANAPHORA_PATTERN = re.compile(
    r"\b(that|this|these|those|there)\b(?!'s|'re|'ll|'ve|'d)"
    r"|\bthe\s+(previous|last|same|above)\b",
    re.IGNORECASE,
)

# Deterministic tool-metadata -> referent rules. (tool_name, slot, kind, arg key)
_TOOL_REFERRULES: dict[str, tuple[str, EntityKind, str]] = {
    "files.read_file": ("last_file", EntityKind.FILE, "path"),
    "files.get_metadata": ("last_file", EntityKind.FILE, "path"),
    "files.write_file": ("last_file", EntityKind.FILE, "path"),
    "files.copy_file": ("last_file", EntityKind.FILE, "destination"),
    "files.delete_file": ("last_file", EntityKind.FILE, "path"),
    "files.list_directory": ("last_directory", EntityKind.DIRECTORY, "path"),
    "memory.search": ("last_query", EntityKind.QUERY, "query"),
    "memory.recall": ("last_query", EntityKind.QUERY, "query"),
    "memory.remember": ("last_memory", EntityKind.MEMORY, "content"),
    "vision.capture": ("last_screen", EntityKind.SCREEN, "monitor"),
    "vision.ocr": ("last_screen", EntityKind.SCREEN, "monitor"),
    "vision.find_element": ("last_screen", EntityKind.SCREEN, "monitor"),
    "vision.ask": ("last_screen", EntityKind.SCREEN, "monitor"),
    "os.input.click": ("last_input_target", EntityKind.APP, "x"),
    "os.input.type_text": ("last_input_target", EntityKind.APP, "text"),
    "os.input.hotkey": ("last_input_target", EntityKind.APP, "keys"),
}

_LAST_TOOL_SLOT = "last_tool"


class ConversationTurnManager:
    """Tracks turn lifecycle, bounded history, entity referents, and background work.

    Usage::

        manager = ConversationTurnManager(task_manager=task_manager)
        ctx = manager.start_turn("Search my repo for the resource manager")
        ...
        manager.record_tool_call(ctx, "files.read_file", {"path": "python/tom/resources/manager.py"})
        manager.record_assistant_reply(ctx, "Found it in manager.py.")
        policy.evaluate(ctx.user_text, ctx)  # -> ResponseAction
    """

    def __init__(
        self,
        task_manager: TaskManager | None = None,
        *,
        max_history_turns: int = 8,
        max_background_tasks: int = 8,
    ) -> None:
        if max_history_turns < 1:
            raise ValueError("max_history_turns must be >= 1")
        self._task_manager = task_manager
        self._history: deque[TurnRecord] = deque(maxlen=max_history_turns)
        self._referents: dict[str, EntityReferent] = {}
        self._max_background_tasks = max_background_tasks
        self._background: tuple[BackgroundTaskSnapshot, ...] = ()
        self._current: TurnContext | None = None
        self._turn_counter: int = 0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def current(self) -> TurnContext | None:
        """The in-flight turn context, or None between turns."""
        return self._current

    @property
    def history(self) -> list[TurnRecord]:
        """Bounded copy of recent turns (oldest first)."""
        return list(self._history)

    @property
    def referents(self) -> dict[str, EntityReferent]:
        """Copy of the currently addressable entity referents."""
        return dict(self._referents)

    @property
    def background_tasks(self) -> tuple[BackgroundTaskSnapshot, ...]:
        """Last snapshot of active Phase 8 background tasks."""
        return self._background

    def referent(self, name: str) -> EntityReferent | None:
        """Return the named referent if it is still addressable."""
        return self._referents.get(name)

    def __len__(self) -> int:
        return len(self._history)

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def start_turn(
        self,
        user_text: str,
        *,
        planned_tools: tuple[str, ...] = (),
        referents: dict[str, EntityReferent] | None = None,
        background_tasks: tuple[BackgroundTaskSnapshot, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TurnContext:
        """Open a new turn and return its structured context.

        The context captures a *snapshot* of referents, history, and background
        tasks so a turn stays internally consistent even if later turns mutate
        manager state.
        """
        self._turn_counter += 1
        if background_tasks is None:
            background_tasks = self.sync_background_tasks()
        if referents is not None:
            for name, ref in referents.items():
                self._referents[name] = ref

        context = TurnContext(
            turn_index=self._turn_counter,
            user_text=user_text,
            status=TurnStatus.ACTIVE,
            has_anaphoric_reference=contains_anaphoric_reference(user_text),
            planned_tools=tuple(planned_tools),
            referents=dict(self._referents),
            history=tuple(self._history),
            background_tasks=background_tasks,
            metadata=dict(metadata or {}),
        )
        self._current = context
        return context

    def record_tool_call(
        self,
        context: TurnContext | None,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> EntityReferent | None:
        """Register referents for a tool call using the deterministic rule table.

        Returns the entity referent created (or updated), or None when the tool
        carries no addressable value. Never raises on unknown tools.
        """
        params = parameters or {}
        self._referents[_LAST_TOOL_SLOT] = EntityReferent(
            name=_LAST_TOOL_SLOT,
            kind=EntityKind.TOOL,
            label="last tool used",
            value=tool_name,
            turn_id=self._turn_id(context),
        )

        rule = _TOOL_REFERRULES.get(tool_name)
        if rule is None:
            return None
        slot, kind, arg_key = rule
        value = params.get(arg_key)
        if value is None or isinstance(value, (list, dict, tuple)):
            return None
        text = str(value).strip()
        if not text:
            return None
        referent = EntityReferent(
            name=slot,
            kind=kind,
            label=f"last {kind.value}",
            value=text[:2000],
            turn_id=self._turn_id(context),
        )
        self._referents[slot] = referent
        if context is not None:
            context.referents[slot] = referent
            context.referents[_LAST_TOOL_SLOT] = self._referents[_LAST_TOOL_SLOT]
        return referent

    def register_referent(
        self,
        name: str,
        kind: EntityKind,
        value: str,
        *,
        label: str = "",
        context: TurnContext | None = None,
    ) -> EntityReferent:
        """Explicitly register (or overwrite) a referent slot."""
        referent = EntityReferent(
            name=name,
            kind=kind,
            label=label or f"last {kind.value}",
            value=value,
            turn_id=self._turn_id(context),
        )
        self._referents[name] = referent
        if context is not None:
            context.referents[name] = referent
        return referent

    def record_assistant_reply(
        self,
        context: TurnContext | None,
        assistant_text: str,
        *,
        response_action: ResponseAction | None = None,
        tool_names: tuple[str, ...] = (),
    ) -> TurnRecord:
        """Close the turn, append it to bounded history, and return the record."""
        return self._close(
            context, assistant_text, response_action, tool_names, TurnStatus.COMPLETED
        )

    def interrupt_turn(
        self,
        context: TurnContext | None,
        partial_text: str = "",
    ) -> TurnRecord:
        """Close the turn as INTERRUPTED (barge-in), preserving the partial reply."""
        return self._close(context, partial_text, None, (), TurnStatus.INTERRUPTED)

    # ------------------------------------------------------------------
    # Phase 8 background task awareness
    # ------------------------------------------------------------------

    def sync_background_tasks(self) -> tuple[BackgroundTaskSnapshot, ...]:
        """Snapshot active Phase 8 tasks. Read-only; safe when TaskManager is absent."""
        if self._task_manager is None:
            self._background = ()
            return ()
        try:
            tasks = self._task_manager.list_active()
        except Exception as exc:
            logger.warning("turn_manager_background_sync_failed", error=str(exc))
            self._background = ()
            return ()
        self._background = tuple(
            _task_snapshot(task)
            for task in sorted(tasks, key=lambda t: t.created_at)[: self._max_background_tasks]
        )
        return self._background

    # ------------------------------------------------------------------
    # Model-facing context
    # ------------------------------------------------------------------

    def build_model_context(self) -> dict[str, Any]:
        """Deterministic structured payload describing the current conversation."""
        return {
            "turn": self._current.model_dump(mode="json") if self._current else None,
            "referents": {
                name: ref.model_dump(mode="json") for name, ref in self._referents.items()
            },
            "history": [record.model_dump(mode="json") for record in self._history],
            "background_tasks": [task.model_dump(mode="json") for task in self._background],
        }

    def render_model_context(self) -> str:
        """Render the structured context as a compact prompt block for the model."""
        lines: list[str] = []
        if self._history:
            lines.append("Recent conversation:")
            for record in self._history:
                suffix = " [interrupted]" if record.interrupted else ""
                lines.append(f"  - user: {record.user_text}{suffix}")
                if record.assistant_text:
                    lines.append(f"  - assistant: {record.assistant_text}")
        if self._referents:
            lines.append("Known referents:")
            lines.extend(f"  - {ref.describe()}" for ref in self._referents.values())
        if self._background:
            lines.append("Active background tasks:")
            lines.extend(
                f"  - {task.goal} [{task.state.value}] "
                f"(step {task.current_step_index}/{task.max_steps})"
                for task in self._background
            )
        return "\n".join(lines)

    def reset(self) -> None:
        """Drop history, referents, and background snapshots."""
        self._history.clear()
        self._referents.clear()
        self._background = ()
        self._current = None
        self._turn_counter = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _turn_id(context: TurnContext | None) -> str:
        return context.turn_id if context is not None else ""

    def _close(
        self,
        context: TurnContext | None,
        assistant_text: str,
        response_action: ResponseAction | None,
        tool_names: tuple[str, ...],
        status: TurnStatus,
    ) -> TurnRecord:
        ctx = context if context is not None else self._current
        record = TurnRecord(
            turn_id=ctx.turn_id if ctx is not None else "turn_unknown",
            turn_index=ctx.turn_index if ctx is not None else 0,
            user_text=ctx.user_text if ctx is not None else "",
            assistant_text=assistant_text,
            response_action=response_action,
            status=status,
            tool_names=tuple(tool_names),
            referents=dict(self._referents),
            started_at=ctx.started_at if ctx is not None else datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        self._history.append(record)
        self._current = None
        return record


def contains_anaphoric_reference(text: str) -> bool:
    """True when ``text`` contains an object back-reference marker.

    Reported as a fact only. Whether the marker is *resolvable* is decided by
    the model from the structured referents, not by this heuristic. Shared with
    ``ResponsePolicy`` so both use identical matching.
    """
    return bool(ANAPHORA_PATTERN.search(text or ""))


def _task_snapshot(task: Task) -> BackgroundTaskSnapshot:
    return BackgroundTaskSnapshot(
        task_id=task.task_id,
        goal=task.goal[:2000],
        state=task.state,
        current_step_index=min(task.current_step_index, 1000),
        max_steps=max(task.max_steps, 1),
        metadata=dict(task.metadata),
    )


__all__ = [
    "ANAPHORA_PATTERN",
    "ConversationTurnManager",
    "contains_anaphoric_reference",
]
