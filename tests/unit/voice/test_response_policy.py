"""Unit tests for Phase 9 Iteration 9.4: deterministic ResponsePolicy.

Offline and side-effect free. No LLM, network, GPU, or model weights.
"""

from __future__ import annotations

import time

from tom.schemas.turn import (
    BackgroundTaskSnapshot,
    EntityKind,
    EntityReferent,
    ResponseAction,
    TurnContext,
)
from tom.voice.response_policy import ResponsePolicy


def context(**kwargs) -> TurnContext:  # type: ignore[no-untyped-def]
    kwargs.setdefault("turn_index", 1)
    return TurnContext(turn_id="t1", user_text=kwargs.pop("user_text", "do the thing"), **kwargs)


def new_context(**kwargs) -> TurnContext:  # type: ignore[no-untyped-def]
    return context(**kwargs)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_decision_is_deterministic_across_repeated_calls() -> None:
    policy = ResponsePolicy()
    ctx = context(planned_tools=("files.list_directory",))
    first = policy.decide("Search my repository for the resource manager.", ctx)
    for _ in range(50):
        assert policy.decide("Search my repository for the resource manager.", ctx) == first


def test_decision_is_independent_of_context_instance() -> None:
    policy = ResponsePolicy()
    a = context(planned_tools=("memory.search",))
    b = context(planned_tools=("memory.search",))
    assert policy.decide("look into that", a) == policy.decide("look into that", b)


def test_evaluate_matches_decide_action() -> None:
    policy = ResponsePolicy()
    ctx = context()
    assert policy.evaluate("Hello TOM", ctx) == policy.decide("Hello TOM", ctx).action


# ---------------------------------------------------------------------------
# Rule precedence
# ---------------------------------------------------------------------------


def test_empty_utterance_is_silent() -> None:
    policy = ResponsePolicy()
    decision = policy.decide("", context())
    assert decision.action is ResponseAction.SILENT_ACT
    assert decision.reason == "empty_utterance"


def test_whitespace_utterance_is_silent() -> None:
    policy = ResponsePolicy()
    assert policy.decide("   \n ", context()).action is ResponseAction.SILENT_ACT


def test_silence_directive_is_silent_even_with_slow_work() -> None:
    policy = ResponsePolicy()
    ctx = context(planned_tools=("files.list_directory", "files.read_file"))
    for text in (
        "Minimize the current test terminal silently.",
        "Clean up the build artifacts, don't say anything.",
        "Just do it, no explanation.",
        "Archive the logs quietly.",
    ):
        decision = policy.decide(text, ctx)
        assert decision.action is ResponseAction.SILENT_ACT, text
        assert decision.reason == "silence_directive"


def test_progress_query_prefers_reporting_over_ack() -> None:
    policy = ResponsePolicy()
    ctx = context(
        background_tasks=(BackgroundTaskSnapshot(task_id="t", goal="g", state="EXECUTING"),)
    )
    decision = policy.decide("What's the progress on that?", ctx)
    assert decision.action is ResponseAction.REPORT_PROGRESS
    assert decision.reason == "progress_query"


def test_progress_query_without_work_falls_through_to_speech() -> None:
    policy = ResponsePolicy()
    decision = policy.decide("What's the progress?", context())
    assert decision.action is ResponseAction.SPEAK_DIRECT


def test_ambiguity_marker_triggers_clarify() -> None:
    policy = ResponsePolicy()
    for text in ("Which one should I use?", "I'm not sure which file you mean."):
        assert policy.decide(text, context()).action is ResponseAction.CLARIFY, text


def test_unresolved_back_reference_triggers_clarify() -> None:
    policy = ResponsePolicy()
    decision = policy.decide("Can you do that again?", context())
    assert decision.action is ResponseAction.CLARIFY
    assert decision.reason == "ambiguous_referent"


def test_resolvable_back_reference_does_not_clarify() -> None:
    policy = ResponsePolicy()
    ctx = context(
        referents={
            "last_file": EntityReferent(name="last_file", kind=EntityKind.FILE, value="a.py")
        }
    )
    assert policy.decide("How many lines was that file?", ctx).action is ResponseAction.SPEAK_DIRECT


def test_destructive_without_target_triggers_clarify() -> None:
    policy = ResponsePolicy()
    for text in ("Delete the plan.", "Remove it.", "Wipe that."):
        decision = policy.decide(text, context())
        assert decision.action is ResponseAction.CLARIFY, text
        assert decision.reason in {"ambiguous_destructive", "ambiguous_referent"}


def test_destructive_with_concrete_target_proceeds() -> None:
    policy = ResponsePolicy()
    for text in ("Delete src/legacy/old.py", "Remove the file main.py", "Drop build.log"):
        assert policy.decide(text, context()).action is ResponseAction.SPEAK_DIRECT, text


def test_destructive_with_inherited_referent_proceeds() -> None:
    policy = ResponsePolicy()
    ctx = context(
        referents={
            "last_file": EntityReferent(name="last_file", kind=EntityKind.FILE, value="a.py")
        }
    )
    assert policy.decide("Delete it.", ctx).action is ResponseAction.SPEAK_DIRECT


# ---------------------------------------------------------------------------
# Latency-bearing work -> fast acknowledgment
# ---------------------------------------------------------------------------


def test_latency_bearing_tool_metadata_triggers_fast_ack() -> None:
    policy = ResponsePolicy()
    ctx = context(planned_tools=("files.list_directory", "files.read_file"))
    decision = policy.decide("Fetch the details", ctx)
    assert decision.action is ResponseAction.FAST_ACK_THEN_WORK
    assert decision.reason == "latency_bearing_tool"
    assert set(decision.latency_bearing_tools) == {"files.list_directory", "files.read_file"}


def test_observed_timing_classifies_unknown_tool_as_slow() -> None:
    policy = ResponsePolicy(slow_tool_threshold_ms=1000.0)
    ctx = context(planned_tools=("vendor.slowcall",))
    assert policy.decide("Do the thing", ctx).action is ResponseAction.SPEAK_DIRECT

    for _ in range(3):
        policy.record_tool_timing("vendor.slowcall", 1500.0)
    decision = policy.decide("Do the thing", ctx)
    assert decision.action is ResponseAction.FAST_ACK_THEN_WORK
    assert decision.latency_bearing_tools == ("vendor.slowcall",)


def test_observed_fast_tool_does_not_trigger_ack() -> None:
    policy = ResponsePolicy(slow_tool_threshold_ms=1000.0)
    policy.record_tool_timing("vendor.quickcall", 12.0)
    assert policy.is_latency_bearing("vendor.quickcall") is False
    ctx = context(planned_tools=("vendor.quickcall",))
    assert policy.decide("Do the thing", ctx).action is ResponseAction.SPEAK_DIRECT


def test_recorded_timing_window_is_bounded() -> None:
    policy = ResponsePolicy()
    for i in range(200):
        policy.record_tool_timing("vendor.slowcall", float(i))
    assert policy.observed_latency_ms("vendor.slowcall") is not None
    assert policy.record_tool_timing("vendor.slowcall", -5.0) is None
    assert policy.observed_latency_ms("never.seen") is None


def test_declared_latency_metadata_triggers_ack() -> None:
    policy = ResponsePolicy(slow_tool_threshold_ms=1000.0)
    ctx = context(metadata={"estimated_latency_ms": 3000})
    decision = policy.decide("Handle it", ctx)
    assert decision.action is ResponseAction.FAST_ACK_THEN_WORK
    assert decision.reason == "declared_latency"


def test_multi_tool_operation_triggers_ack() -> None:
    policy = ResponsePolicy()
    ctx = context(planned_tools=("clock.time", "system.ls"))
    decision = policy.decide("Do both", ctx)
    assert decision.action is ResponseAction.FAST_ACK_THEN_WORK
    assert decision.reason == "multi_tool_operation"


def test_latency_bearing_intent_triggers_ack_without_tool_knowledge() -> None:
    policy = ResponsePolicy()
    for text in (
        "Search my repository for where the resource manager is configured.",
        "Run the test suite for the voice pipeline.",
        "Compare the two implementations.",
    ):
        decision = policy.decide(text, context())
        assert decision.action is ResponseAction.FAST_ACK_THEN_WORK, text
        assert decision.reason == "latency_bearing_intent"


def test_declared_latency_in_seconds_is_treated_as_ms() -> None:
    policy = ResponsePolicy(slow_tool_threshold_ms=1000.0)
    ctx = context(metadata={"estimated_latency_ms": "1200"})
    assert policy.decide("Handle it", ctx).action is ResponseAction.FAST_ACK_THEN_WORK


# ---------------------------------------------------------------------------
# Direct speech (the default)
# ---------------------------------------------------------------------------


def test_simple_conversation_speaks_directly() -> None:
    policy = ResponsePolicy()
    for text in ("What can you do?", "Turn on the lights.", "Thanks TOM, that's all for now."):
        decision = policy.decide(text, context())
        assert decision.action is ResponseAction.SPEAK_DIRECT, text
        assert decision.reason == "default"
        assert decision.ack_text is None


def test_thanks_is_speech_not_anaphora_clarification() -> None:
    policy = ResponsePolicy()
    assert (
        policy.decide("Thanks TOM, that's all for now.", context()).action
        is ResponseAction.SPEAK_DIRECT
    )


# ---------------------------------------------------------------------------
# Acknowledgment text determinism
# ---------------------------------------------------------------------------


def test_ack_text_selection_is_deterministic_and_domain_aware() -> None:
    policy = ResponsePolicy()
    assert policy.ack_text_for("Search the codebase") == "Looking into that..."
    assert policy.ack_text_for("Run the test suite") == "Running that now..."
    assert policy.ack_text_for("Look at the screen") == "Taking a look at the screen..."
    assert policy.ack_text_for("Read those files") == "Checking those files..."
    assert policy.ack_text_for("Do something unusual") == "On it..."


def test_custom_ack_texts_override_defaults() -> None:
    policy = ResponsePolicy(ack_texts={"widget": "Widgeting..."})
    assert policy.ack_text_for("Refactor the widget factory") == "Widgeting..."
    assert policy.ack_text_for("Search the codebase") == "Looking into that..."


def test_fast_ack_decision_carries_ack_text() -> None:
    policy = ResponsePolicy()
    ctx = context(
        planned_tools=("files.read_file",),
        referents={
            "last_file": EntityReferent(name="last_file", kind=EntityKind.FILE, value="a.py")
        },
    )
    decision = policy.decide("Read those files", ctx)
    assert decision.ack_text == "Checking those files..."


# ---------------------------------------------------------------------------
# Contract guarantees
# ---------------------------------------------------------------------------


def test_threshold_is_validated() -> None:
    for bad in (0.0, -1.0):
        try:
            ResponsePolicy(slow_tool_threshold_ms=bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} must be rejected")


def test_evaluation_is_well_under_the_five_millisecond_budget() -> None:
    policy = ResponsePolicy()
    ctx = context(planned_tools=("files.read_file", "memory.search"))
    samples = [
        "Search my repository for where the resource manager is configured.",
        "Delete the plan.",
        "What's the progress on that?",
        "Minimize the current test terminal silently.",
        "Turn on the lights.",
    ]
    for text in samples:
        policy.decide(text, ctx)  # warm caches / regex compilation

    start = time.perf_counter()
    iterations = 2000
    for i in range(iterations):
        policy.decide(samples[i % len(samples)], ctx)
    elapsed_ms = (time.perf_counter() - start) * 1000.0 / iterations

    assert elapsed_ms < 5.0, f"average decision took {elapsed_ms:.4f} ms"


def test_policy_holds_no_locks() -> None:
    import gc

    policy = ResponsePolicy()
    policy.record_tool_timing("files.read_file", 900.0)
    gc.collect()
    openers = [
        obj
        for obj in gc.get_objects()
        if isinstance(obj, type(policy))
        and any(type(v).__name__ in {"lock", "RLock", "_thread.RLock"} for v in vars(obj).values())
    ]
    assert openers == []


def test_decision_is_json_serialisable() -> None:
    import json

    policy = ResponsePolicy()
    decision = policy.decide("Search the codebase", context())
    payload = json.loads(decision.model_dump_json())
    assert payload["action"] == "fast_ack_then_work"
    assert payload["ack_text"] == "Looking into that..."


def test_new_context_helper_is_equivalent() -> None:
    assert new_context().turn_index == 1
