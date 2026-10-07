"""Monotonic latency instrumentation for the Phase 9 interaction loop.

Phase 9 Iteration 9.6.

Design constraints
------------------
* **Monotonic only.** All timing comes from :func:`time.monotonic_ns`, never
  wall-clock time, so an NTP step or a DST change cannot corrupt a measurement.
* **No coupling.** The tracer knows nothing about the LLM, logging, the audio
  device, or the pipeline. It is a passive recorder, so it can never change
  production behaviour or add latency to the hot path.
* **Deterministically testable.** The clock is injectable, so a unit test can
  drive exact timestamps and assert exact derived metrics offline with no
  sleeping, no hardware, and no model.

Stage semantics (plan section 10.2)
-----------------------------------
==============================  ==========================================
Stage                           Meaning
==============================  ==========================================
``SPEECH_END``                  VAD committed the utterance
``STT_FINAL``                   Streaming STT produced its final transcript
``STT_PARTIAL1``                First streaming STT partial (telemetry only)
``LLM_FIRST_TOKEN``             First streamed delta from the orchestrator
``SENTENCE1``                   First speakable sentence chunked
``FIRST_AUDIO``                 First audio chunk ready for playback
``PLAYBACK_STARTED``            Engine accepted the first playback buffer
``BARGE_IN_ONSET``              User speech onset detected (interrupt)
``BARGE_IN_STOP``               ``stop_audio_playback()`` returned
==============================  ==========================================

Derived metrics
---------------
* ``stt_finalization_ms``      = ``STT_FINAL``      - ``SPEECH_END``
* ``llm_ttft_ms``              = ``LLM_FIRST_TOKEN`` - ``STT_FINAL``
* ``sentence1_ms``             = ``SENTENCE1``      - ``LLM_FIRST_TOKEN``
* ``tts_first_audio_ms``       = ``FIRST_AUDIO``    - ``SENTENCE1``
* ``playback_latency_ms``      = ``PLAYBACK_STARTED`` - ``FIRST_AUDIO``
* ``perceived_response_ms``    = ``PLAYBACK_STARTED`` - ``SPEECH_END``
* ``barge_in_stop_ms``         = ``BARGE_IN_STOP``  - ``BARGE_IN_ONSET``
* ``endpointing_ms``           = ``SPEECH_END``     - turn start (the
  hangover the VAD waited before committing the utterance)

SLA table (plan section 10.2). Targets are the primary acceptance criteria;
ceilings are the hard limits. :meth:`InteractionLatencyTracer.assert_slas`
never relaxes either value.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "BARGE_IN_STOP_CEILING_MS",
    "BARGE_IN_STOP_TARGET_MS",
    "LATENCY_SLAS",
    "InteractionLatencyTracer",
    "LatencyMetric",
    "LatencyReport",
    "LatencyStage",
]


class LatencyStage(StrEnum):
    """Monotonic markers recorded once per turn (plus one barge-in pair)."""

    SPEECH_END = "speech_end"
    STT_PARTIAL1 = "stt_partial1"
    STT_FINAL = "stt_final"
    LLM_FIRST_TOKEN = "llm_first_token"
    SENTENCE1 = "sentence1"
    FIRST_AUDIO = "first_audio"
    PLAYBACK_STARTED = "playback_started"
    BARGE_IN_ONSET = "barge_in_onset"
    BARGE_IN_STOP = "barge_in_stop"


@dataclass(frozen=True)
class LatencyMetric:
    """One derived latency with its Phase 9 budget."""

    name: str
    value_ms: float | None
    target_ms: float
    ceiling_ms: float

    @property
    def measured(self) -> bool:
        """True when the metric has both endpoints recorded."""
        return self.value_ms is not None

    @property
    def passed(self) -> bool:
        """True when the measurement meets the *target* (primary criterion)."""
        return self.value_ms is not None and self.value_ms <= self.target_ms

    @property
    def within_ceiling(self) -> bool:
        """True when the measurement is under the hard ceiling."""
        return self.value_ms is not None and self.value_ms <= self.ceiling_ms

    @property
    def status(self) -> str:
        if self.value_ms is None:
            return "not_measured"
        if self.passed:
            return "pass"
        if self.within_ceiling:
            return "pass_ceiling_only"
        return "fail"

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.name,
            "value_ms": None if self.value_ms is None else round(self.value_ms, 3),
            "target_ms": self.target_ms,
            "ceiling_ms": self.ceiling_ms,
            "status": self.status,
        }


# Phase 9 latency budgets. Frozen by the plan; never widened by code or test.
LATENCY_SLAS: dict[str, tuple[float, float]] = {
    "endpointing_ms": (300.0, 600.0),
    "stt_first_partial_ms": (200.0, 350.0),
    "stt_finalization_ms": (350.0, 500.0),
    "llm_ttft_ms": (500.0, 1000.0),
    "sentence1_ms": (50.0, 100.0),
    "tts_first_audio_ms": (350.0, 500.0),
    "playback_latency_ms": (100.0, 250.0),
    "perceived_response_ms": (1500.0, 2200.0),
    "barge_in_stop_ms": (150.0, 250.0),
}

BARGE_IN_STOP_TARGET_MS = 150.0
BARGE_IN_STOP_CEILING_MS = 250.0


@dataclass(frozen=True)
class LatencyReport:
    """Immutable result of :meth:`InteractionLatencyTracer.get_report`."""

    marks_ns: dict[str, int]
    metrics: dict[str, LatencyMetric] = field(default_factory=dict)

    def metric(self, name: str) -> LatencyMetric:
        """Return one metric; unknown names yield a not-measured placeholder."""
        found = self.metrics.get(name)
        if found is not None:
            return found
        target, ceiling = LATENCY_SLAS.get(name, (0.0, 0.0))
        return LatencyMetric(name=name, value_ms=None, target_ms=target, ceiling_ms=ceiling)

    @property
    def measured(self) -> dict[str, float]:
        """``{metric: value_ms}`` for every metric that has both endpoints."""
        return {
            name: metric.value_ms
            for name, metric in self.metrics.items()
            if metric.value_ms is not None
        }

    @property
    def failures(self) -> list[LatencyMetric]:
        """Metrics that were measured and breached even the hard ceiling."""
        return [
            metric
            for metric in self.metrics.values()
            if metric.measured and not metric.within_ceiling
        ]

    @property
    def unmeasured(self) -> list[str]:
        """Metrics with no measurement (missing stage marks)."""
        return [name for name, metric in self.metrics.items() if not metric.measured]

    @property
    def all_passed(self) -> bool:
        """True when every *measured* metric meets its primary target."""
        return all(metric.passed for metric in self.metrics.values() if metric.measured)

    def as_dict(self) -> dict[str, Any]:
        return {
            "marks_ns": dict(self.marks_ns),
            "metrics": {name: metric.as_dict() for name, metric in self.metrics.items()},
            "all_passed": self.all_passed,
            "failures": [metric.name for metric in self.failures],
            "unmeasured": self.unmeasured,
        }

    def format_table(self) -> str:
        """Human-readable SLA table for test logs and hardware reports."""
        rows = [f"{'metric':<24}{'value':>12}{'target':>10}{'ceiling':>10}  status"]
        for name, metric in self.metrics.items():
            value = "n/a" if metric.value_ms is None else f"{metric.value_ms:.2f} ms"
            rows.append(
                f"{name:<24}{value:>12}{metric.target_ms:>9.0f}ms"
                f"{metric.ceiling_ms:>9.0f}ms  {metric.status}"
            )
        return "\n".join(rows)


class InteractionLatencyTracer:
    """Records monotonic stage timestamps for one turn and derives latencies.

    Usage::

        tracer = InteractionLatencyTracer()
        tracer.mark(LatencyStage.SPEECH_END)
        ...
        report = tracer.get_report()
        tracer.assert_slas()

    First mark wins: re-marking a stage is ignored so a late or duplicate
    producer cannot rewrite an earlier measurement.

    Args:
        clock: Monotonic nanosecond source. Injectable purely so unit tests can
            supply exact timestamps; production uses :func:`time.monotonic_ns`.
        turn_start_ns: Optional turn-start timestamp. When provided it also
            yields the VAD endpointing metric.
    """

    __slots__ = ("_clock", "_marks_ns", "_turn_start_ns", "_recording")

    def __init__(
        self,
        *,
        clock: Callable[[], int] | None = None,
        turn_start_ns: int | None = None,
    ) -> None:
        self._clock: Callable[[], int] = clock or time.monotonic_ns
        self._marks_ns: dict[LatencyStage, int] = {}
        self._turn_start_ns = turn_start_ns
        self._recording = False

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    @property
    def marks_ns(self) -> dict[LatencyStage, int]:
        """Copy of the raw monotonic marks taken so far."""
        return dict(self._marks_ns)

    @property
    def turn_start_ns(self) -> int | None:
        return self._turn_start_ns

    @property
    def is_recording(self) -> bool:
        return self._recording

    def mark(self, stage: LatencyStage, *, at_ns: int | None = None) -> int:
        """Record ``stage``. Returns the timestamp used.

        The first mark for a stage wins; later calls are ignored and return the
        original timestamp. ``at_ns`` exists for deterministic tests only.
        """
        existing = self._marks_ns.get(stage)
        if existing is not None:
            return existing
        value = self._clock() if at_ns is None else at_ns
        self._marks_ns[stage] = value
        if stage is LatencyStage.SPEECH_END:
            self._recording = True
        return value

    def mark_speech_end(self) -> int:
        return self.mark(LatencyStage.SPEECH_END)

    def reset(self, *, turn_start_ns: int | None = None) -> None:
        """Clear all marks so the tracer can be reused for the next turn."""
        self._marks_ns.clear()
        self._recording = False
        self._turn_start_ns = turn_start_ns

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self) -> LatencyReport:
        """Derive every metric that has both endpoints recorded."""
        values: dict[str, float | None] = {
            name: self._delta_ms(start, end) for name, (start, end) in _METRIC_STAGES.items()
        }
        values["endpointing_ms"] = self._endpointing_ms()
        metrics = {
            name: LatencyMetric(
                name=name,
                value_ms=value,
                target_ms=LATENCY_SLAS[name][0],
                ceiling_ms=LATENCY_SLAS[name][1],
            )
            for name, value in values.items()
        }
        return LatencyReport(
            marks_ns={stage.value: value for stage, value in self._marks_ns.items()},
            metrics=metrics,
        )

    def assert_slas(self) -> dict[str, bool]:
        """Return ``{metric: meets_target}`` for every measured metric.

        This method is deliberately assertion-free: callers decide whether a
        missed *target* is a failure (Phase 9 exit gate) or an accepted
        degradation (a ceiling-only pass is always reported distinctly).
        """
        report = self.get_report()
        return {name: metric.passed for name, metric in report.metrics.items()}

    def assert_within_ceilings(self) -> None:
        """Raise ``AssertionError`` when a measured metric breaches its ceiling."""
        report = self.get_report()
        if report.failures:
            detail = ", ".join(
                f"{metric.name}={metric.value_ms:.2f}ms > {metric.ceiling_ms:.0f}ms"
                for metric in report.failures
            )
            raise AssertionError(f"Latency ceiling breached: {detail}")

    def require_stages(self, *stages: LatencyStage) -> None:
        """Raise ``AssertionError`` when any stage was never marked."""
        missing = [stage.value for stage in stages if stage not in self._marks_ns]
        if missing:
            raise AssertionError(f"Latency stages not recorded: {', '.join(missing)}")

    def __iter__(self) -> Iterator[LatencyStage]:
        return iter(self._marks_ns)

    def __contains__(self, stage: object) -> bool:
        return stage in self._marks_ns

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _delta_ms(self, start: LatencyStage, end: LatencyStage) -> float | None:
        first = self._marks_ns.get(start)
        last = self._marks_ns.get(end)
        if first is None or last is None:
            return None
        return (last - first) / 1_000_000.0

    def _endpointing_ms(self) -> float | None:
        if self._turn_start_ns is None:
            return None
        end = self._marks_ns.get(LatencyStage.SPEECH_END)
        if end is None:
            return None
        return (end - self._turn_start_ns) / 1_000_000.0


# Metric name -> the two stage marks it is derived from.
_METRIC_STAGES: dict[str, tuple[LatencyStage, LatencyStage]] = {
    "stt_first_partial_ms": (LatencyStage.SPEECH_END, LatencyStage.STT_PARTIAL1),
    "stt_finalization_ms": (LatencyStage.SPEECH_END, LatencyStage.STT_FINAL),
    "llm_ttft_ms": (LatencyStage.STT_FINAL, LatencyStage.LLM_FIRST_TOKEN),
    "sentence1_ms": (LatencyStage.LLM_FIRST_TOKEN, LatencyStage.SENTENCE1),
    "tts_first_audio_ms": (LatencyStage.SENTENCE1, LatencyStage.FIRST_AUDIO),
    "playback_latency_ms": (LatencyStage.FIRST_AUDIO, LatencyStage.PLAYBACK_STARTED),
    "perceived_response_ms": (LatencyStage.SPEECH_END, LatencyStage.PLAYBACK_STARTED),
    "barge_in_stop_ms": (LatencyStage.BARGE_IN_ONSET, LatencyStage.BARGE_IN_STOP),
}
