"""Interactive Text Console for TOM CLI mode.

Provides a non-blocking readline loop over stdin for the --cli operating mode.
"""

from __future__ import annotations

import asyncio
import sys

from tom.agents.orchestrator import AgentOrchestrator
from tom.core.context import CancellationToken
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="tom.desktop.console")


class TextConsole:
    """Async readline-based text console for interactive CLI mode."""

    def __init__(
        self,
        reader: asyncio.StreamReader | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._running = False
        self._stop_event = stop_event
        self._reader = reader

    async def run(self, orchestrator: AgentOrchestrator) -> None:
        """Run the interactive console loop.

        Args:
            orchestrator: The AgentOrchestrator to stream responses from.
        """
        self._running = True
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        # Set up stdin reader if not injected.
        # On Windows, connect_read_pipe raises [WinError 6] (handle invalid) on console stdin.
        if self._reader is None and sys.platform != "win32":
            try:
                self._reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(self._reader)
                await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)
            except (NotImplementedError, OSError, AttributeError):
                self._reader = None

        print("TOM CLI ÔÇö Type 'exit' or 'quit' to leave. Ctrl+D to EOF.")
        print()

        try:
            while self._running and not self._stop_event.is_set():
                try:
                    line = await self._read_line()
                except EOFError:
                    break

                if not line:
                    continue

                line = line.strip()
                if not line:
                    continue

                if line.lower() in ("exit", "quit"):
                    break

                # Stream response from orchestrator
                token = CancellationToken()
                print("TOM: ", end="", flush=True)
                first = True
                async for delta in orchestrator.stream(line, token):
                    if first:
                        print(delta, end="", flush=True)
                        first = False
                    else:
                        print(delta, end="", flush=True)
                print()  # Newline after response
                print()

        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            self._running = False

    async def _read_line(self) -> str:
        """Read a single line from stdin asynchronously."""
        if self._reader is not None:
            line_bytes = await self._reader.readline()
            if not line_bytes:
                raise EOFError("End of input")
            return line_bytes.decode("utf-8", errors="replace")

        # Windows/fallback path: read via non-blocking worker thread
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            raise EOFError("End of input")
        return line

    async def stop(self) -> None:
        """Stop the console loop."""
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
