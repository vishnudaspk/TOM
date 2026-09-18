"""Unit tests for Agent lifecycle state machine, transitions, and callbacks.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 1)
- Decision 031: Agent State Machine Architecture & Invariants
- skills/testing/python-testing: Strict assertions, edge cases, deterministic testing.
"""

import pytest
from tom.agents.base import VALID_TRANSITIONS, Agent
from tom.schemas.agent import (
    AgentConfig,
    AgentIdentity,
    AgentState,
    InvalidStateTransitionError,
    Role,
    StateChangeEvent,
)


class TestAgentInitialization:
    """Tests for Agent creation and initial state invariants."""

    def test_default_initialization(self) -> None:
        agent = Agent()
        assert agent.state == AgentState.IDLE
        assert agent.agent_id.startswith("agent_")
        assert agent.identity.created_at is not None
        assert agent.config.name == "tom-agent"
        assert agent.config.max_steps == 10
        # Check system prompt is in history
        assert len(agent.history) == 1
        assert agent.history[0].role == Role.SYSTEM
        assert agent.history[0].content == agent.config.system_prompt

    def test_custom_config_and_identity(self) -> None:
        identity = AgentIdentity(agent_id="test_agent_001")
        config = AgentConfig(
            name="custom-tom",
            max_steps=25,
            system_prompt="Custom prompt",
            temperature=0.2,
        )
        agent = Agent(config=config, identity=identity)
        assert agent.agent_id == "test_agent_001"
        assert agent.config.name == "custom-tom"
        assert agent.config.max_steps == 25
        assert agent.state == AgentState.IDLE
        assert agent.history[0].content == "Custom prompt"

    def test_empty_system_prompt_does_not_add_system_message(self) -> None:
        config = AgentConfig(system_prompt="")
        agent = Agent(config=config)
        assert len(agent.history) == 0


class TestValidStateTransitions:
    """Verifies that every valid transition defined in Decision 031 succeeds."""

    @pytest.mark.parametrize(
        ("initial_steps", "target_state"),
        [
            # IDLE transitions
            ([], AgentState.THINKING),
            ([], AgentState.TERMINATED),
            # THINKING transitions
            ([AgentState.THINKING], AgentState.ACTING),
            ([AgentState.THINKING], AgentState.WAITING_CONFIRMATION),
            ([AgentState.THINKING], AgentState.IDLE),
            ([AgentState.THINKING], AgentState.ERROR),
            ([AgentState.THINKING], AgentState.TERMINATED),
            # ACTING transitions
            ([AgentState.THINKING, AgentState.ACTING], AgentState.THINKING),
            ([AgentState.THINKING, AgentState.ACTING], AgentState.WAITING_CONFIRMATION),
            ([AgentState.THINKING, AgentState.ACTING], AgentState.IDLE),
            ([AgentState.THINKING, AgentState.ACTING], AgentState.ERROR),
            ([AgentState.THINKING, AgentState.ACTING], AgentState.TERMINATED),
            # WAITING_CONFIRMATION transitions
            (
                [AgentState.THINKING, AgentState.WAITING_CONFIRMATION],
                AgentState.ACTING,
            ),
            ([AgentState.THINKING, AgentState.WAITING_CONFIRMATION], AgentState.IDLE),
            ([AgentState.THINKING, AgentState.WAITING_CONFIRMATION], AgentState.ERROR),
            (
                [AgentState.THINKING, AgentState.WAITING_CONFIRMATION],
                AgentState.TERMINATED,
            ),
            # ERROR transitions
            ([AgentState.THINKING, AgentState.ERROR], AgentState.IDLE),
            ([AgentState.THINKING, AgentState.ERROR], AgentState.TERMINATED),
        ],
    )
    def test_valid_transitions_succeed(
        self,
        initial_steps: list[AgentState],
        target_state: AgentState,
    ) -> None:
        agent = Agent()
        for step in initial_steps:
            agent.transition_to(step)
        agent.transition_to(target_state)
        assert agent.state == target_state


class TestInvalidStateTransitions:
    """Verifies that illegal transitions strictly raise InvalidStateTransitionError."""

    @pytest.mark.parametrize(
        ("initial_steps", "invalid_target"),
        [
            # From IDLE
            ([], AgentState.IDLE),  # Self-loop
            ([], AgentState.ACTING),
            ([], AgentState.WAITING_CONFIRMATION),
            ([], AgentState.ERROR),
            # From THINKING
            ([AgentState.THINKING], AgentState.THINKING),  # Self-loop
            # From ACTING
            ([AgentState.THINKING, AgentState.ACTING], AgentState.ACTING),  # Self-loop
            # From WAITING_CONFIRMATION
            (
                [AgentState.THINKING, AgentState.WAITING_CONFIRMATION],
                AgentState.WAITING_CONFIRMATION,
            ),  # Self-loop
            (
                [AgentState.THINKING, AgentState.WAITING_CONFIRMATION],
                AgentState.THINKING,
            ),
            # From ERROR
            ([AgentState.THINKING, AgentState.ERROR], AgentState.ERROR),  # Self-loop
            ([AgentState.THINKING, AgentState.ERROR], AgentState.THINKING),
            ([AgentState.THINKING, AgentState.ERROR], AgentState.ACTING),
            ([AgentState.THINKING, AgentState.ERROR], AgentState.WAITING_CONFIRMATION),
            # From TERMINATED (genuinely terminal - cannot transition anywhere)
            ([AgentState.TERMINATED], AgentState.IDLE),
            ([AgentState.TERMINATED], AgentState.THINKING),
            ([AgentState.TERMINATED], AgentState.ACTING),
            ([AgentState.TERMINATED], AgentState.WAITING_CONFIRMATION),
            ([AgentState.TERMINATED], AgentState.ERROR),
            ([AgentState.TERMINATED], AgentState.TERMINATED),
        ],
    )
    def test_invalid_transitions_raise(
        self,
        initial_steps: list[AgentState],
        invalid_target: AgentState,
    ) -> None:
        agent = Agent()
        for step in initial_steps:
            agent.transition_to(step)

        initial_state = agent.state
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            agent.transition_to(invalid_target)

        assert exc_info.value.current_state == initial_state
        assert exc_info.value.target_state == invalid_target
        # Verify state did not change
        assert agent.state == initial_state


class TestComprehensiveMatrixCompleteness:
    """Verifies that all possible state pairs are covered by the matrix."""

    def test_matrix_covers_all_enum_members(self) -> None:
        for state in AgentState:
            assert state in VALID_TRANSITIONS

    def test_terminated_has_empty_transitions(self) -> None:
        assert VALID_TRANSITIONS[AgentState.TERMINATED] == set()

    def test_all_state_pairs_deterministic(self) -> None:
        all_states = list(AgentState)
        for origin in all_states:
            for target in all_states:
                agent = Agent()
                # Reach origin state via known path if possible
                if origin == AgentState.IDLE:
                    pass
                elif origin == AgentState.THINKING:
                    agent.transition_to(AgentState.THINKING)
                elif origin == AgentState.ACTING:
                    agent.transition_to(AgentState.THINKING)
                    agent.transition_to(AgentState.ACTING)
                elif origin == AgentState.WAITING_CONFIRMATION:
                    agent.transition_to(AgentState.THINKING)
                    agent.transition_to(AgentState.WAITING_CONFIRMATION)
                elif origin == AgentState.ERROR:
                    agent.transition_to(AgentState.THINKING)
                    agent.transition_to(AgentState.ERROR)
                elif origin == AgentState.TERMINATED:
                    agent.transition_to(AgentState.TERMINATED)

                is_valid = target in VALID_TRANSITIONS[origin]
                if is_valid:
                    agent.transition_to(target)
                    assert agent.state == target
                else:
                    with pytest.raises(InvalidStateTransitionError):
                        agent.transition_to(target)
                    assert agent.state == origin


class TestStateChangeCallbacks:
    """Verifies callback subscription, dispatch, and error containment."""

    def test_callback_with_event(self) -> None:
        agent = Agent()
        received_events: list[StateChangeEvent] = []

        def on_change(event: StateChangeEvent) -> None:
            received_events.append(event)

        agent.add_state_callback(on_change)
        agent.transition_to(AgentState.THINKING, metadata={"reason": "user_query"})

        assert len(received_events) == 1
        evt = received_events[0]
        assert evt.agent_id == agent.agent_id
        assert evt.old_state == AgentState.IDLE
        assert evt.new_state == AgentState.THINKING
        assert evt.metadata == {"reason": "user_query"}

    def test_callback_with_two_args(self) -> None:
        agent = Agent()
        transitions: list[tuple[AgentState, AgentState]] = []

        def on_change(old_state: AgentState, new_state: AgentState) -> None:
            transitions.append((old_state, new_state))

        agent.add_state_callback(on_change)
        agent.transition_to(AgentState.THINKING)
        agent.transition_to(AgentState.ACTING)

        assert transitions == [
            (AgentState.IDLE, AgentState.THINKING),
            (AgentState.THINKING, AgentState.ACTING),
        ]

    def test_callback_removal(self) -> None:
        agent = Agent()
        calls = []

        def callback(event: StateChangeEvent) -> None:
            calls.append(event)

        agent.add_state_callback(callback)
        agent.transition_to(AgentState.THINKING)
        assert len(calls) == 1

        agent.remove_state_callback(callback)
        agent.transition_to(AgentState.ACTING)
        assert len(calls) == 1  # Not called again

    def test_failing_callback_does_not_abort_transition(self) -> None:
        agent = Agent()
        second_called = []

        def failing_callback(event: StateChangeEvent) -> None:
            raise RuntimeError("callback exploded")

        def normal_callback(event: StateChangeEvent) -> None:
            second_called.append(event)

        agent.add_state_callback(failing_callback)
        agent.add_state_callback(normal_callback)

        agent.transition_to(AgentState.THINKING)
        # Transition succeeded despite error
        assert agent.state == AgentState.THINKING
        # Subsequent callback still ran
        assert len(second_called) == 1

    def test_invalid_transition_does_not_invoke_callbacks(self) -> None:
        agent = Agent()
        calls = []

        def callback(event: StateChangeEvent) -> None:
            calls.append(event)

        agent.add_state_callback(callback)
        with pytest.raises(InvalidStateTransitionError):
            agent.transition_to(AgentState.ACTING)  # Illegal from IDLE

        assert len(calls) == 0
