from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from tom.desktop.console import CONTROL_PING, CONTROL_PONG, TextConsole


class MockOrchestrator:
    async def stream(self, prompt: str, token) -> AsyncIterator[str]:
        yield f"Echo: {prompt}"


@pytest.mark.asyncio
async def test_console_submit_input_and_stream(capsys: pytest.CaptureFixture[str]) -> None:
    stop_event = asyncio.Event()
    console = TextConsole(stop_event=stop_event, attach_stdin=False)
    orchestrator = MockOrchestrator()

    task = asyncio.create_task(console.run(orchestrator))  # type: ignore[arg-type]
    await asyncio.sleep(0.02)

    console.submit_input("Hello TOM")
    await asyncio.sleep(0.05)

    console.submit_input("exit")
    await asyncio.wait_for(task, timeout=2.0)

    captured = capsys.readouterr()
    assert "TOM CLI" in captured.out
    assert "Echo: Hello TOM" in captured.out


@pytest.mark.asyncio
async def test_console_ping_pong(capsys: pytest.CaptureFixture[str]) -> None:
    stop_event = asyncio.Event()
    console = TextConsole(stop_event=stop_event, attach_stdin=False)
    orchestrator = MockOrchestrator()

    task = asyncio.create_task(console.run(orchestrator))  # type: ignore[arg-type]
    await asyncio.sleep(0.02)

    console.submit_input(CONTROL_PING)
    await asyncio.sleep(0.05)

    console.submit_input("quit")
    await asyncio.wait_for(task, timeout=2.0)

    captured = capsys.readouterr()
    assert CONTROL_PONG in captured.out


@pytest.mark.asyncio
async def test_console_stop_event_terminates_cleanly() -> None:
    stop_event = asyncio.Event()
    console = TextConsole(stop_event=stop_event, attach_stdin=False)
    orchestrator = MockOrchestrator()

    task = asyncio.create_task(console.run(orchestrator))  # type: ignore[arg-type]
    await asyncio.sleep(0.02)

    await console.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert not console._running
