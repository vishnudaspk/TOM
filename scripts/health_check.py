#!/usr/bin/env python3
"""TOM Health Check CLI.

Runs a bounded sequence of diagnostic checks against the TOM engine,
inference endpoint, GPU, system RAM, and model lifecycle state.

Exit codes:
  0: All required checks HEALTHY
  1: At least one required check DEGRADED (none UNAVAILABLE/FAILED)
  2: Any required check UNAVAILABLE or FAILED

Usage:
  python scripts/health_check.py
"""

import asyncio
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

from tom.core.engine import EngineClient
from tom.ipc.client import NamedPipeIpcClient
from tom.ipc.errors import (
    ConnectionLostError,
    IpcTimeoutError,
    MaxRetriesExceededError,
    NotConnectedError,
    RemoteError,
)
from tom.models.providers.http import HttpModelProvider
from tom.schemas.config import TOMConfig
from tom.telemetry.logging import configure_logging


class CheckStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str


async def check_engine_ipc(
    ipc_client: NamedPipeIpcClient,
    timeout_s: float,
) -> CheckResult:
    """Check engine IPC connectivity via ping."""
    try:
        engine = EngineClient(ipc_client)
        ping_resp = await asyncio.wait_for(engine.ping(), timeout=timeout_s)
        if ping_resp.pong is True:
            return CheckResult(
                "Engine IPC",
                CheckStatus.HEALTHY,
                f"Engine responsive (version: {ping_resp.version})",
            )
        return CheckResult(
            "Engine IPC",
            CheckStatus.UNAVAILABLE,
            "Engine ping returned pong=false",
        )
    except TimeoutError:
        return CheckResult("Engine IPC", CheckStatus.UNAVAILABLE, "Connection timeout")
    except NotConnectedError:
        return CheckResult("Engine IPC", CheckStatus.UNAVAILABLE, "Not connected to engine")
    except (ConnectionLostError, IpcTimeoutError, RemoteError, MaxRetriesExceededError) as exc:
        return CheckResult("Engine IPC", CheckStatus.UNAVAILABLE, f"IPC error: {exc}")
    except Exception as exc:
        return CheckResult("Engine IPC", CheckStatus.FAILED, f"Unexpected error: {exc}")


async def check_inference_endpoint(
    provider: HttpModelProvider,
    timeout_s: float,
) -> CheckResult:
    """Check inference endpoint health."""
    try:
        healthy = await asyncio.wait_for(provider.check_health(), timeout=timeout_s)
        if healthy:
            return CheckResult(
                "Inference Endpoint",
                CheckStatus.HEALTHY,
                "Endpoint reachable and responding",
            )
        # check_health returns False for both non-200 responses and connection failures
        # Since we can't distinguish without a lower-level check, report as UNAVAILABLE
        return CheckResult(
            "Inference Endpoint",
            CheckStatus.UNAVAILABLE,
            "Endpoint unreachable or returned non-200",
        )
    except TimeoutError:
        return CheckResult("Inference Endpoint", CheckStatus.UNAVAILABLE, "Health check timeout")
    except Exception as exc:
        return CheckResult("Inference Endpoint", CheckStatus.FAILED, f"Unexpected error: {exc}")


async def check_gpu_telemetry(
    ipc_client: NamedPipeIpcClient,
    engine_healthy: bool,
    timeout_s: float,
) -> CheckResult:
    """Check GPU telemetry and VRAM headroom."""
    if not engine_healthy:
        return CheckResult(
            "GPU Telemetry",
            CheckStatus.UNAVAILABLE,
            "Skipped (engine unavailable)",
        )
    try:
        engine = EngineClient(ipc_client)
        gpu = await asyncio.wait_for(engine.get_gpu(), timeout=timeout_s)
        if not gpu.available:
            return CheckResult(
                "GPU Telemetry",
                CheckStatus.UNAVAILABLE,
                f"GPU unavailable: {gpu.reason or 'no GPU detected'}",
            )
        total_mb = gpu.memory_total_bytes / (1024 * 1024) if gpu.memory_total_bytes else 0
        used_mb = gpu.memory_used_bytes / (1024 * 1024) if gpu.memory_used_bytes else 0
        headroom_mb = total_mb - used_mb if total_mb > 0 else 0
        if headroom_mb > 500:
            return CheckResult(
                "GPU Telemetry",
                CheckStatus.HEALTHY,
                f"VRAM headroom: {headroom_mb:.0f} MB (total: {total_mb:.0f} MB, used: {used_mb:.0f} MB)",
            )
        return CheckResult(
            "GPU Telemetry",
            CheckStatus.DEGRADED,
            f"Low VRAM headroom: {headroom_mb:.0f} MB (threshold: 500 MB)",
        )
    except TimeoutError:
        return CheckResult("GPU Telemetry", CheckStatus.UNAVAILABLE, "GPU query timeout")
    except (NotConnectedError, ConnectionLostError, IpcTimeoutError, RemoteError) as exc:
        return CheckResult("GPU Telemetry", CheckStatus.UNAVAILABLE, f"IPC error: {exc}")
    except Exception as exc:
        return CheckResult("GPU Telemetry", CheckStatus.FAILED, f"Unexpected error: {exc}")


async def check_system_ram(
    ipc_client: NamedPipeIpcClient,
    engine_healthy: bool,
    timeout_s: float,
) -> CheckResult:
    """Check system RAM availability."""
    if not engine_healthy:
        return CheckResult(
            "System RAM",
            CheckStatus.UNAVAILABLE,
            "Skipped (engine unavailable)",
        )
    try:
        engine = EngineClient(ipc_client)
        mem = await asyncio.wait_for(engine.get_memory(), timeout=timeout_s)
        available_gb = mem.available_bytes / (1024 * 1024 * 1024)
        if available_gb > 2.0:
            return CheckResult(
                "System RAM",
                CheckStatus.HEALTHY,
                f"Available: {available_gb:.2f} GB",
            )
        return CheckResult(
            "System RAM",
            CheckStatus.DEGRADED,
            f"Low available RAM: {available_gb:.2f} GB (threshold: 2 GB)",
        )
    except TimeoutError:
        return CheckResult("System RAM", CheckStatus.UNAVAILABLE, "RAM query timeout")
    except (NotConnectedError, ConnectionLostError, IpcTimeoutError, RemoteError) as exc:
        return CheckResult("System RAM", CheckStatus.UNAVAILABLE, f"IPC error: {exc}")
    except Exception as exc:
        return CheckResult("System RAM", CheckStatus.FAILED, f"Unexpected error: {exc}")


async def check_model_states(
    coordinator: Any,
    timeout_s: float,
) -> CheckResult:
    """Display model lifecycle states (informational only)."""
    if coordinator is None:
        return CheckResult(
            "Model States",
            CheckStatus.UNAVAILABLE,
            "ModelLifecycleCoordinator not available",
        )
    try:
        registrations = await asyncio.wait_for(coordinator.list_models(), timeout=timeout_s)
        if not registrations:
            return CheckResult(
                "Model States",
                CheckStatus.HEALTHY,
                "No models registered",
            )
        # Get detailed state for each model
        state_counts: dict[str, int] = {}
        active_acquisitions_total = 0
        for reg in registrations:
            record = await asyncio.wait_for(
                coordinator.get_record(reg.model_name), timeout=timeout_s
            )
            state_str = record.state.value
            state_counts[state_str] = state_counts.get(state_str, 0) + 1
            active_acquisitions_total += record.active_acquisitions
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(state_counts.items()))
        return CheckResult(
            "Model States",
            CheckStatus.HEALTHY,
            f"{summary}; active acquisitions: {active_acquisitions_total}",
        )
    except TimeoutError:
        return CheckResult("Model States", CheckStatus.UNAVAILABLE, "Model state query timeout")
    except Exception as exc:
        return CheckResult("Model States", CheckStatus.UNAVAILABLE, f"Query failed: {exc}")


def compute_exit_code(results: list[CheckResult]) -> int:
    """Compute exit code from check results per documented precedence."""
    required_results = [r for r in results if r.name != "Model States"]
    if any(r.status == CheckStatus.FAILED for r in required_results):
        return 2
    if any(r.status == CheckStatus.UNAVAILABLE for r in required_results):
        return 2
    if any(r.status == CheckStatus.DEGRADED for r in required_results):
        return 1
    return 0


def print_results(results: list[CheckResult]) -> None:
    """Print check results in a columnar format."""
    print(f"{'Check':<20} {'Status':<12} {'Details'}")
    print("-" * 80)
    for r in results:
        print(f"{r.name:<20} {r.status.value:<12} {r.message}")


async def run_health_check(config: TOMConfig) -> tuple[int, list[CheckResult]]:
    """Run all health checks and return exit code and results."""
    timeout_s = config.telemetry.health_check_timeout_ms / 1000.0

    # Initialize IPC client
    ipc_config = config.ipc
    ipc_client = NamedPipeIpcClient(config=ipc_config)

    # Initialize HTTP provider for inference check
    # Use the default base_url which is the llama.cpp endpoint
    provider = HttpModelProvider()

    # Try to get model lifecycle coordinator (optional)
    coordinator = None
    try:
        from tom.resources.model_manager import ModelLifecycleCoordinator

        coordinator = ModelLifecycleCoordinator()
    except Exception:
        pass  # Optional, not required

    engine_healthy = False
    results: list[CheckResult] = []

    try:
        # Connect IPC
        try:
            await asyncio.wait_for(ipc_client.connect(), timeout=timeout_s)
        except Exception as exc:
            # Connection failed - record engine as unavailable but continue with independent checks
            results.append(
                CheckResult(
                    "Engine IPC",
                    CheckStatus.UNAVAILABLE,
                    f"Connection failed: {exc}",
                )
            )
            engine_healthy = False

        # 1. Engine IPC check (if not already added)
        if engine_healthy or not any(r.name == "Engine IPC" for r in results):
            result = await check_engine_ipc(ipc_client, timeout_s)
            results.append(result)
            engine_healthy = result.status == CheckStatus.HEALTHY

        # 2. Inference endpoint check (independent)
        result = await check_inference_endpoint(provider, timeout_s)
        results.append(result)

        # 3. GPU telemetry (depends on engine)
        result = await check_gpu_telemetry(ipc_client, engine_healthy, timeout_s)
        results.append(result)

        # 4. System RAM (depends on engine)
        result = await check_system_ram(ipc_client, engine_healthy, timeout_s)
        results.append(result)

        # 5. Model states (optional, informational)
        result = await check_model_states(coordinator, timeout_s)
        results.append(result)

    finally:
        # Cleanup: ensure IPC client is closed on all paths
        try:
            await ipc_client.close()
        except Exception:
            pass  # Best effort cleanup

    return compute_exit_code(results), results


def main() -> int:
    # Configure minimal logging for health check (errors only)
    configure_logging(level="ERROR", json_output=False)

    # Load configuration
    try:
        config = TOMConfig()
    except Exception as exc:
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        return 2

    # Run health checks
    try:
        exit_code, results = asyncio.run(run_health_check(config))
    except KeyboardInterrupt:
        print("\nHealth check cancelled", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Health check failed with exception: {exc}", file=sys.stderr)
        return 2

    # Print results
    print_results(results)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
