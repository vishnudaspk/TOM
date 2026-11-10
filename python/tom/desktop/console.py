"""Interactive Text Console for TOM CLI mode.

Provides a non-blocking readline loop over stdin and internal input queue for the --cli operating mode.
"""

from __future__ import annotations

import asyncio
import sys
import threading

from tom.agents.orchestrator import AgentOrchestrator
from tom.core.context import CancellationToken
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="tom.desktop.console")

CONTROL_PING = "__tom_control_ping__"
CONTROL_PONG = "__tom_control_pong__"


def _safe_print(*args: object, end: str = "\n", flush: bool = True) -> None:
    """Print to stdout ignoring OS/pipe write errors during shutdown."""
    try:
        print(*args, end=end, file=sys.stdout)
        if flush and sys.stdout is not None:
            sys.stdout.flush()
    except (OSError, ValueError):
        pass


class TextConsole:
    """Async text console supporting both stdin and programmatic input injection."""

    def __init__(
        self,
        reader: asyncio.StreamReader | None = None,
        stop_event: asyncio.Event | None = None,
        attach_stdin: bool = True,
    ) -> None:
        self._running = False
        self._stop_event = stop_event
        self._reader = reader
        self._attach_stdin = attach_stdin
        self._input_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stdin_thread: threading.Thread | None = None

    def submit_input(self, line: str) -> None:
        """Inject input programmatically (e.g. from Control API or IPC bridge)."""
        self._input_queue.put_nowait(line)

    def _stdin_daemon_target(self, loop: asyncio.AbstractEventLoop) -> None:
        """Daemon thread reading sys.stdin.

        Being a daemon thread ensures it never blocks Python interpreter shutdown
        or causes [Errno 22] in threadpool join.
        """
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                if self._running:
                    loop.call_soon_threadsafe(self._input_queue.put_nowait, line)
            except (OSError, ValueError, EOFError):
                break
            except Exception as exc:
                logger.debug("stdin_read_error", error=str(exc))
                break
        if self._running:
            loop.call_soon_threadsafe(self._input_queue.put_nowait, None)

    async def run(self, orchestrator: AgentOrchestrator) -> None:
        """Run the interactive console loop.

        Args:
            orchestrator: The AgentOrchestrator to stream responses from.
        """
        self._running = True
        if self._stop_event is None:
            self._stop_event = asyncio.Event()

        _safe_print("TOM CLI - Type 'exit' or 'quit' to leave. Ctrl+D to EOF.")
        _safe_print()

        # Start dedicated daemon thread for stdin reads if enabled and sys.stdin is available
        if self._attach_stdin and sys.stdin is not None:
            loop = asyncio.get_running_loop()
            self._stdin_thread = threading.Thread(
                target=self._stdin_daemon_target,
                args=(loop,),
                name="tom-cli-stdin-reader",
                daemon=True,
            )
            self._stdin_thread.start()

        try:
            while self._running and not self._stop_event.is_set():
                get_task = asyncio.create_task(self._input_queue.get())
                stop_task = asyncio.create_task(self._stop_event.wait())

                done, pending = await asyncio.wait(
                    [get_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                if stop_task in done:
                    break

                raw_line = get_task.result()
                if raw_line is None:
                    # Stdin EOF reached; if queue empty, finish
                    break

                line = raw_line.strip()
                if not line:
                    continue

                if line == CONTROL_PING:
                    _safe_print(CONTROL_PONG)
                    continue

                if line.lower() in ("exit", "quit"):
                    break

                # Stream response from orchestrator
                token = CancellationToken()
                _safe_print("TOM: ", end="", flush=True)
                first = True
                try:
                    async for delta in orchestrator.stream(line, token):
                        if first:
                            _safe_print(delta, end="", flush=True)
                            first = False
                        else:
                            _safe_print(delta, end="", flush=True)
                    _safe_print()
                    _safe_print()
                except Exception as exc:
                    logger.error("cli_orchestrator_stream_error", error=str(exc))
                    _safe_print(f"\nError: {exc}\n")

        except KeyboardInterrupt:
            _safe_print("\nInterrupted.")
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the console loop."""
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
        self._input_queue.put_nowait(None)
