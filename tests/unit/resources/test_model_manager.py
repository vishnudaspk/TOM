"""Unit tests for ModelLifecycleCoordinator.

Verifies:
- Deterministic state transitions (UNLOADED -> READY -> BUSY -> IDLE)
- Invalid state transitions raise InvalidStateTransitionError
- Registration, unregistration, and list bookkeeping
- Idle timeout evaluation using an injected fake clock
- Idle evaluation does NOT claim physical unload
- Pluggable eviction hooks with safe exception handling (no state corruption)
- Distinct tracking of TOM logical state, observed residency, and confirmed physical unload
- Concurrent acquire/release updates under asyncio
"""

from __future__ import annotations

import asyncio

import pytest
from tom.resources.manager import ModelRole
from tom.resources.model_manager import (
    InvalidStateTransitionError,
    ModelLifecycleCoordinator,
    ModelNotFoundError,
    ModelRegistration,
    ModelState,
)


class FakeClock:
    def __init__(self, start_time: float = 1000.0) -> None:
        self.current_time = start_time

    def time(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start_time=1000.0)


@pytest.fixture
def coordinator(fake_clock: FakeClock) -> ModelLifecycleCoordinator:
    return ModelLifecycleCoordinator(clock=fake_clock.time)


@pytest.fixture
def reasoning_reg() -> ModelRegistration:
    return ModelRegistration(
        model_name="qwen-7b-reasoning",
        role=ModelRole.REASONING,
        vram_required_mb=5200,
        idle_timeout_seconds=60.0,
        metadata={"quant": "q4_k_m"},
    )


@pytest.fixture
def vlm_reg() -> ModelRegistration:
    return ModelRegistration(
        model_name="internvl-2b-vlm",
        role=ModelRole.VLM,
        vram_required_mb=5500,
        idle_timeout_seconds=30.0,
    )


class TestModelRegistrationAndBasics:
    @pytest.mark.anyio
    async def test_register_and_get_state(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        await coordinator.register_model(reasoning_reg)
        state = await coordinator.get_state(reasoning_reg.model_name)
        assert state == ModelState.UNLOADED

        record = await coordinator.get_record(reasoning_reg.model_name)
        assert record.state == ModelState.UNLOADED
        assert not record.observed_residency
        assert not record.confirmed_physical_unload
        assert record.active_acquisitions == 0

    @pytest.mark.anyio
    async def test_re_registration_updates_metadata_preserving_state(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(reasoning_reg.model_name)

        # Update registration with new metadata
        updated_reg = ModelRegistration(
            model_name=reasoning_reg.model_name,
            role=reasoning_reg.role,
            vram_required_mb=5000,
            metadata={"new_key": "new_val"},
        )
        await coordinator.register_model(updated_reg)

        record = await coordinator.get_record(reasoning_reg.model_name)
        assert record.state == ModelState.READY
        assert record.registration.vram_required_mb == 5000
        assert record.registration.metadata["new_key"] == "new_val"

    @pytest.mark.anyio
    async def test_unregister_model(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        await coordinator.register_model(reasoning_reg)
        await coordinator.unregister_model(reasoning_reg.model_name)

        with pytest.raises(ModelNotFoundError):
            await coordinator.get_state(reasoning_reg.model_name)

    @pytest.mark.anyio
    async def test_cannot_unregister_busy_model(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(reasoning_reg.model_name)
        await coordinator.record_acquire(reasoning_reg.model_name)

        with pytest.raises(InvalidStateTransitionError):
            await coordinator.unregister_model(reasoning_reg.model_name)

    @pytest.mark.anyio
    async def test_list_models(
        self,
        coordinator: ModelLifecycleCoordinator,
        reasoning_reg: ModelRegistration,
        vlm_reg: ModelRegistration,
    ) -> None:
        await coordinator.register_model(reasoning_reg)
        await coordinator.register_model(vlm_reg)

        models = await coordinator.list_models()
        assert len(models) == 2
        names = {m.model_name for m in models}
        assert names == {reasoning_reg.model_name, vlm_reg.model_name}


class TestModelLifecycleTransitions:
    @pytest.mark.anyio
    async def test_normal_lifecycle_transitions(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        """UNLOADED -> READY -> BUSY -> IDLE."""
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)

        # 1. UNLOADED -> READY
        await coordinator.record_ready(name, observed_residency=True)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.READY
        assert rec.observed_residency is True

        # 2. READY -> BUSY
        await coordinator.record_acquire(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.BUSY
        assert rec.active_acquisitions == 1

        # 3. BUSY -> IDLE
        await coordinator.record_release(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert rec.active_acquisitions == 0

        # 4. IDLE -> BUSY again
        await coordinator.record_acquire(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.BUSY
        assert rec.active_acquisitions == 1

        # Release again
        await coordinator.record_release(name)
        assert (await coordinator.get_state(name)) == ModelState.IDLE

    @pytest.mark.anyio
    async def test_invalid_transitions_raise_error(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)

        # UNLOADED cannot transition directly to BUSY
        with pytest.raises(InvalidStateTransitionError):
            await coordinator.record_acquire(name)

        # UNLOADED cannot transition to IDLE
        with pytest.raises(InvalidStateTransitionError):
            await coordinator.record_release(name)

        # Transition to READY then BUSY
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)

        # BUSY cannot transition to UNLOADED directly
        with pytest.raises(InvalidStateTransitionError):
            await coordinator.record_unloaded(name)

    @pytest.mark.anyio
    async def test_nested_acquisitions_and_release(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)

        # Two acquire calls (concurrent requests using same model)
        await coordinator.record_acquire(name)
        await coordinator.record_acquire(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.BUSY
        assert rec.active_acquisitions == 2

        # First release does NOT set state to IDLE
        await coordinator.record_release(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.BUSY
        assert rec.active_acquisitions == 1

        # Second release sets state to IDLE
        await coordinator.record_release(name)
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert rec.active_acquisitions == 0

    @pytest.mark.anyio
    async def test_error_transition_and_reset(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)

        await coordinator.record_error(name, "Inference out of memory")
        assert (await coordinator.get_state(name)) == ModelState.ERROR

        # Can reset from ERROR to READY or UNLOADED
        await coordinator.record_ready(name)
        assert (await coordinator.get_state(name)) == ModelState.READY


class TestIdleEvaluationAndEviction:
    @pytest.mark.anyio
    async def test_idle_timeout_evaluation(
        self,
        coordinator: ModelLifecycleCoordinator,
        reasoning_reg: ModelRegistration,
        fake_clock: FakeClock,
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)

        # Finish inference at t=1000.0
        await coordinator.record_release(name)
        assert (await coordinator.get_state(name)) == ModelState.IDLE

        # Advance clock by 30 seconds (idle timeout is 60s)
        fake_clock.advance(30.0)
        idle = await coordinator.get_idle_models()
        assert idle == []

        # Advance by another 35 seconds (total 65s > 60s)
        fake_clock.advance(35.0)
        idle = await coordinator.get_idle_models()
        assert idle == [name]

        # CRITICAL: get_idle_models must NOT automatically change state or claim unload!
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert not rec.confirmed_physical_unload

    @pytest.mark.anyio
    async def test_explicit_eviction_hook_success(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)
        await coordinator.record_release(name)

        hook_called = False

        async def fake_eviction_hook(model: str) -> bool:
            nonlocal hook_called
            hook_called = True
            assert model == name
            return True

        result = await coordinator.trigger_eviction(name, hook=fake_eviction_hook)
        assert result is True
        assert hook_called is True

        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.UNLOADED
        assert rec.observed_residency is False
        assert rec.confirmed_physical_unload is True

    @pytest.mark.anyio
    async def test_eviction_hook_failure_does_not_corrupt_state(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)
        await coordinator.record_release(name)

        # Hook raises an unhandled exception
        async def failing_hook(model: str) -> bool:
            raise ConnectionError("Server unreachable")

        result = await coordinator.trigger_eviction(name, hook=failing_hook)
        assert result is False

        # State must remain IDLE, not corrupted!
        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert not rec.confirmed_physical_unload

    @pytest.mark.anyio
    async def test_eviction_hook_returning_false_keeps_state(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)
        await coordinator.record_release(name)

        async def rejecting_hook(model: str) -> bool:
            return False

        result = await coordinator.trigger_eviction(name, hook=rejecting_hook)
        assert result is False

        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert not rec.confirmed_physical_unload

    @pytest.mark.anyio
    async def test_cannot_evict_busy_model(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)
        await coordinator.record_acquire(name)

        with pytest.raises(InvalidStateTransitionError):
            await coordinator.trigger_eviction(name)

    @pytest.mark.anyio
    async def test_concurrent_state_updates(
        self, coordinator: ModelLifecycleCoordinator, reasoning_reg: ModelRegistration
    ) -> None:
        """Verify thread-safe concurrent acquire and release updates."""
        name = reasoning_reg.model_name
        await coordinator.register_model(reasoning_reg)
        await coordinator.record_ready(name)

        async def worker():
            for _ in range(50):
                await coordinator.record_acquire(name)
                await asyncio.sleep(0.001)
                await coordinator.record_release(name)

        await asyncio.gather(worker(), worker(), worker())

        rec = await coordinator.get_record(name)
        assert rec.state == ModelState.IDLE
        assert rec.active_acquisitions == 0
