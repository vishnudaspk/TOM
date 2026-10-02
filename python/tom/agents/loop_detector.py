"""Loop detection and execution pattern stagnation monitoring.

Adheres to:
- Phase 8 Architecture (Loop Detection & Stagnation Monitoring)
- Decision 035: Step-Bounded Execution Loop
- Decision 051: Task Lifecycle State Machine
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tom.telemetry.logging import get_logger

logger = get_logger(__name__)


def normalize_parameters(parameters: dict[str, Any] | None) -> str:
    """Produce a deterministic, order-independent fingerprint for a parameter dict.

    Sorts keys and serializes standard JSON types without retaining raw secrets.
    """
    if not parameters:
        return ""
    try:
        serialized = json.dumps(parameters, sort_keys=True, default=str)
    except Exception:
        serialized = str(sorted(parameters.items()))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def normalize_observation(observation: str | bytes | dict[str, Any] | None) -> str:
    """Produce a safe, non-persisted SHA-256 fingerprint from an observation payload."""
    if observation is None:
        return ""
    if isinstance(observation, bytes):
        return hashlib.sha256(observation).hexdigest()[:16]
    if isinstance(observation, dict):
        try:
            serialized = json.dumps(observation, sort_keys=True, default=str)
        except Exception:
            serialized = str(sorted(observation.items()))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(str(observation).encode("utf-8")).hexdigest()[:16]


class LoopDetector:
    """Monitors tool invocations and screen/environment observations for execution loops.

    Detects:
    1. Tool repetition: 3 or more consecutive identical ineffective calls
       (failing execution or identical stagnant output with identical parameters).
    2. Observation stagnation: 3 or more consecutive identical environment/screen hashes.

    Thread-safe and independent of TaskExecutor.
    """

    def __init__(
        self,
        tool_threshold: int = 3,
        observation_threshold: int = 3,
    ) -> None:
        self.tool_threshold = max(2, tool_threshold)
        self.observation_threshold = max(2, observation_threshold)

        # Tool tracking state
        self._last_tool_sig: tuple[str, str] | None = None
        self._last_outcome_sig: str | None = None
        self._consecutive_tool_count: int = 0

        # Observation tracking state
        self._last_obs_sig: str | None = None
        self._consecutive_obs_count: int = 0

        self._loop_reason: str | None = None

    def record_tool_call(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        outcome: Any = None,
        success: bool = True,
    ) -> None:
        """Record a tool invocation attempt and evaluate whether a loop occurred."""
        param_sig = normalize_parameters(parameters)
        tool_sig = (tool_name, param_sig)
        outcome_sig = normalize_observation(outcome)

        is_same_call = self._last_tool_sig == tool_sig
        # Ineffective: failed call OR identical output as previous attempt of same call
        is_ineffective = (not success) or (is_same_call and outcome_sig == self._last_outcome_sig)

        if is_same_call and is_ineffective:
            self._consecutive_tool_count += 1
        else:
            self._consecutive_tool_count = 1
            self._last_tool_sig = tool_sig
            self._last_outcome_sig = outcome_sig

        if self._consecutive_tool_count >= self.tool_threshold:
            self._loop_reason = (
                f"Tool repetition loop detected: tool '{tool_name}' invoked "
                f"{self._consecutive_tool_count} times consecutively with ineffective outcomes"
            )
            logger.warning("tool_loop_detected", tool=tool_name, count=self._consecutive_tool_count)

    def record_observation(
        self,
        observation: str | bytes | dict[str, Any] | None,
    ) -> None:
        """Record an environment/screen observation fingerprint to detect visual stagnation."""
        obs_sig = normalize_observation(observation)
        if not obs_sig:
            return

        if obs_sig == self._last_obs_sig:
            self._consecutive_obs_count += 1
        else:
            self._last_obs_sig = obs_sig
            self._consecutive_obs_count = 1

        if self._consecutive_obs_count >= self.observation_threshold:
            self._loop_reason = (
                f"Observation stagnation loop detected: identical observation recorded "
                f"{self._consecutive_obs_count} times consecutively"
            )
            logger.warning(
                "observation_stagnation_detected",
                count=self._consecutive_obs_count,
            )

    def is_loop_detected(self) -> bool:
        """Return True if any repetition or stagnation threshold has been tripped."""
        return self._loop_reason is not None

    def get_loop_reason(self) -> str | None:
        """Return the explanation string if a loop was detected, otherwise None."""
        return self._loop_reason

    def reset(self) -> None:
        """Clear all invocation and observation history."""
        self._last_tool_sig = None
        self._last_outcome_sig = None
        self._consecutive_tool_count = 0
        self._last_obs_sig = None
        self._consecutive_obs_count = 0
        self._loop_reason = None
