"""Shared fixtures for Python <-> Rust live integration tests."""

import asyncio
import os
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from tom.ipc.protocol import PIPE_NAME
from tom.ipc.transport import NamedPipeTransport
from tom.schemas.config import IpcConfig


def _can_connect_pipe() -> bool:
    """Check if the Windows named pipe is currently listening."""

    async def _try_connect() -> bool:
        transport = NamedPipeTransport(
            config=IpcConfig(pipe_name=PIPE_NAME, connection_timeout_ms=500)
        )
        try:
            await transport.connect()
            await transport.close()
            return True
        except Exception:
            return False

    return asyncio.run(_try_connect())


@pytest.fixture(scope="session")
def live_engine() -> Generator[str, None, None]:
    """Ensure tom-engine is running and listening on the named pipe.

    If the pipe is already open, uses it directly.
    Otherwise, if the debug binary is available, starts it as a subprocess
    and terminates it at the end of the session.
    Skips the test if the engine cannot be reached or started.
    """
    if _can_connect_pipe():
        yield PIPE_NAME
        return

    # Find the compiled binary
    repo_root = Path(__file__).resolve().parents[3]
    exe_path = repo_root / "rust" / "tom-engine" / "target" / "debug" / "tom-engine.exe"

    if not exe_path.is_file():
        pytest.skip(
            f"tom-engine binary not found at {exe_path} and pipe {PIPE_NAME} is not open. "
            "Run 'cargo build' in rust/tom-engine first."
        )

    # Launch tom-engine in background
    proc = subprocess.Popen(
        [str(exe_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )

    # Wait up to 5s for pipe to open
    connected = False
    start_time = time.monotonic()
    while time.monotonic() - start_time < 5.0:
        if proc.poll() is not None:
            break
        if _can_connect_pipe():
            connected = True
            break
        time.sleep(0.1)

    if not connected:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.skip("Failed to connect to tom-engine named pipe after launching binary.")

    try:
        yield PIPE_NAME
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
