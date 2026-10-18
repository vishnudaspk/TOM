"""Unit tests for personal tool permission escalation (Phase 10 Iterations 3+4).

Validates that the PERSONAL_ACTION_ESCALATION mapping in PermissionEngine:
- permits 'retrieve' actions as SAFE (no confirmation required)
- escalates all mutations (create, update, delete, cancel) to ASK_USER
- escalates unknown/missing actions to ASK_USER (fail-safe)

All tests are offline and deterministic.
"""

from __future__ import annotations

import pytest
from tom.schemas.config import PermissionsConfig
from tom.security.permissions import (
    PERSONAL_ACTION_ESCALATION,
    PermissionEngine,
    PermissionLevel,
)


@pytest.fixture()
def engine() -> PermissionEngine:
    """Engine with confirmation required — so ASK_USER is distinguishable from SAFE."""
    return PermissionEngine(config=PermissionsConfig(require_confirmation_for_ask=True))


PERSONAL_TOOLS = ("personal.note", "personal.calendar", "personal.reminder")

MUTATION_ACTIONS = {
    "personal.note": ("create", "update", "delete"),
    "personal.calendar": ("create", "update", "delete"),
    "personal.reminder": ("create", "update", "cancel", "delete"),
}


class TestPersonalActionEscalation:
    """PERSONAL_ACTION_ESCALATION contract: only 'retrieve' is read-only."""

    def test_retrieve_is_safe_for_all_personal_tools(self, engine: PermissionEngine) -> None:
        for tool in PERSONAL_TOOLS:
            decision = engine.evaluate(tool, params={"action": "retrieve"})
            assert decision.allowed, f"{tool} retrieve must be allowed"
            assert not decision.requires_confirmation, (
                f"{tool} retrieve must not require confirmation"
            )
            assert decision.permission_level == PermissionLevel.SAFE

    @pytest.mark.parametrize("tool", PERSONAL_TOOLS)
    def test_mutations_escalate_to_ask_user(self, engine: PermissionEngine, tool: str) -> None:
        for action in MUTATION_ACTIONS[tool]:
            decision = engine.evaluate(tool, params={"action": action})
            assert decision.allowed, f"{tool}.{action} must be allowed (pending confirmation)"
            assert decision.requires_confirmation, f"{tool}.{action} must require user confirmation"
            assert decision.permission_level == PermissionLevel.ASK_USER

    @pytest.mark.parametrize("tool", PERSONAL_TOOLS)
    def test_unknown_action_escalates_fail_safe(self, engine: PermissionEngine, tool: str) -> None:
        decision = engine.evaluate(tool, params={"action": "unknown_action"})
        assert decision.requires_confirmation, (
            f"{tool} with unknown action must escalate to ASK_USER (fail-safe)"
        )

    @pytest.mark.parametrize("tool", PERSONAL_TOOLS)
    def test_missing_action_escalates_fail_safe(self, engine: PermissionEngine, tool: str) -> None:
        decision = engine.evaluate(tool, params={})
        assert decision.requires_confirmation, (
            f"{tool} with missing action must escalate to ASK_USER (fail-safe)"
        )

    @pytest.mark.parametrize("tool", PERSONAL_TOOLS)
    def test_none_params_escalates_fail_safe(self, engine: PermissionEngine, tool: str) -> None:
        decision = engine.evaluate(tool, params=None)
        assert decision.requires_confirmation, (
            f"{tool} with no params must escalate to ASK_USER (fail-safe)"
        )

    def test_escalation_table_covers_all_personal_tools(self) -> None:
        """PERSONAL_ACTION_ESCALATION must declare exactly the three personal tools."""
        assert set(PERSONAL_ACTION_ESCALATION) == set(PERSONAL_TOOLS)

    def test_only_retrieve_listed_as_read_for_all_personal_tools(self) -> None:
        for tool in PERSONAL_TOOLS:
            assert PERSONAL_ACTION_ESCALATION[tool] == frozenset({"retrieve"}), (
                f"{tool}: only 'retrieve' should be in the read-safe set"
            )
