"""Unit tests for Phase 9 Iteration 9.2: AgentOrchestrator.stream()."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.context import CancellationToken
from tom.models.providers.mock import MockModelProvider
from tom.models.schemas import ModelResponse, ToolCallRequest
from tom.schemas.agent import AgentState, Role
from tom.schemas.router import IntentDomain, RoutingDecision
from tom.tools.registry import ToolResult


class FakeRouter:
    def __init__(self, responses: list[RoutingDecision] | None = None) -> None:
        self._responses: list[RoutingDecision] = responses or []
        self._call_count = 0

    async def classify(self, user_input: str) -> RoutingDecision:
        del user_input
        if self._call_count >= len(self._responses):
            raise IndexError("FakeRouter: no more responses")
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


def _tier2() -> RoutingDecision:
    return RoutingDecision(
        domain=IntentDomain.REASONING,
        confidence=0.5,
        target_tool=None,
        route_tier=2,
        latency_ms=1.0,
    )


def make_orchestrator(
    model_responses: list[str | ModelResponse] | None = None,
    route: RoutingDecision | None = None,
) -> tuple[AgentOrchestrator, Agent]:
    model_provider = MockModelProvider(model_responses) if model_responses else None
    deps = AgentDependencies(model_provider=model_provider) if model_provider else None
    agent = Agent(dependencies=deps)
    router = FakeRouter([route or _tier2()])
    return AgentOrchestrator(agent=agent, router=router), agent


async def collect_stream(
    orchestrator: AgentOrchestrator,
    prompt: str,
    token: CancellationToken | None = None,
) -> list[str]:
    return [delta async for delta in orchestrator.stream(prompt, token)]


class TestStreamDeltas:
    def test_yields_incremental_mock_deltas(self) -> None:
        orchestrator, _ = make_orchestrator(model_responses=["Hello world today"])

        deltas = asyncio.run(collect_stream(orchestrator, "hi"))

        assert deltas == ["Hello ", "world ", "today"]

    def test_run_behavior_unchanged(self) -> None:
        orchestrator, agent = make_orchestrator(model_responses=["I understand"])

        result = asyncio.run(orchestrator.run("explain"))

        assert result == "I understand"
        assert agent.state == AgentState.IDLE
        assert any(m.role == Role.ASSISTANT and m.content == "I understand" for m in agent.history)


class TestStreamCancellation:
    def test_cancel_stops_cooperatively(self) -> None:
        token = CancellationToken()
        provider = MockModelProvider(
            ["alpha bravo charlie delta echo"],
            latency_seconds=0.15,
        )
        agent = Agent(dependencies=AgentDependencies(model_provider=provider))
        orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))

        async def consume() -> list[str]:
            got: list[str] = []
            async for delta in orchestrator.stream("go", token):
                got.append(delta)
                token.cancel()
            return got

        got = asyncio.run(consume())
        assert 0 < len(got) < 5
        assert agent.state == AgentState.TERMINATED


class TestStreamToolPause:
    def test_yields_text_then_existing_tool_path_then_continues(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[
                ModelResponse(
                    content="Let me check.",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="system.cpu_info",
                            arguments={},
                        )
                    ],
                    finish_reason="tool_call",
                ),
                "CPU usage normal",
            ]
        )

        order: list[str] = []
        original = agent.execute_tool

        async def tracked(*args: Any, **kwargs: Any) -> ToolResult:
            order.append(f"tool:{kwargs.get('tool_name') or args[0]}")
            return await original(*args, **kwargs)

        async def run() -> list[str]:
            with patch.object(agent, "execute_tool", tracked):
                deltas: list[str] = []
                async for delta in orchestrator.stream("check system"):
                    order.append(f"delta:{delta}")
                    deltas.append(delta)
                return deltas

        deltas = asyncio.run(run())

        tool_idx = order.index("tool:system.cpu_info")
        first_delta_idx = next(i for i, x in enumerate(order) if x.startswith("delta:"))
        assert first_delta_idx < tool_idx
        assert any(i > tool_idx and x.startswith("delta:") for i, x in enumerate(order))
        assert "".join(deltas).startswith("Let me check.")
        assert "CPU usage normal" in "".join(deltas)
        assert any(m.role == Role.TOOL_RESULT for m in agent.history)
        assert agent.state == AgentState.IDLE


class TestMaxResponseTokens:
    def test_custom_max_response_tokens_passed_to_model_request(self) -> None:
        provider = MockModelProvider(["Short answer"])
        agent = Agent(dependencies=AgentDependencies(model_provider=provider))
        orchestrator = AgentOrchestrator(
            agent=agent,
            router=FakeRouter([_tier2()]),
            max_response_tokens=200,
        )

        deltas = asyncio.run(collect_stream(orchestrator, "tell me a fact"))
        assert len(deltas) > 0
        assert provider.last_request is not None
        assert provider.last_request.max_tokens == 200

    def test_voice_prompt_limits_ordinary_replies_to_two_short_sentences(self) -> None:
        from tom.agents.prompts import VOICE_SYSTEM_PROMPT

        assert "at most two short sentences" in VOICE_SYSTEM_PROMPT
        assert "unless the user explicitly asks for detail" in VOICE_SYSTEM_PROMPT


class TestConversationIsolation:
    def test_failed_generation_does_not_commit_an_unanswered_user_turn(self) -> None:
        from tom.models.base import ModelResponseError

        orchestrator, agent = make_orchestrator(model_responses=[ModelResponseError("offline")])

        result = asyncio.run(orchestrator.run("question that cannot be answered"))

        assert "Model error" in result
        assert not any(
            message.role == Role.USER and message.content == "question that cannot be answered"
            for message in agent.history
        )

    def test_failed_stream_does_not_commit_partial_conversation_turn(self) -> None:
        from tom.models.base import ModelResponseError

        orchestrator, agent = make_orchestrator(model_responses=[ModelResponseError("offline")])

        deltas = asyncio.run(collect_stream(orchestrator, "streamed question"))

        assert "Model error" in "".join(deltas)
        assert not any(
            message.role == Role.USER and message.content == "streamed question"
            for message in agent.history
        )

    def test_concurrent_requests_do_not_share_uncommitted_turns(self) -> None:
        class GatedProvider(MockModelProvider):
            def __init__(self) -> None:
                super().__init__(["first answer", "second answer"])
                self.requests = []
                self.first_started = asyncio.Event()
                self.release_first = asyncio.Event()

            async def generate(self, request: Any) -> ModelResponse:
                self.requests.append(request)
                if len(self.requests) == 1:
                    self.first_started.set()
                    await self.release_first.wait()
                return await super().generate(request)

        async def exercise() -> tuple[list[str], GatedProvider]:
            provider = GatedProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            router = FakeRouter([_tier2(), _tier2()])
            orchestrator = AgentOrchestrator(agent=agent, router=router)
            first = asyncio.create_task(orchestrator.run("first prompt"))
            await provider.first_started.wait()
            second = asyncio.create_task(orchestrator.run("second prompt"))
            await asyncio.sleep(0)
            provider.release_first.set()
            return await asyncio.gather(first, second), provider

        results, provider = asyncio.run(exercise())

        assert results == ["first answer", "second answer"]
        assert [message.content for message in provider.requests[0].messages if message.role == Role.USER] == [
            "first prompt"
        ]
        assert [message.content for message in provider.requests[1].messages if message.role == Role.USER] == [
            "first prompt",
            "second prompt",
        ]
        first_turn = next(
            message.turn_id
            for message in provider.requests[0].messages
            if message.role == Role.USER and message.content == "first prompt"
        )
        second_turn = [
            message for message in provider.requests[1].messages if message.role == Role.USER
        ][-1]
        assert second_turn.content == "second prompt"
        assert second_turn.turn_id != first_turn

    def test_concurrent_orchestrators_sharing_an_agent_do_not_interleave_history(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, ModelResponse, StreamChunk

        class GatedProvider(LLMProvider):
            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []
                self.first_started = asyncio.Event()
                self.release_first = asyncio.Event()

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                self.requests.append(request.model_copy(deep=True))
                latest_user = [
                    message for message in request.messages if message.role == Role.USER
                ][-1]
                if latest_user.content == "first prompt":
                    self.first_started.set()
                    await self.release_first.wait()
                return ModelResponse(content=f"answer to {latest_user.content}")

            async def stream(self, request: ModelRequest):
                self.requests.append(request.model_copy(deep=True))
                latest_user = [
                    message for message in request.messages if message.role == Role.USER
                ][-1]
                yield StreamChunk(content=f"answer to {latest_user.content}")

        async def exercise() -> tuple[list[str], GatedProvider]:
            provider = GatedProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            first_orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))
            second_orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))

            first = asyncio.create_task(first_orchestrator.run("first prompt"))
            await provider.first_started.wait()

            second_started = asyncio.Event()

            async def run_second() -> str:
                second_started.set()
                return "".join(
                    [delta async for delta in second_orchestrator.stream("second prompt")]
                )

            second = asyncio.create_task(run_second())
            await second_started.wait()
            provider.release_first.set()
            return await asyncio.gather(first, second), provider

        responses, provider = asyncio.run(exercise())

        assert responses == ["answer to first prompt", "answer to second prompt"]
        second_request = next(
            request
            for request in provider.requests
            if [
                message.content
                for message in request.messages
                if message.role == Role.USER
            ][-1]
            == "second prompt"
        )
        assert [
            message.content for message in second_request.messages if message.role == Role.USER
        ] == ["first prompt", "second prompt"]
        assert any(
            message.role == Role.ASSISTANT
            and message.content == "answer to first prompt"
            for message in second_request.messages
        )

    def test_shared_agent_lock_is_released_after_generation_error(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, ModelResponse, StreamChunk

        class FailOnceProvider(LLMProvider):
            def __init__(self) -> None:
                self.calls = 0

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("synthetic generation failure")
                return ModelResponse(content="fresh response")

            async def stream(self, request: ModelRequest):
                yield StreamChunk(content="unused")

        async def exercise() -> tuple[str, str, Agent]:
            provider = FailOnceProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            first = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))
            second = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))
            failed = await first.run("first attempt")
            assert not agent._conversation_lock.locked()
            recovered = await second.run("retry after failure")
            return failed, recovered, agent

        failed, recovered, agent = asyncio.run(exercise())

        assert "Model error: synthetic generation failure" == failed
        assert recovered == "fresh response"
        assert not agent._conversation_lock.locked()

    def test_shared_agent_lock_is_released_after_stream_cancellation_cleanup(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, StreamChunk

        class CancellableProvider(LLMProvider):
            def __init__(self) -> None:
                self.stream_started = asyncio.Event()
                self.stream_closed = asyncio.Event()

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest):
                raise AssertionError("This test exercises the streaming path")

            async def stream(self, request: ModelRequest):
                try:
                    self.stream_started.set()
                    yield StreamChunk(content="partial response")
                    await asyncio.Event().wait()
                finally:
                    self.stream_closed.set()

        async def exercise() -> tuple[Agent, CancellableProvider]:
            provider = CancellableProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))

            async def consume() -> None:
                async for _ in orchestrator.stream("cancel this turn"):
                    pass

            consumer = asyncio.create_task(consume())
            await provider.stream_started.wait()
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("The consumer task should propagate cancellation")

            assert provider.stream_closed.is_set()
            assert not agent._conversation_lock.locked()
            return agent, provider

        agent, _ = asyncio.run(exercise())

        assert agent.state == AgentState.TERMINATED
        assert not agent._conversation_lock.locked()

    def test_twelve_streamed_turns_keep_responses_with_their_originating_turns(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, StreamChunk

        class RequestAwareProvider(LLMProvider):
            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                raise AssertionError("This test exercises the streaming path")

            async def stream(self, request: ModelRequest):
                captured = request.model_copy(deep=True)
                self.requests.append(captured)
                user_messages = [message for message in captured.messages if message.role == Role.USER]
                current = user_messages[-1]
                if current.content == "What was my first question?":
                    response = f"Your first question was: {user_messages[0].content}"
                else:
                    response = f"reply-{len(self.requests)}-{current.turn_id}: {current.content}"
                midpoint = len(response) // 2
                yield StreamChunk(content=response[:midpoint])
                yield StreamChunk(content=response[midpoint:])

        async def exercise() -> tuple[list[str], Agent, RequestAwareProvider]:
            provider = RequestAwareProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()] * 12))
            prompts = [f"Unique question {index}: what is item {index}?" for index in range(1, 11)]
            prompts.extend(["What was my first question?", "Are you there?"])
            responses = [
                "".join([delta async for delta in orchestrator.stream(prompt)])
                for prompt in prompts
            ]
            return responses, agent, provider

        responses, agent, provider = asyncio.run(exercise())
        prompts = [f"Unique question {index}: what is item {index}?" for index in range(1, 11)]
        prompts.extend(["What was my first question?", "Are you there?"])

        assert responses[:10] == [
            f"reply-{index}-{provider.requests[index - 1].messages[-1].turn_id}: {prompt}"
            for index, prompt in enumerate(prompts[:10], start=1)
        ]
        assert responses[10] == f"Your first question was: {prompts[0]}"
        assert responses[11].endswith("Are you there?")
        assert responses[11] not in responses[:11]

        latest_request = provider.requests[-1]
        latest_user = [message for message in latest_request.messages if message.role == Role.USER][-1]
        assert latest_user.content == "Are you there?"
        assert latest_user.turn_id is not None
        latest_assistant = [
            message
            for message in agent.history.messages
            if message.role == Role.ASSISTANT and message.turn_id == latest_user.turn_id
        ]
        assert len(latest_assistant) == 1
        assert latest_assistant[0].content == responses[-1]

        for request, prompt in zip(provider.requests, prompts, strict=True):
            request_user = [message for message in request.messages if message.role == Role.USER][-1]
            assert request_user.content == prompt
            assert request_user.turn_id is not None

        for user_message in (
            message for message in agent.history.messages if message.role == Role.USER
        ):
            assistant_message = next(
                message
                for message in agent.history.messages
                if message.role == Role.ASSISTANT and message.turn_id == user_message.turn_id
            )
            assert assistant_message.turn_id == user_message.turn_id

    def test_closed_stream_finishes_provider_cleanup_before_the_next_turn(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, StreamChunk

        class ClosingProvider(LLMProvider):
            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []
                self.first_stream_closed = asyncio.Event()

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                raise AssertionError("This test exercises the streaming path")

            async def stream(self, request: ModelRequest):
                self.requests.append(request.model_copy(deep=True))
                user = [message for message in request.messages if message.role == Role.USER][-1]
                if user.content == "abandoned question":
                    try:
                        yield StreamChunk(content="partial old response")
                        yield StreamChunk(content="must not be consumed")
                    finally:
                        self.first_stream_closed.set()
                else:
                    yield StreamChunk(content=f"fresh response to {user.content}")

        async def exercise() -> tuple[str, Agent, ClosingProvider]:
            provider = ClosingProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2(), _tier2()]))

            abandoned = orchestrator.stream("abandoned question")
            first_delta = await abandoned.__anext__()
            assert first_delta == "partial old response"
            await abandoned.aclose()
            assert provider.first_stream_closed.is_set()

            response = "".join(
                [delta async for delta in orchestrator.stream("Are you there?")]
            )
            return response, agent, provider

        response, agent, provider = asyncio.run(exercise())

        assert response == "fresh response to Are you there?"
        assert len(provider.requests) == 2
        latest_request_users = [
            message.content
            for message in provider.requests[-1].messages
            if message.role == Role.USER
        ]
        assert latest_request_users == ["Are you there?"]
        assert not any(
            message.role == Role.USER and message.content == "abandoned question"
            for message in agent.history.messages
        )
        response_message = next(
            message
            for message in agent.history.messages
            if message.role == Role.ASSISTANT and message.content == response
        )
        latest_user = [
            message for message in provider.requests[-1].messages if message.role == Role.USER
        ][-1]
        assert response_message.turn_id == latest_user.turn_id

    def test_duplicate_input_gets_a_distinct_turn_and_fresh_session_has_no_old_history(self) -> None:
        from tom.models.base import LLMProvider
        from tom.models.schemas import ModelRequest, StreamChunk

        class CapturingProvider(LLMProvider):
            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest):
                raise AssertionError("This test exercises the streaming path")

            async def stream(self, request: ModelRequest):
                self.requests.append(request.model_copy(deep=True))
                user = [message for message in request.messages if message.role == Role.USER][-1]
                yield StreamChunk(content=f"response-{len(self.requests)}-{user.turn_id}")

        async def exercise() -> tuple[list[str], Agent, CapturingProvider]:
            provider = CapturingProvider()
            agent = Agent(dependencies=AgentDependencies(model_provider=provider))
            orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()] * 2))
            responses = [
                "".join([delta async for delta in orchestrator.stream("repeat this")])
                for _ in range(2)
            ]
            return responses, agent, provider

        responses, agent, provider = asyncio.run(exercise())

        first_user, second_user = [
            [message for message in request.messages if message.role == Role.USER][-1]
            for request in provider.requests
        ]
        assert first_user.content == second_user.content == "repeat this"
        assert first_user.turn_id != second_user.turn_id
        assert responses[0] != responses[1]
        for user in (first_user, second_user):
            assistant = next(
                message
                for message in agent.history.messages
                if message.role == Role.ASSISTANT and message.turn_id == user.turn_id
            )
            assert assistant.content in responses

        fresh_provider = CapturingProvider()
        fresh_agent = Agent(dependencies=AgentDependencies(model_provider=fresh_provider))
        fresh_orchestrator = AgentOrchestrator(
            agent=fresh_agent,
            router=FakeRouter([_tier2()]),
        )
        fresh_response = asyncio.run(
            collect_stream(fresh_orchestrator, "new session question")
        )
        fresh_user_messages = [
            message
            for message in fresh_provider.requests[0].messages
            if message.role == Role.USER
        ]
        assert [message.content for message in fresh_user_messages] == ["new session question"]
        assert "repeat this" not in [message.content for message in fresh_agent.history.messages]
        assert "".join(fresh_response).startswith("response-1-")
