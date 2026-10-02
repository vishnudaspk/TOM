"""Unit tests for Hardware Resource Governor (ResourceManager).

Adheres to:
- Offline, deterministic, zero GPU or network requirement.
- Verifies VRAM headroom, RAM limits, mutual exclusion, cancellation, and exceptions.
"""

from __future__ import annotations

import asyncio

import pytest
from tom.resources.manager import (
    InsufficientResourceError,
    MockTelemetryProvider,
    ModelRole,
    ResourceLockError,
    ResourceManager,
)
from tom.schemas.config import ResourcesConfig


@pytest.fixture
def mock_telemetry() -> MockTelemetryProvider:
    return MockTelemetryProvider(
        vram_total_mb=8192.0,
        vram_used_mb=1000.0,
        vram_free_mb=7192.0,
        ram_total_mb=16384.0,
        ram_used_mb=4000.0,
        ram_free_mb=12384.0,
        gpu_available=True,
    )


@pytest.fixture
def resource_manager(mock_telemetry: MockTelemetryProvider) -> ResourceManager:
    config = ResourcesConfig(
        max_vram_mb=7200,
        max_ram_mb=12000,
        gpu_device_id=0,
    )
    return ResourceManager(
        config=config,
        telemetry_provider=mock_telemetry,
        safety_headroom_mb=500,
    )


class TestResourceManager:
    def test_default_initialization(self, resource_manager: ResourceManager) -> None:
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.allocated_ram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.safety_headroom_mb == 500

    def test_telemetry_query_with_mock(
        self, resource_manager: ResourceManager, mock_telemetry: MockTelemetryProvider
    ) -> None:
        telem = resource_manager.get_telemetry()
        assert telem.gpu_available is True
        assert telem.vram_total_mb == 8192.0
        assert telem.vram_free_mb == 7192.0
        assert telem.ram_total_mb == 16384.0

    @pytest.mark.anyio
    async def test_sufficient_resource_acquisition(self, resource_manager: ResourceManager) -> None:
        lease = await resource_manager.acquire(
            role=ModelRole.ROUTER,
            required_vram_mb=1200,
            required_ram_mb=1000,
        )
        assert lease.is_active
        assert lease.role == ModelRole.ROUTER
        assert lease.vram_mb == 1200
        assert lease.ram_mb == 1000
        assert resource_manager.allocated_vram_mb == 1200
        assert resource_manager.allocated_ram_mb == 1000
        assert resource_manager.active_leases_count == 1

        await lease.release()
        assert not lease.is_active
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.allocated_ram_mb == 0
        assert resource_manager.active_leases_count == 0

    @pytest.mark.anyio
    async def test_500mb_headroom_enforcement_software_limit(self) -> None:
        # Config max_vram = 7200, headroom = 500 -> usable ceiling = 6700 MB
        mgr = ResourceManager(
            config=ResourcesConfig(max_vram_mb=7200),
            telemetry_provider=MockTelemetryProvider(gpu_available=False),
            safety_headroom_mb=500,
        )
        assert mgr.check_budget(required_vram_mb=6700) is True
        assert mgr.check_budget(required_vram_mb=6701) is False

        with pytest.raises(InsufficientResourceError) as exc_info:
            await mgr.acquire(role=ModelRole.GENERAL, required_vram_mb=6701)
        assert "headroom 500MB" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_500mb_headroom_enforcement_live_telemetry(
        self, mock_telemetry: MockTelemetryProvider
    ) -> None:
        # Mock telemetry free VRAM is 2000 MB. Headroom is 500 MB -> usable free is 1500 MB
        mock_telemetry.vram_free_mb = 2000.0
        mgr = ResourceManager(
            config=ResourcesConfig(max_vram_mb=7200),
            telemetry_provider=mock_telemetry,
            safety_headroom_mb=500,
        )

        assert mgr.check_budget(required_vram_mb=1500) is True
        assert mgr.check_budget(required_vram_mb=1501) is False

        with pytest.raises(InsufficientResourceError):
            await mgr.acquire(role=ModelRole.GENERAL, required_vram_mb=1501)

    @pytest.mark.anyio
    async def test_ram_limit_enforcement(self, resource_manager: ResourceManager) -> None:
        # Config max_ram_mb = 12000
        assert resource_manager.check_budget(required_ram_mb=12000) is True
        assert resource_manager.check_budget(required_ram_mb=12001) is False

        with pytest.raises(InsufficientResourceError):
            await resource_manager.acquire(role=ModelRole.ROUTER, required_ram_mb=12001)

    @pytest.mark.anyio
    async def test_telemetry_unavailable_fallback(
        self, mock_telemetry: MockTelemetryProvider
    ) -> None:
        mock_telemetry.gpu_available = False
        mock_telemetry.should_fail = True
        mgr = ResourceManager(
            config=ResourcesConfig(max_vram_mb=7200, max_ram_mb=12000),
            telemetry_provider=mock_telemetry,
            safety_headroom_mb=500,
        )

        # Telemetry query does not throw, returns fallback
        telem = mgr.get_telemetry()
        assert telem.gpu_available is False

        # Budget checking still works based on software allocation limits
        assert mgr.check_budget(required_vram_mb=5000) is True
        lease = await mgr.acquire(role=ModelRole.ROUTER, required_vram_mb=5000)
        assert lease.is_active
        await lease.release()

    @pytest.mark.anyio
    async def test_reasoning_and_vlm_mutual_exclusion(
        self, resource_manager: ResourceManager
    ) -> None:
        # 1. Acquire heavy reasoning lease
        reasoning_lease = await resource_manager.acquire(
            role=ModelRole.REASONING,
            required_vram_mb=4000,
        )
        assert resource_manager.is_heavy_inference_active

        # 2. Attempting to acquire local VLM non-blocking must fail with ResourceLockError
        with pytest.raises(ResourceLockError) as exc_info:
            await resource_manager.acquire(
                role=ModelRole.VLM,
                required_vram_mb=2000,
                blocking=False,
            )
        assert "Heavy inference lock is already held" in str(exc_info.value)

        # 3. Attempting to acquire with timeout must fail with ResourceLockError
        with pytest.raises(ResourceLockError) as exc_info2:
            await resource_manager.acquire(
                role=ModelRole.VLM,
                required_vram_mb=2000,
                timeout=0.05,
            )
        assert "Timeout of 0.05s exceeded" in str(exc_info2.value)

        # 4. Release reasoning lease -> heavy inference lock is released
        await reasoning_lease.release()
        assert not resource_manager.is_heavy_inference_active

        # 5. Now local VLM can acquire the lock
        vlm_lease = await resource_manager.acquire(
            role=ModelRole.VLM,
            required_vram_mb=2000,
            blocking=False,
        )
        assert resource_manager.is_heavy_inference_active
        await vlm_lease.release()
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_router_can_run_concurrently_with_reasoning(
        self, resource_manager: ResourceManager
    ) -> None:
        # Router is a lightweight model role and does not acquire the heavy inference lock
        reasoning_lease = await resource_manager.acquire(
            role=ModelRole.REASONING,
            required_vram_mb=4000,
        )
        router_lease = await resource_manager.acquire(
            role=ModelRole.ROUTER,
            required_vram_mb=1200,
        )

        assert resource_manager.allocated_vram_mb == 5200
        assert resource_manager.active_leases_count == 2

        await router_lease.release()
        await reasoning_lease.release()

        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0

    @pytest.mark.anyio
    async def test_idempotent_release(self, resource_manager: ResourceManager) -> None:
        lease = await resource_manager.acquire(role=ModelRole.ROUTER, required_vram_mb=1000)
        assert resource_manager.allocated_vram_mb == 1000

        await lease.release()
        assert resource_manager.allocated_vram_mb == 0

        # Repeated release does not decrease below 0
        await lease.release()
        await resource_manager.release(lease)
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_context_manager_clean_exit(self, resource_manager: ResourceManager) -> None:
        async with resource_manager.acquire(
            role=ModelRole.REASONING, required_vram_mb=3500
        ) as lease:
            assert lease.is_active
            assert resource_manager.is_heavy_inference_active
            assert resource_manager.allocated_vram_mb == 3500

        # After exiting context manager
        assert not lease.is_active
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_release_after_exception_in_context(
        self, resource_manager: ResourceManager
    ) -> None:
        with pytest.raises(RuntimeError):
            async with resource_manager.acquire(role=ModelRole.REASONING, required_vram_mb=3500):
                assert resource_manager.is_heavy_inference_active
                raise RuntimeError("Simulation of model inference crash")

        # Must be released despite the exception
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0

    @pytest.mark.anyio
    async def test_cancellation_safe_cleanup(self, resource_manager: ResourceManager) -> None:
        # Hold heavy lock
        first_lease = await resource_manager.acquire(
            role=ModelRole.REASONING, required_vram_mb=2000
        )

        waiting_task_started = asyncio.Event()

        async def waiting_acquirer() -> None:
            waiting_task_started.set()
            async with resource_manager.acquire(role=ModelRole.VLM, required_vram_mb=2000):
                pass

        task = asyncio.create_task(waiting_acquirer())
        await waiting_task_started.wait()
        await asyncio.sleep(0.01)

        # Cancel while waiting on the lock
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Release first lease
        await first_lease.release()
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.allocated_vram_mb == 0

        # State remains clean and allows subsequent acquisition
        second_lease = await resource_manager.acquire(role=ModelRole.VLM, required_vram_mb=2000)
        assert resource_manager.is_heavy_inference_active
        await second_lease.release()

    @pytest.mark.anyio
    async def test_concurrent_sequential_acquisition(
        self, resource_manager: ResourceManager
    ) -> None:
        results: list[str] = []

        async def worker(role: ModelRole, name: str) -> None:
            async with resource_manager.acquire(role=role, required_vram_mb=1000):
                results.append(f"{name}_start")
                await asyncio.sleep(0.02)
                results.append(f"{name}_end")

        # Run 2 reasoning and 1 vlm workers concurrently
        await asyncio.gather(
            worker(ModelRole.REASONING, "w1"),
            worker(ModelRole.VLM, "w2"),
            worker(ModelRole.REASONING, "w3"),
        )

        assert len(results) == 6
        # Check mutual exclusion: a start must be immediately followed by end before next start
        for i in range(0, 6, 2):
            assert results[i].endswith("_start")
            assert results[i + 1].endswith("_end")
            assert results[i].split("_")[0] == results[i + 1].split("_")[0]

        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active
