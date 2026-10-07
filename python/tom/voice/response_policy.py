"""Deterministic response / acknowledgment policy for conversational turns.

Phase 9 Iteration 9.4.

Invariants (Phase 9 Architectural Principle 5, plan §9 Iteration 9.4):
- **Deterministic**: same input tuple always yields the same ``ResponseAction``.
- **Local and side-effect free**: pure string/table evaluation. No network, no
  LLM, no tool execution, no filesystem, no audio.
- **Lock-free**: read-only lookups over immutable frozensets plus plain dicts of
  observed tool timings; typical evaluation is single-digit microseconds against
  the < 5 ms budget.
- Latency heuristics use *operation type, tool metadata, and observed historical
  timing* — never a hard-coded exact duration for a specific tool.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from tom.schemas.turn import ResponseAction, ResponseDecision, TurnContext
from tom.voice.turn_manager import contains_anaphoric_reference

# ---------------------------------------------------------------------------
# Lexical tables — data, not logic, so policy behaviour stays auditable
# ---------------------------------------------------------------------------

# Silence directives: purely administrative / "just do it" instructions.
_SILENCE_MARKERS: tuple[str, ...] = (
    "silently",
    "quietly",
    "without speaking",
    "without saying",
    "without announcing",
    "without announcing anything",
    "don't say",
    "do not say",
    "don't speak",
    "do not speak",
    "don't announce",
    "do not announce",
    "no need to say",
    "don't explain",
    "just do it",
    "just do that",
    "shut up",
    "stay quiet",
)

# Progress / status check-ins.
_PROGRESS_MARKERS: tuple[str, ...] = (
    "progress",
    "status",
    "how's it going",
    "hows it going",
    "how is it going",
    "any update",
    "any news",
    "any movement",
    "still running",
    "how far along",
    "where are you",
    "check on it",
    "update me",
)

# Ambiguity is not always a lexical signal; these phrases always are.
_AMBIGUITY_MARKERS: tuple[str, ...] = (
    "which one",
    "which of them",
    "what do you mean",
    "not sure",
    "be more specific",
)

# Verbs whose target must be unambiguous before acting destructively.
_DESTRUCTIVE_VERBS: tuple[str, ...] = (
    "delete",
    "remove",
    "erase",
    "wipe",
    "format",
    "drop",
    "purge",
    "destroy",
    "truncate",
    "reset",
    "overwrite",
)

# Utterances describing latency-bearing work, for pre-routing acknowledgments
# (tool metadata is not always known yet when the first sentence is generated).
_LATENT_HEAVY_HINTS: tuple[str, ...] = (
    "search",
    "grep",
    "scan",
    "look through",
    "go through",
    "sift through",
    "every file",
    "all files",
    "across the repo",
    "across the codebase",
    "whole repository",
    "entire repository",
    "whole codebase",
    "entire codebase",
    "list all",
    "find all",
    "compare",
    "audit",
    "run the tests",
    "run tests",
    "run the test suite",
    "test suite",
    "run tests in the",
    "build the",
    "refactor",
    "in detail",
    "summarize the entire",
    "investigate",
    "debug",
)

# Tool metadata: statically latency-bearing operations (external data reads).
_LATENCY_BEARING_TOOLS: frozenset[str] = frozenset(
    {
        "files.list_directory",
        "files.read_file",
        "files.get_metadata",
        "memory.search",
        "memory.recall",
        "system.list_processes",
        "system.get_snapshot",
        "vision.capture",
        "vision.ocr",
        "vision.find_element",
        "vision.ask",
    }
)

# Tool categories whose operations touch external, unbounded data sources.
_LATENCY_BEARING_CATEGORIES: frozenset[str] = frozenset({"files", "vision"})

# Deterministic acknowledgment text, selected by operation category.
_ACK_TEXTS: tuple[tuple[str, str], ...] = (
    ("test", "Running that now..."),
    ("build", "Running that now..."),
    ("vision", "Taking a look at the screen..."),
    ("screen", "Taking a look at the screen..."),
    ("search", "Looking into that..."),
    ("grep", "Looking into that..."),
    ("scan", "Looking through those files..."),
    ("file", "Checking those files..."),
    ("files", "Checking those files..."),
    ("directory", "Checking that folder..."),
    ("memory", "Looking through what I remember..."),
)

_DEFAULT_ACK_TEXT = "On it..."

# Path-like, quoted, or versioned token => a concrete target was supplied.
_CONCRETE_TARGET_RE = re.compile(
    r"""(
          [\w.\\/-]*[\\/][\w.\\/-]*          # path separator
        | \b\w[\w-]*\.[A-Za-z0-9]{1,5}\b     # file extension / version 2.0
        | ["'“][^"'”]{1,}["'”]        # quoted literal
        | \b\w+\s+\d+(?:\.\d+)*\b       # "file 2", "phase 8"
    )""",
    re.VERBOSE,
)

# Latency hints beyond this count in a single turn indicate multi-step work.
_MULTI_STEP_TOOL_THRESHOLD = 2


class ResponsePolicy:
    """Evaluates the deterministic response action for a single turn.

    Args:
        slow_tool_threshold_ms: Observed mean latency above which a tool counts
            as latency-bearing. Used only together with *recorded* timings, so
            the policy never assumes a specific tool's duration.
        ack_texts: Optional override of the acknowledgment phrase table.
    """

    def __init__(
        self,
        *,
        slow_tool_threshold_ms: float = 1200.0,
        ack_texts: Mapping[str, str] | None = None,
    ) -> None:
        if slow_tool_threshold_ms <= 0:
            raise ValueError("slow_tool_threshold_ms must be > 0")
        self._slow_tool_threshold_ms = slow_tool_threshold_ms
        self._ack_texts = dict(ack_texts) if ack_texts else None
        self._tool_timings: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Historical timing (fed by the caller, never fetched by the policy)
    # ------------------------------------------------------------------

    def record_tool_timing(self, tool_name: str, duration_ms: float) -> None:
        """Record one observed tool duration for latency classification."""
        if duration_ms < 0:
            return
        samples = self._tool_timings.setdefault(tool_name, [])
        samples.append(duration_ms)
        if len(samples) > 16:
            del samples[:-16]

    def observed_latency_ms(self, tool_name: str) -> float | None:
        """Mean observed latency for ``tool_name``, or None when unmeasured."""
        samples = self._tool_timings.get(tool_name)
        if not samples:
            return None
        return sum(samples) / len(samples)

    def is_latency_bearing(self, tool_name: str) -> bool:
        """True when an operation is likely to have meaningful latency."""
        if tool_name in _LATENCY_BEARING_TOOLS:
            return True
        observed = self.observed_latency_ms(tool_name)
        if observed is not None and observed >= self._slow_tool_threshold_ms:
            return True
        category = tool_name.rsplit(".", 1)[0] if "." in tool_name else tool_name
        return category in _LATENCY_BEARING_CATEGORIES

    # ------------------------------------------------------------------
    # Policy evaluation
    # ------------------------------------------------------------------

    def evaluate(self, user_intent: str, turn_context: TurnContext) -> ResponseAction:
        """Return the deterministic response action for one turn."""
        return self.decide(user_intent, turn_context).action

    def decide(self, user_intent: str, turn_context: TurnContext) -> ResponseDecision:
        """Evaluate the full decision (action + acknowledgment + rule identifier).

        Rules are evaluated in a fixed precedence order so the result is stable:

        1. ``empty_utterance``          -> SILENT_ACT
        2. ``silence_directive``        -> SILENT_ACT
        3. ``progress_query``           -> REPORT_PROGRESS (needs running work)
        4. ``ambiguous_referent``       -> CLARIFY
        5. ``ambiguous_destructive``    -> CLARIFY
        6. ``latency_bearing_operation``-> FAST_ACK_THEN_WORK
        7. default                      -> SPEAK_DIRECT
        """
        text = (user_intent or "").strip()
        lowered = text.lower()

        if not text:
            return _decide(ResponseAction.SILENT_ACT, reason="empty_utterance")

        if _matches_any(lowered, _SILENCE_MARKERS):
            return _decide(ResponseAction.SILENT_ACT, reason="silence_directive")

        if _matches_any(lowered, _PROGRESS_MARKERS) and (
            turn_context.has_running_task or turn_context.background_tasks
        ):
            return _decide(ResponseAction.REPORT_PROGRESS, reason="progress_query")

        if _matches_any(lowered, _AMBIGUITY_MARKERS):
            return _decide(ResponseAction.CLARIFY, reason="ambiguous_utterance")

        if _is_unresolved_referent(lowered, turn_context):
            return _decide(ResponseAction.CLARIFY, reason="ambiguous_referent")

        if _is_ambiguous_destructive(lowered, turn_context):
            return _decide(ResponseAction.CLARIFY, reason="ambiguous_destructive")

        slow_tools = tuple(
            tool for tool in turn_context.planned_tools if self.is_latency_bearing(tool)
        )
        if slow_tools:
            return _decide(
                ResponseAction.FAST_ACK_THEN_WORK,
                ack_text=self.ack_text_for(user_intent),
                reason="latency_bearing_tool",
                tools=slow_tools,
            )

        if len(turn_context.planned_tools) >= _MULTI_STEP_TOOL_THRESHOLD:
            return _decide(
                ResponseAction.FAST_ACK_THEN_WORK,
                ack_text=self.ack_text_for(user_intent),
                reason="multi_tool_operation",
                tools=tuple(turn_context.planned_tools),
            )

        declared_ms = float(turn_context.metadata.get("estimated_latency_ms", 0.0) or 0.0)
        if declared_ms >= self._slow_tool_threshold_ms:
            return _decide(
                ResponseAction.FAST_ACK_THEN_WORK,
                ack_text=self.ack_text_for(user_intent),
                reason="declared_latency",
            )

        if _matches_any(lowered, _LATENT_HEAVY_HINTS):
            return _decide(
                ResponseAction.FAST_ACK_THEN_WORK,
                ack_text=self.ack_text_for(user_intent),
                reason="latency_bearing_intent",
            )

        return _decide(ResponseAction.SPEAK_DIRECT, reason="default")

    def ack_text_for(self, user_intent: str) -> str:
        """Deterministic acknowledgment phrase for a latency-bearing request."""
        lowered = (user_intent or "").lower()
        if self._ack_texts:
            for keyword, text in self._ack_texts.items():
                if keyword in lowered:
                    return text
        for keyword, text in _ACK_TEXTS:
            if keyword in lowered:
                return text
        return _DEFAULT_ACK_TEXT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decide(
    action: ResponseAction,
    *,
    reason: str,
    ack_text: str | None = None,
    tools: tuple[str, ...] = (),
) -> ResponseDecision:
    return ResponseDecision(
        action=action,
        ack_text=ack_text,
        reason=reason,
        latency_bearing_tools=tools,
    )


def _matches_any(lowered: str, markers: tuple[str, ...]) -> bool:
    return any(marker in lowered for marker in markers)


def _is_unresolved_referent(lowered: str, turn_context: TurnContext) -> bool:
    """A back-reference with nothing to bind to must be clarified, not guessed."""
    if not contains_anaphoric_reference(lowered):
        return False
    return not turn_context.referents and not turn_context.history


def _is_ambiguous_destructive(lowered: str, turn_context: TurnContext) -> bool:
    """Destructive action without a concrete target (and nothing to inherit)."""
    if not _matches_any(lowered, _DESTRUCTIVE_VERBS):
        return False
    if _CONCRETE_TARGET_RE.search(_target_remainder(lowered)):
        return False
    return not turn_context.referents


def _target_remainder(lowered: str) -> str:
    """Strip the leading destructive verb so target detection looks at the object."""
    for verb in _DESTRUCTIVE_VERBS:
        if lowered.startswith(verb):
            return lowered[len(verb) :].strip()
        marker = f" {verb} "
        if marker in lowered:
            return lowered.split(marker, 1)[1].strip()
    return lowered


__all__ = [
    "ResponsePolicy",
]
