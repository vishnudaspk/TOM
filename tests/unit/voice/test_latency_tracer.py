"""Phase 9.6 latency tracer: deterministic metrics, SLA evaluation, isolation.

Every test here runs offline with an injected clock. No sleeping, no hardware,
no model, no event loop — the numbers are exact by construction, so a failure is
always a real logic bug rather than machine noise.
"""

from __future__ import annotations

import time

import pytest
from tom.voice.latency import (
    BARGE_IN_STOP_CEILING_MS,
    BARGE_IN_STOP_TARGET_MS,
    LATENCY_SLAS,
    InteractionLatencyTracer,
    LatencyMetric,
    LatencyReport,
    LatencyStage,
)

MS = 1_000_000


class FakeClock:
    """Monotonic nanosecond clock advanced explicitly by the test."""

    def __init__(self, start_ns: int = 0) -> None:
        self.now = start_ns

    def __call__(self) -> int:
        return self.now

    def advance_ms(self, ms: float) -> int:
        self.now += int(ms * MS)
        return self.now


def full_turn_tracer(clock: FakeClock) -> InteractionLatencyTracer:
    """Tracer marked with a healthy end-to-end turn (all targets met)."""
    tracer = InteractionLatencyTracer(clock=clock, turn_start_ns=0)
    clock.advance_ms(280)  # VAD hangover just inside the 300 ms target
    tracer.mark(LatencyStage.SPEECH_END)
    clock.advance_ms(150)
    tracer.mark(LatencyStage.STT_PARTIAL1)
    clock.advance_ms(150)
    tracer.mark(LatencyStage.STT_FINAL)
    clock.advance_ms(400)  # LLM TTFT inside the 500 ms target
    tracer.mark(LatencyStage.LLM_FIRST_TOKEN)
    clock.advance_ms(30)  # sentence chunking inside the 50 ms target
    tracer.mark(LatencyStage.SENTENCE1)
    clock.advance_ms(300)  # Kokoro sentence 1 inside the 350 ms target
    tracer.mark(LatencyStage.FIRST_AUDIO)
    clock.advance_ms(40)  # engine accepted the buffer
    tracer.mark(LatencyStage.PLAYBACK_STARTED)
    return tracer


# ---------------------------------------------------------------------------
# SLA table integrity
# ---------------------------------------------------------------------------


def test_sla_table_matches_phase9_plan_budgets() -> None:
    """The frozen budgets must equal the plan values, target strictly < ceiling."""
    assert LATENCY_SLAS["endpointing_ms"] == (300.0, 600.0)
    assert LATENCY_SLAS["stt_first_partial_ms"] == (200.0, 350.0)
    assert LATENCY_SLAS["stt_finalization_ms"] == (350.0, 500.0)
    assert LATENCY_SLAS["llm_ttft_ms"] == (500.0, 1000.0)
    assert LATENCY_SLAS["sentence1_ms"] == (50.0, 100.0)
    assert LATENCY_SLAS["tts_first_audio_ms"] == (350.0, 500.0)
    assert LATENCY_SLAS["playback_latency_ms"] == (100.0, 250.0)
    assert LATENCY_SLAS["perceived_response_ms"] == (1500.0, 2200.0)
    assert LATENCY_SLAS["barge_in_stop_ms"] == (BARGE_IN_STOP_TARGET_MS, BARGE_IN_STOP_CEILING_MS)
    for name, (target, ceiling) in LATENCY_SLAS.items():
        assert 0 < target < ceiling, name


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------


def test_report_derives_every_stage_delta_exactly() -> None:
    clock = FakeClock()
    report = full_turn_tracer(clock).get_report()

    assert report.measured == {
        "endpointing_ms": pytest.approx(280.0),
        "stt_first_partial_ms": pytest.approx(150.0),
        "stt_finalization_ms": pytest.approx(300.0),
        "llm_ttft_ms": pytest.approx(400.0),
        "sentence1_ms": pytest.approx(30.0),
        "tts_first_audio_ms": pytest.approx(300.0),
        "playback_latency_ms": pytest.approx(40.0),
        "perceived_response_ms": pytest.approx(1070.0),
    }
    assert report.unmeasured == ["barge_in_stop_ms"]
    assert report.failures == []
    assert report.all_passed is True


def test_perceived_response_spans_speech_end_to_playback_start() -> None:
    """Perceived latency is anchor-to-speaker, not any single stage."""
    clock = FakeClock()
    tracer = full_turn_tracer(clock)
    report = tracer.get_report()
    speech_end = tracer.marks_ns[LatencyStage.SPEECH_END]
    playback = tracer.marks_ns[LatencyStage.PLAYBACK_STARTED]
    assert report.measured["perceived_response_ms"] == pytest.approx((playback - speech_end) / 1e6)
    stage_sum = (
        report.measured["stt_finalization_ms"]
        + report.measured["llm_ttft_ms"]
        + report.measured["sentence1_ms"]
        + report.measured["tts_first_audio_ms"]
        + report.measured["playback_latency_ms"]
    )
    assert report.measured["perceived_response_ms"] == pytest.approx(stage_sum)


def test_mark_uses_monotonic_clock_by_default() -> None:
    """The production default must be a monotonic source, never wall clock."""
    tracer = InteractionLatencyTracer()
    before = time.monotonic_ns()
    tracer.mark(LatencyStage.SPEECH_END)
    after = time.monotonic_ns()
    stamped = tracer.marks_ns[LatencyStage.SPEECH_END]
    assert before <= stamped <= after


def test_first_mark_wins_so_late_producers_cannot_rewrite_history() -> None:
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.STT_FINAL)
    clock.advance_ms(5_000)
    returned = tracer.mark(LatencyStage.STT_FINAL)
    assert returned == 0
    assert tracer.marks_ns[LatencyStage.STT_FINAL] == 0


def test_marks_can_be_marked_out_of_order_without_corrupting_metrics() -> None:
    """An out-of-order mark yields a negative delta rather than being hidden."""
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.LLM_FIRST_TOKEN)
    clock.advance_ms(10)
    tracer.mark(LatencyStage.STT_FINAL)
    report = tracer.get_report()
    assert report.measured["llm_ttft_ms"] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Missing data is never invented
# ---------------------------------------------------------------------------


def test_empty_transcript_turn_reports_stt_latency_and_nothing_further() -> None:
    """A VAD false trigger measures STT, then stops: no fabricated LLM/TTS data."""
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock, turn_start_ns=0)
    tracer.mark(LatencyStage.SPEECH_END)
    clock.advance_ms(120)
    tracer.mark(LatencyStage.STT_FINAL)

    report = tracer.get_report()
    assert report.measured["stt_finalization_ms"] == pytest.approx(120.0)
    assert set(report.unmeasured) == {
        "stt_first_partial_ms",
        "llm_ttft_ms",
        "sentence1_ms",
        "tts_first_audio_ms",
        "playback_latency_ms",
        "perceived_response_ms",
        "barge_in_stop_ms",
    }
    assert report.metrics["perceived_response_ms"].status == "not_measured"
    # A turn that never spoke has nothing to fail: no measured breach.
    assert report.failures == []
    assert report.all_passed is True


def test_unmeasured_metric_is_excluded_from_pass_verdict() -> None:
    metric = LatencyMetric(name="x", value_ms=None, target_ms=10.0, ceiling_ms=20.0)
    assert metric.measured is False
    assert metric.passed is False
    assert metric.status == "not_measured"
    report = LatencyReport(marks_ns={}, metrics={"x": metric})
    assert report.all_passed is True  # vacuously: nothing measured, nothing failed
    assert report.unmeasured == ["x"]


def test_endpointing_unmeasured_without_turn_start() -> None:
    report = InteractionLatencyTracer(clock=FakeClock()).get_report()
    assert "endpointing_ms" in report.unmeasured


def test_require_stages_names_the_missing_marks() -> None:
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.SPEECH_END)
    with pytest.raises(AssertionError, match=r"stt_final, sentence1"):
        tracer.require_stages(
            LatencyStage.SPEECH_END,
            LatencyStage.STT_FINAL,
            LatencyStage.SENTENCE1,
        )
    tracer.require_stages(LatencyStage.SPEECH_END)


# ---------------------------------------------------------------------------
# SLA verdicts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value_ms", "expected_status"),
    [
        (149.0, "pass"),
        (150.0, "pass"),
        (150.1, "pass_ceiling_only"),
        (250.0, "pass_ceiling_only"),
        (250.1, "fail"),
    ],
)
def test_barge_in_sla_is_graded_target_then_ceiling(value_ms: float, expected_status: str) -> None:
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.BARGE_IN_ONSET)
    clock.advance_ms(value_ms)
    tracer.mark(LatencyStage.BARGE_IN_STOP)

    metric = tracer.get_report().metric("barge_in_stop_ms")
    assert metric.value_ms == pytest.approx(value_ms)
    assert metric.status == expected_status


def test_assert_slas_reports_targets_not_ceilings() -> None:
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.BARGE_IN_ONSET)
    clock.advance_ms(200)  # over target, under ceiling
    tracer.mark(LatencyStage.BARGE_IN_STOP)

    verdicts = tracer.assert_slas()
    assert verdicts["barge_in_stop_ms"] is False
    # Ceiling check passes: a degraded-but-usable stop is not a hard failure.
    tracer.assert_within_ceilings()


def test_assert_within_ceilings_raises_on_hard_breach() -> None:
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock)
    tracer.mark(LatencyStage.SPEECH_END)
    clock.advance_ms(2_300)  # beyond the 2200 ms perceived ceiling
    tracer.mark(LatencyStage.PLAYBACK_STARTED)

    with pytest.raises(AssertionError, match="perceived_response_ms"):
        tracer.assert_within_ceilings()


def test_slow_llm_fails_target_but_a_faster_slow_stage_still_passes() -> None:
    """Each stage is graded independently against its own budget."""
    clock = FakeClock()
    tracer = InteractionLatencyTracer(clock=clock, turn_start_ns=0)
    tracer.mark(LatencyStage.SPEECH_END)
    clock.advance_ms(100)
    tracer.mark(LatencyStage.STT_FINAL)
    clock.advance_ms(900)  # TTFT 900 ms: over 500 target, under 1000 ceiling
    tracer.mark(LatencyStage.LLM_FIRST_TOKEN)
    clock.advance_ms(20)
    tracer.mark(LatencyStage.SENTENCE1)
    clock.advance_ms(100)
    tracer.mark(LatencyStage.FIRST_AUDIO)
    clock.advance_ms(50)
    tracer.mark(LatencyStage.PLAYBACK_STARTED)

    report = tracer.get_report()
    assert report.metric("llm_ttft_ms").status == "pass_ceiling_only"
    assert report.metric("stt_finalization_ms").status == "pass"
    assert report.metric("sentence1_ms").status == "pass"
    assert report.all_passed is False
    assert report.failures == []


# ---------------------------------------------------------------------------
# Serialization and reuse
# ---------------------------------------------------------------------------


def test_report_serializes_marks_metrics_and_verdicts() -> None:
    payload = full_turn_tracer(FakeClock()).get_report().as_dict()

    assert payload["marks_ns"]["speech_end"] == 280_000_000
    assert payload["metrics"]["llm_ttft_ms"] == {
        "metric": "llm_ttft_ms",
        "value_ms": 400.0,
        "target_ms": 500.0,
        "ceiling_ms": 1000.0,
        "status": "pass",
    }
    assert payload["all_passed"] is True
    assert payload["failures"] == []
    assert payload["unmeasured"] == ["barge_in_stop_ms"]


def test_format_table_renders_every_metric_with_status() -> None:
    table = full_turn_tracer(FakeClock()).get_report().format_table()
    assert "metric" in table
    assert "llm_ttft_ms" in table
    assert "n/a" in table  # barge-in never happened


def test_reset_clears_marks_for_the_next_turn() -> None:
    tracer = full_turn_tracer(FakeClock())
    assert tracer.get_report().measured

    tracer.reset(turn_start_ns=10_000_000)
    assert tracer.marks_ns == {}
    assert tracer.get_report().measured == {}
    assert tracer.is_recording is False


def test_tracer_supports_iteration_and_membership_checks() -> None:
    tracer = full_turn_tracer(FakeClock())
    assert LatencyStage.SPEECH_END in tracer
    assert LatencyStage.BARGE_IN_STOP not in tracer
    assert len(list(tracer)) == len(tracer.marks_ns)


def test_recording_flag_starts_on_speech_end() -> None:
    tracer = InteractionLatencyTracer(clock=FakeClock())
    assert tracer.is_recording is False
    tracer.mark(LatencyStage.SPEECH_END)
    assert tracer.is_recording is True


def test_unknown_metric_name_returns_zero_budget_placeholder() -> None:
    """Typo protection: an unknown metric is unmeasured, never a silent pass."""
    metric = LatencyReport(marks_ns={}, metrics={}).metric("typo_metric")
    assert metric.measured is False
    assert metric.target_ms == 0.0
