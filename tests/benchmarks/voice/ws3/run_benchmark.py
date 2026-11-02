#!/usr/bin/env python3
"""Voice latency benchmark - runs existing integration test and parses latency reports."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

try:
    import nvidia_smi
    HAS_NVIDIA_SMI = True
except ImportError:
    HAS_NVIDIA_SMI = False


def get_vram_mib() -> int:
    """Return total VRAM used in MiB."""
    if not HAS_NVIDIA_SMI:
        return -1
    try:
        nvidia_smi.nvmlInit()
        handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
        info = nvidia_smi.nvmlDeviceGetMemoryInfo(handle)
        nvidia_smi.nvmlShutdown()
        return info.used // (1024 * 1024)
    except Exception:
        return -1


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(values: list[float], label: str) -> str:
    if not values:
        return f"{label}: no data"
    vals = [v for v in values if v is not None]
    if not vals:
        return f"{label}: all None"
    return (
        f"{label}: p50={percentile(vals, 50):.1f}ms, "
        f"p95={percentile(vals, 95):.1f}ms, "
        f"min={min(vals):.1f}ms, max={max(vals):.1f}ms, n={len(vals)}"
    )


def parse_latency_reports(log_output: str) -> list[dict]:
    """Parse latency_report events from log output."""
    reports = []
    for line in log_output.split('\n'):
        line = line.strip()
        if line.startswith('{'):
            try:
                data = json.loads(line)
                if data.get("event") == "voice.latency.report":
                    latency_report = data.get("latency_report", {})
                    if latency_report:
                        reports.append({
                            'utterance_id': data.get("context", {}).get("utterance_id"),
                            'metrics': latency_report.get("metrics", {})
                        })
            except json.JSONDecodeError:
                continue
    return reports


def run_integration_test_benchmark(num_turns: int = 10) -> list[dict]:
    """Run the integration test and capture latency reports from logs."""
    
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/integration/voice/test_interaction_session.py::test_eight_turn_session_survives_every_failure_and_stays_measurable",
        "-v", "-s", "--tb=short"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=120,
    )
    
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    
    # Parse latency reports from the log file
    log_file = ROOT / "logs" / "tom.log"
    if log_file.exists():
        with open(log_file, "r") as f:
            log_output = f.read()
        return parse_latency_reports(log_output)
    else:
        print(f"Warning: Log file not found at {log_file}")
        return []


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(values: list[float], label: str) -> str:
    if not values:
        return f"{label}: no data"
    vals = [v for v in values if v is not None]
    if not vals:
        return f"{label}: all None"
    return (
        f"{label}: p50={percentile(vals, 50):.1f}ms, "
        f"p95={percentile(vals, 95):.1f}ms, "
        f"min={min(vals):.1f}ms, max={max(vals):.1f}ms, n={len(vals)}"
    )


def get_vram_mib() -> int:
    if not globals().get('HAS_NVIDIA_SMI', False):
        return -1
    try:
        nvidia_smi.nvmlInit()
        handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
        info = nvidia_smi.nvmlDeviceGetMemoryInfo(handle)
        nvidia_smi.nvmlShutdown()
        return info.used // (1024 * 1024)
    except Exception:
        return -1


def run_integration_test_benchmark(num_turns: int = 10) -> list[dict]:
    """Run the integration test and capture latency reports from logs."""
    
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/integration/voice/test_interaction_session.py::test_eight_turn_session_survives_every_failure_and_stays_measurable",
        "-v", "-s", "--tb=short"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=120,
    )
    
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    
    return parse_latency_reports(result.stdout)


def parse_latency_reports(log_output: str) -> list[dict]:
    """Parse latency_report events from log output."""
    reports = []
    for line in log_output.split('\n'):
        line = line.strip()
        if line.startswith('{'):
            try:
                data = json.loads(line)
                if data.get("event") == "voice.latency.report":
                    latency_report = data.get("latency_report", {})
                    if latency_report:
                        reports.append({
                            'utterance_id': data.get("context", {}).get("utterance_id"),
                            'metrics': latency_report.get("metrics", {})
                        })
            except json.JSONDecodeError:
                continue
    return reports


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(values: list[float], label: str) -> str:
    if not values:
        return f"{label}: no data"
    vals = [v for v in values if v is not None]
    if not vals:
        return f"{label}: all None"
    return (
        f"{label}: p50={percentile(vals, 50):.1f}ms, "
        f"p95={percentile(vals, 95):.1f}ms, "
        f"min={min(vals):.1f}ms, max={max(vals):.1f}ms, n={len(vals)}"
    )


def get_vram_mib() -> int:
    if not globals().get('HAS_NVIDIA_SMI', False):
        return -1
    try:
        nvidia_smi.nvmlInit()
        handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
        info = nvidia_smi.nvmlDeviceGetMemoryInfo(handle)
        nvidia_smi.nvmlShutdown()
        return info.used // (1024 * 1024)
    except Exception:
        return -1


def run_integration_test_benchmark(num_turns: int = 10) -> list[dict]:
    """Run the integration test and capture latency reports from logs."""
    
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/integration/voice/test_interaction_session.py::test_eight_turn_session_survives_every_failure_and_stays_measurable",
        "-v", "-s", "--tb=short"
    ]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=120,
    )
    
    print("STDOUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:")
        print(result.stderr)
    
    return parse_latency_reports(result.stdout)


def parse_latency_reports(log_output: str) -> list[dict]:
    """Parse latency_report events from log output."""
    reports = []
    for line in log_output.split('\n'):
        line = line.strip()
        if line.startswith('{'):
            try:
                data = json.loads(line)
                if data.get("event") == "voice.latency.report":
                    latency_report = data.get("latency_report", {})
                    if latency_report:
                        reports.append({
                            'utterance_id': data.get("context", {}).get("utterance_id"),
                            'metrics': latency_report.get("metrics", {})
                        })
            except json.JSONDecodeError:
                continue
    return reports


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(values: list[float], label: str) -> str:
    if not values:
        return f"{label}: no data"
    vals = [v for v in values if v is not None]
    if not vals:
        return f"{label}: all None"
    return (
        f"{label}: p50={percentile(vals, 50):.1f}ms, "
        f"p95={percentile(vals, 95):.1f}ms, "
        f"min={min(vals):.1f}ms, max={max(vals):.1f}ms, n={len(vals)}"
    )


def get_vram_mib() -> int:
    if not globals().get('HAS_NVIDIA_SMI', False):
        return -1
    try:
        nvidia_smi.nvmlInit()
        handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
        info = nvidia_smi.nvmlDeviceGetMemoryInfo(handle)
        nvidia_smi.nvmlShutdown()
        return info.used // (1024 * 1024)
    except Exception:
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice latency benchmark - runs integration test")
    parser.add_argument("--turns", type=int, default=10, help="Number of turns (default: 10)")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent.parent.parent.parent / "docs" / "perf" / "00-baseline.md", help="Output file")
    parser.add_argument("--real", action="store_true", help="Use real providers (not implemented yet)")
    args = parser.parse_args()

    print("=" * 60)
    print("VOICE LATENCY BENCHMARK - STEP 0 BASELINE")
    print("=" * 60)

    vram_idle = get_vram_mib()
    print(f"VRAM at idle: {vram_idle} MiB")

    print("\nRunning integration test to collect latency reports...")
    reports = run_integration_test_benchmark(args.turns)
    
    vram_during = get_vram_mib()
    print(f"VRAM during benchmark: {vram_during} MiB")

    print(f"\nCollected {len(reports)} latency reports")
    
    # Parse reports
    metrics_by_name = {}
    for report in reports:
        metrics = report.get("metrics", {})
        for name, data in metrics.items():
            if name not in metrics_by_name:
                metrics_by_name[name] = []
            if data.get("value_ms") is not None:
                metrics_by_name[name].append(data["value_ms"])

    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)
    
    if reports:
        first = reports[0]
        first_metrics = first.get("metrics", {})
        print("\n--- COLD TURN (Turn 1) ---")
        for name, data in first_metrics.items():
            val = data.get("value_ms")
            status = data.get("status", "")
            print(f"  {name}: {val:.1f}ms ({status})" if val is not None else f"  {name}: N/A")
    
    warm_reports = reports[1:]
    warm_metrics = {}
    if warm_reports:
        print("\n--- WARM TURNS (Turns 2+) ---")
        for report in warm_reports:
            for name, data in report.get("metrics", {}).items():
                if name not in warm_metrics:
                    warm_metrics[name] = []
                val = data.get("value_ms")
                if val is not None:
                    warm_metrics[name].append(val)
        
        for name, values in warm_metrics.items():
            print(f"  {summarize(values, name)}")

    vram_during = get_vram_mib()
    print(f"\nVRAM at idle: {get_vram_mib()} MiB")
    print(f"VRAM during benchmark: {vram_during} MiB")

    # Save to markdown
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        f.write("# Voice Latency Baseline (Step 0)\n\n")
        f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"VRAM idle: {get_vram_mib()} MiB\n")
        f.write(f"VRAM during benchmark: {vram_during} MiB\n\n")
        
        f.write("## Latency Reports Collected\n\n")
        f.write(f"Total reports: {len(reports)}\n\n")
        
        f.write("## Cold Turn (Turn 1)\n\n")
        if reports:
            first = reports[0]
            for name, data in first.get("metrics", {}).items():
                val = data.get("value_ms")
                status = data.get("status", "")
                f.write(f"- {name}: {val:.1f}ms ({status})\n" if val is not None else f"- {name}: N/A\n")
        
        f.write("\n## Warm Turns (p50/p95)\n\n")
        for name, values in warm_metrics.items():
            f.write(f"- {summarize(values, name)}\n")
        
        f.write(f"\nVRAM during benchmark: {vram_during} MiB\n")

    print(f"\nBaseline saved to {args.output}")
    print("See pytest output above for latency reports.")
    return 0


if __name__ == "__main__":
    import time
    sys.exit(main())