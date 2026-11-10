from __future__ import annotations

import asyncio
import socket

import aiohttp
import pytest
from tom.desktop.control_api import ControlApiServer, sqlite_readonly_diagnostic


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_control_api_requires_bearer_and_exports_read_only_diagnostics() -> None:
    token = "a" * 48
    cli_inputs: list[str] = []
    api = ControlApiServer(
        port=free_port(),
        token=token,
        status_provider=lambda: asyncio.sleep(0, result={"state": "ready"}),
        tools_provider=lambda: {"tools": [{"name": "system.cpu_info", "permission": "SAFE"}]},
        memory_provider=lambda: asyncio.sleep(0, result={"available": False}),
        shutdown_callback=lambda: None,
        voice_operation=lambda op, body: asyncio.sleep(0, result={"operation": op, **body}),
        cli_input_callback=lambda line: cli_inputs.append(line),
    )
    await api.start()
    try:
        async with aiohttp.ClientSession() as client:
            url = f"http://127.0.0.1:{api._port}/api/v1"
            denied = await client.get(f"{url}/status")
            assert denied.status == 401
            headers = {"Authorization": f"Bearer {token}"}
            status = await client.get(f"{url}/status", headers=headers)
            assert (await status.json()) == {"api_version": 1, "data": {"state": "ready"}}
            tools = await client.get(f"{url}/tools", headers=headers)
            assert (await tools.json())["data"]["tools"][0]["permission"] == "SAFE"
            cli_res = await client.post(f"{url}/cli/input", headers=headers, json={"line": "hello tom"})
            assert cli_res.status == 200
            assert cli_inputs == ["hello tom"]
            invalid_shutdown = await client.post(f"{url}/shutdown", headers=headers, json={"force": True})
            assert invalid_shutdown.status == 400
            voice = await client.post(f"{url}/voice/test_tts", headers=headers, json={"text": "hello"})
            assert (await voice.json())["data"] == {"operation": "test_tts", "text": "hello"}
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_control_api_rejects_weak_token() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        ControlApiServer(
            port=65403,
            token="short",
            status_provider=lambda: asyncio.sleep(0, result={}),
            tools_provider=dict,
            memory_provider=lambda: asyncio.sleep(0, result={}),
            shutdown_callback=lambda: None,
        )


def test_sqlite_diagnostic_does_not_create_missing_database(tmp_path) -> None:
    missing = tmp_path / "missing.db"
    result = sqlite_readonly_diagnostic(missing)
    assert result["integrity"] == "not_configured"
    assert not missing.exists()


def test_sqlite_diagnostic_reads_existing_database_only(tmp_path) -> None:
    import sqlite3

    database = tmp_path / "memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE private_data (secret TEXT)")
        connection.execute("INSERT INTO private_data VALUES ('must not be returned')")
    result = sqlite_readonly_diagnostic(database)
    assert result["integrity"] == "ok"
    assert result["size_bytes"] > 0
    assert "must not be returned" not in str(result)
