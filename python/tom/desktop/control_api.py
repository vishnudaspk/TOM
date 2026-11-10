"""Authenticated, loopback-only management API used by TOM Control Center.

Diagnostics are read-only except for explicit, whitelisted voice lifecycle and
hardware test operations plus graceful application shutdown. This API cannot
execute TOM tools or change permissions.
"""

from __future__ import annotations

import asyncio
import hmac
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

API_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024


def sqlite_readonly_diagnostic(database_path: str | Path) -> dict[str, Any]:
    """Inspect SQLite connectivity without creating or modifying a database."""
    path = Path(database_path).expanduser()
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sqlite_version": sqlite3.sqlite_version,
        "integrity": "unavailable",
        "last_checked": datetime.now(UTC).isoformat(),
        "error": None,
    }
    if not path.is_file():
        result["integrity"] = "not_configured"
        return result
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=0.3,
        )
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=300")
        result["integrity"] = connection.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            connection.close()
    return result


class ControlApiServer:
    def __init__(
        self,
        *,
        port: int,
        token: str,
        status_provider: Callable[[], Awaitable[dict[str, Any]]],
        tools_provider: Callable[[], dict[str, Any]],
        memory_provider: Callable[[], Awaitable[dict[str, Any]]],
        shutdown_callback: Callable[[], None],
        voice_operation: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        cli_input_callback: Callable[[str], None] | None = None,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("Control API port must be between 1 and 65535")
        if len(token) < 32:
            raise ValueError("Control API token must contain at least 32 characters")
        self._port = port
        self._token = token
        self._status_provider = status_provider
        self._tools_provider = tools_provider
        self._memory_provider = memory_provider
        self._shutdown_callback = shutdown_callback
        self._voice_operation = voice_operation
        self._cli_input_callback = cli_input_callback
        self._runner: web.AppRunner | None = None

    @staticmethod
    def _reply(data: Any, *, status: int = 200) -> web.Response:
        return web.json_response({"api_version": API_VERSION, "data": data}, status=status)

    @web.middleware
    async def _authenticate(self, request: web.Request, handler: Callable[..., Any]) -> web.Response:
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {self._token}"
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return self._reply({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def start(self) -> None:
        if self._runner is not None:
            raise RuntimeError("Control API is already running")
        app = web.Application(client_max_size=MAX_REQUEST_BYTES, middlewares=[self._authenticate])
        app.router.add_get("/api/v1/status", self._status)
        app.router.add_get("/api/v1/tools", self._tools)
        app.router.add_get("/api/v1/memory", self._memory)
        app.router.add_post("/api/v1/cli/input", self._cli_input)
        app.router.add_post("/api/v1/voice/{operation}", self._voice)
        app.router.add_post("/api/v1/shutdown", self._shutdown)
        runner = web.AppRunner(app, access_log=None, handle_signals=False, shutdown_timeout=2.0)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=self._port)
        try:
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner

    async def close(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    async def _status(self, _request: web.Request) -> web.Response:
        return self._reply(await self._status_provider())

    async def _tools(self, _request: web.Request) -> web.Response:
        return self._reply(self._tools_provider())

    async def _memory(self, _request: web.Request) -> web.Response:
        return self._reply(await self._memory_provider())

    async def _voice(self, request: web.Request) -> web.Response:
        if self._voice_operation is None:
            return self._reply({"error": "voice operations are unsupported in this mode"}, status=501)
        try:
            payload = await request.json() if request.can_read_body else {}
            if not isinstance(payload, dict):
                return self._reply({"error": "voice request must be a JSON object"}, status=400)
            return self._reply(await self._voice_operation(request.match_info["operation"], payload))
        except (ValueError, RuntimeError) as exc:
            return self._reply({"error": str(exc)}, status=409)
        except Exception as exc:
            return self._reply({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    async def _cli_input(self, request: web.Request) -> web.Response:
        if self._cli_input_callback is None:
            return self._reply({"error": "CLI input is unsupported in this mode"}, status=501)
        try:
            payload = await request.json() if request.can_read_body else {}
            if not isinstance(payload, dict):
                return self._reply({"error": "request body must be a JSON object"}, status=400)
            line = str(payload.get("line", ""))
            self._cli_input_callback(line)
            return self._reply({"accepted": True})
        except Exception as exc:
            return self._reply({"error": str(exc)}, status=400)

    async def _shutdown(self, request: web.Request) -> web.Response:
        if request.can_read_body:
            body = await request.json()
            if body not in ({}, None):
                return self._reply({"error": "shutdown body must be empty"}, status=400)
        response = self._reply({"accepted": True})
        async def stop_after_response() -> None:
            await asyncio.sleep(0.1)
            self._shutdown_callback()

        asyncio.create_task(stop_after_response(), name="control_api_shutdown")
        return response
