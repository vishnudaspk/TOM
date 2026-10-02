"""Unit tests for LoopDetector (Phase 8 Iteration 1).

Adheres to:
- Phase 8 Architecture (Loop Detection & Stagnation Monitoring)
- Decision 035: Step-Bounded Execution Loop
- Decision 051: Task Lifecycle State Machine
"""

from __future__ import annotations

from tom.agents.loop_detector import (
    LoopDetector,
    normalize_observation,
    normalize_parameters,
)


class TestNormalization:
    def test_parameter_normalization_order_independent(self) -> None:
        params_a = {"alpha": 1, "beta": "two", "gamma": True}
        params_b = {"gamma": True, "alpha": 1, "beta": "two"}
        assert normalize_parameters(params_a) == normalize_parameters(params_b)

    def test_empty_parameters_produce_empty_string(self) -> None:
        assert normalize_parameters(None) == ""
        assert normalize_parameters({}) == ""

    def test_different_parameters_produce_different_fingerprints(self) -> None:
        assert normalize_parameters({"x": 1}) != normalize_parameters({"x": 2})

    def test_observation_normalization_handles_various_types(self) -> None:
        assert normalize_observation(None) == ""
        assert normalize_observation("screenshot_hash_1") != ""
        assert normalize_observation(b"binary_pixel_data") != ""
        assert normalize_observation({"window": "notepad", "elements": 3}) != ""


class TestLoopDetectorToolRepetition:
    def test_first_invocation_is_not_a_loop(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        assert not detector.is_loop_detected()
        assert detector.get_loop_reason() is None

    def test_repeated_identical_failing_calls_trigger_loop(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        for _ in range(3):
            detector.record_tool_call(
                "files.read_file",
                {"path": "missing.txt"},
                outcome={"error": "Not found"},
                success=False,
            )
        assert detector.is_loop_detected()
        reason = detector.get_loop_reason()
        assert reason is not None
        assert "Tool repetition loop detected" in reason
        assert "files.read_file" in reason

    def test_threshold_behavior(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        assert not detector.is_loop_detected()

        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        assert detector.is_loop_detected()

    def test_different_parameters_reset_consecutive_count(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        # Calling with different parameter breaks the consecutive repetition
        detector.record_tool_call("files.read_file", {"path": "b.txt"}, success=False)
        assert not detector.is_loop_detected()

    def test_different_tools_reset_consecutive_count(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        detector.record_tool_call("files.read_file", {"path": "a.txt"}, success=False)
        detector.record_tool_call("system.ping", {}, success=False)
        assert not detector.is_loop_detected()

    def test_successful_calls_with_different_outcomes_do_not_trigger_loop(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        # Progress is being made: successive calls yield different results
        detector.record_tool_call(
            "files.read_file", {"path": "log.txt"}, outcome="line 1", success=True
        )
        detector.record_tool_call(
            "files.read_file", {"path": "log.txt"}, outcome="line 2", success=True
        )
        detector.record_tool_call(
            "files.read_file", {"path": "log.txt"}, outcome="line 3", success=True
        )
        assert not detector.is_loop_detected()

    def test_repeated_successful_calls_with_identical_stagnant_outcome_triggers_loop(self) -> None:
        detector = LoopDetector(tool_threshold=3)
        # Repeating same call and getting identical outcome means agent is stuck polling/waiting without progress
        for _ in range(3):
            detector.record_tool_call(
                "os.input.get_cursor_pos",
                {},
                outcome={"x": 100, "y": 200},
                success=True,
            )
        assert detector.is_loop_detected()
        assert "Tool repetition loop detected" in (detector.get_loop_reason() or "")


class TestLoopDetectorObservationStagnation:
    def test_repeated_identical_observations_trigger_loop(self) -> None:
        detector = LoopDetector(observation_threshold=3)
        obs = {"active_window": "Calculator", "hash": "abc1234"}
        detector.record_observation(obs)
        assert not detector.is_loop_detected()
        detector.record_observation(obs)
        assert not detector.is_loop_detected()
        detector.record_observation(obs)
        assert detector.is_loop_detected()
        assert "Observation stagnation loop detected" in (detector.get_loop_reason() or "")

    def test_changing_observations_avoid_loop(self) -> None:
        detector = LoopDetector(observation_threshold=3)
        detector.record_observation("frame_1")
        detector.record_observation("frame_2")
        detector.record_observation("frame_3")
        assert not detector.is_loop_detected()

    def test_reset_clears_all_history(self) -> None:
        detector = LoopDetector(tool_threshold=2, observation_threshold=2)
        detector.record_tool_call("tool.a", {}, success=False)
        detector.record_tool_call("tool.a", {}, success=False)
        assert detector.is_loop_detected()

        detector.reset()
        assert not detector.is_loop_detected()
        assert detector.get_loop_reason() is None
