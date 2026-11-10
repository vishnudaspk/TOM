"""Unit tests for TOM Command Wheel HTTP bridge server.

Deterministic, offline — no real HTTP connections, no real tool calls.
Follows existing TOM test conventions: sync test_ methods, run_async helper,
no pytest.mark.asyncio.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _make_orchestrator(result: str = "ok response") -> MagicMock:
    orc = MagicMock()
    orc.run = AsyncMock(return_value=result)
    return orc


def _tool_result(
    success: bool = True,
    data: Any = None,
    error: str | None = None,
) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.data = data if data is not None else {}
    result.error = error
    return result


def _make_executor(
    success: bool = True,
    data: Any = None,
    error: str | None = None,
) -> MagicMock:
    exc = MagicMock()
    exc.execute = AsyncMock(return_value=_tool_result(success, data, error))
    return exc


# ---------------------------------------------------------------------------
# Tests for _dispatch helper
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_ask_routes_to_orchestrator(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("Sure, here you go!")
        exc = _make_executor()

        result = run_async(_dispatch("ask", "hello TOM", None, orc, exc))
        assert result == "Sure, here you go!"
        orc.run.assert_called_once_with("hello TOM")
        exc.execute.assert_not_called()

    def test_ask_uses_default_prompt_when_no_context(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("I can help!")
        exc = _make_executor()

        result = run_async(_dispatch("ask", "", None, orc, exc))
        assert "I can help!" in result
        call_arg = orc.run.call_args[0][0]
        assert len(call_arg) > 0

    def test_ocr_is_explicitly_deferred(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = _make_executor()
        result = run_async(_dispatch("ocr", "", None, _make_orchestrator(), exc))
        assert "deferred" in result.lower()
        exc.execute.assert_not_called()

    def test_note_calls_personal_note_with_create_action(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = _make_executor(success=True, data={"saved": True})
        result = run_async(_dispatch("note", "buy milk", None, _make_orchestrator(), exc))
        assert result == "Note saved."
        call_name, params = exc.execute.call_args[0]
        assert call_name == "personal.note"
        assert params["action"] == "create"
        assert params["title"] == "buy milk"
        assert params["content"] == "buy milk"
        assert params["tags"] == ["wheel"]

    def test_note_failed_returns_error(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = _make_executor(success=False, error="DB locked")
        result = run_async(_dispatch("note", "test", None, _make_orchestrator(), exc))
        assert "Note failed" in result

    def test_empty_note_does_not_call_tool(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = _make_executor()
        result = run_async(_dispatch("note", "  \n", None, _make_orchestrator(), exc))
        assert "nothing was saved" in result
        exc.execute.assert_not_called()

    def test_status_calls_snapshot_and_processes(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = MagicMock()
        snap_result = _tool_result(
            success=True,
            data={
                "cpu": {"usage_percent": 42},
                "memory": {"total_bytes": 1000, "used_bytes": 600},
                "gpu": {"available": True, "utilization_percent": 30},
            },
        )
        proc_result = _tool_result(
            success=True,
            data={"processes": [{"name": "chrome.exe", "cpu_percent": 15}]},
        )

        async def mock_execute(tool_name: str, params: dict) -> Any:
            if tool_name == "system.get_snapshot":
                return snap_result
            return proc_result

        exc.execute = mock_execute

        result = run_async(_dispatch("status", "", None, _make_orchestrator(), exc))
        assert "CPU 42%" in result
        assert "RAM 60.0%" in result
        assert "GPU 30%" in result
        assert "chrome.exe" in result

    def test_search_performs_search_and_fetch_then_summarises(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("Search result summary.")
        exc = MagicMock()
        search_result = _tool_result(
            success=True,
            data={
                "results": [{"title": "Python", "url": "https://python.org", "summary": "Python"}]
            },
        )
        fetch_result = _tool_result(
            success=True,
            data={"extracted_text": "Python is a programming language.", "title": "Python"},
        )

        async def mock_execute(tool_name: str, params: dict) -> Any:
            if tool_name == "web.search":
                return search_result
            if tool_name == "web.fetch":
                return fetch_result
            raise AssertionError(f"unexpected tool {tool_name}")

        exc.execute = mock_execute

        result = run_async(_dispatch("search", "python async", None, orc, exc))
        assert result == "Search result summary."
        prompt = orc.run.call_args[0][0]
        assert "python async" in prompt
        assert "Python is a programming language." in prompt

    def test_search_no_results_does_not_call_orchestrator(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator()
        exc = _make_executor(success=True, data={"results": []})
        result = run_async(_dispatch("search", "nothing", None, orc, exc))
        assert "No search results" in result
        orc.run.assert_not_called()

    def test_remind_routes_to_orchestrator(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("Reminder set for 5pm.")
        exc = _make_executor(success=True, data={"reminders": []})
        result = run_async(_dispatch("remind", "dentist at 5pm tomorrow", None, orc, exc))
        assert "No new saved reminder" in result
        assert "Saved reminder:" not in result
        orc.run.assert_called_once()
        assert exc.execute.await_count == 2

    def test_remind_reports_only_a_new_persisted_record(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("Reminder set.")
        persisted = {
            "reminder_id": "rem_123456789abc",
            "message": "dentist appointment",
            "fire_utc": "2026-11-08T10:00:00+00:00",
        }
        exc = MagicMock()
        exc.execute = AsyncMock(
            side_effect=[
                _tool_result(True, {"reminders": []}),
                _tool_result(True, {"reminders": [persisted]}),
            ]
        )
        result = run_async(_dispatch("remind", "dentist tomorrow at 3:30pm", None, orc, exc))
        assert "dentist appointment" in result
        assert "2026-11-08T10:00:00+00:00 UTC" in result

    def test_remind_without_context_asks_for_details_without_running_model(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator()
        result = run_async(_dispatch("remind", "", None, orc, _make_executor()))
        assert "No reminder was saved" in result
        orc.run.assert_not_called()

    def test_summarise_fetches_url_then_orchestrator(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator("This page is about Python.")
        exc = _make_executor(
            success=True,
            data={"title": "Welcome", "extracted_text": "Python is a language."},
        )
        result = run_async(_dispatch("summarise", "https://python.org", None, orc, exc))
        assert "Python" in result
        assert exc.execute.call_args[0][0] == "web.fetch"
        prompt = orc.run.call_args[0][0]
        assert "Python is a language." in prompt

    def test_summarise_no_url_returns_guidance(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator()
        exc = _make_executor()
        result = run_async(_dispatch("summarise", "", None, orc, exc))
        assert "No URL" in result
        orc.run.assert_not_called()
        exc.execute.assert_not_called()

    def test_summarise_fetch_failure_returns_error(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        orc = _make_orchestrator()
        exc = _make_executor(success=False, error="blocked")
        result = run_async(_dispatch("summarise", "https://example.com", None, orc, exc))
        assert "Could not fetch URL" in result
        orc.run.assert_not_called()

    def test_explain_uses_vision_capture_then_orchestrator(self) -> None:
        from tom.desktop.wheel_server import _dispatch

        exc = _make_executor(
            success=True,
            data={"active_window_title": "Visual Studio Code", "width": 1920, "height": 1080},
        )
        orc = _make_orchestrator("This is VS Code. You can edit files and run code.")
        result = run_async(_dispatch("explain", "", None, orc, exc))
        assert result != ""
        assert exc.execute.call_args[0][0] == "vision.capture"
        prompt = orc.run.call_args[0][0]
        assert "Visual Studio Code" in prompt


# ---------------------------------------------------------------------------
# Tests for _handle_wheel HTTP endpoint
# ---------------------------------------------------------------------------


class TestHandleWheelEndpoint:
    def _make_request(self, body: Any, app: dict | None = None) -> MagicMock:
        req = MagicMock()
        req.json = AsyncMock(return_value=body)
        req.app = app or {
            "orchestrator": _make_orchestrator("tool result"),
            "executor": _make_executor(),
        }
        return req

    def _app(self) -> dict:
        return {
            "orchestrator": _make_orchestrator("tool result"),
            "executor": _make_executor(),
        }

    def test_unknown_action_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = self._make_request({"action": "hack_the_planet", "context": "", "request_id": "1"})
        response = run_async(_handle_wheel(req))
        assert response.status == 400
        body = json.loads(response.body)
        assert body["ok"] is False
        assert "unknown action" in body["error"]

    def test_valid_action_returns_200(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = self._make_request({"action": "ask", "context": "hello", "request_id": "42"})
        response = run_async(_handle_wheel(req))
        assert response.status == 200
        body = json.loads(response.body)
        assert body["ok"] is True
        assert body["request_id"] == "42"

    def test_malformed_json_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = MagicMock()
        req.json = AsyncMock(side_effect=Exception("bad json"))
        req.app = self._app()
        response = run_async(_handle_wheel(req))
        assert response.status == 400
        body = json.loads(response.body)
        assert body["ok"] is False

    def test_non_object_json_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        response = run_async(_handle_wheel(self._make_request([1, 2, 3])))
        assert response.status == 400

    def test_request_id_echoed_in_response(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = self._make_request({"action": "note", "context": "my note", "request_id": "req-99"})
        response = run_async(_handle_wheel(req))
        body = json.loads(response.body)
        assert body["request_id"] == "req-99"

    def test_missing_or_invalid_request_id_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        for request_id in (None, "", "x" * 65, "bad id!"):
            req = self._make_request({"action": "ask", "context": "hi", "request_id": request_id})
            response = run_async(_handle_wheel(req))
            assert response.status == 400, request_id

    def test_invalid_region_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        for region in ([10, 20, 10, 40], [10, 20, 30], ["a", 2, 3, 4], [0, 0, 20000, 100]):
            req = self._make_request(
                {"action": "ocr", "context": "", "region": region, "request_id": "1"}
            )
            response = run_async(_handle_wheel(req))
            assert response.status == 400, region

    def test_oversized_context_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = self._make_request({"action": "ask", "context": "x" * 20_001, "request_id": "1"})
        response = run_async(_handle_wheel(req))
        assert response.status == 400

    def test_unknown_field_returns_400(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        req = self._make_request(
            {"action": "ask", "context": "hi", "request_id": "1", "extra": True}
        )
        response = run_async(_handle_wheel(req))
        assert response.status == 400

    def test_all_valid_action_names_accepted(self) -> None:
        from tom.desktop.wheel_server import _VALID_ACTIONS, _handle_wheel

        for action in _VALID_ACTIONS:
            req = self._make_request(
                {"action": action, "context": "ctx", "request_id": "1"},
                app=self._app(),
            )
            resp = run_async(_handle_wheel(req))
            assert resp.status == 200, f"action {action!r} unexpectedly rejected"

    def test_executor_error_returns_error_response(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        app = self._app()
        exc = MagicMock()
        exc.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        app["executor"] = exc
        req = self._make_request(
            {"action": "note", "context": "hi", "request_id": "5"},
            app=app,
        )
        response = run_async(_handle_wheel(req))
        body = json.loads(response.body)
        assert body["ok"] is False
        assert response.status == 500

    def test_permission_denied_returns_403(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel
        from tom.security.permissions import PermissionDeniedError

        app = self._app()
        exc = MagicMock()
        exc.execute = AsyncMock(side_effect=PermissionDeniedError("denied"))
        app["executor"] = exc
        req = self._make_request(
            {"action": "note", "context": "hi", "request_id": "5"},
            app=app,
        )
        response = run_async(_handle_wheel(req))
        assert response.status == 403

    def test_duplicate_inflight_request_returns_409(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        app = self._app()
        app["wheel_inflight"] = {"dup"}
        req = self._make_request(
            {"action": "ask", "context": "hi", "request_id": "dup"},
            app=app,
        )
        response = run_async(_handle_wheel(req))
        assert response.status == 409

    def test_cached_duplicate_request_returns_cached_response(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        app = self._app()
        cached_payload = {"ok": True, "result": "cached", "request_id": "dup", "error": None}
        app["wheel_cache"] = OrderedDict([("dup", (time.monotonic(), cached_payload, 200))])
        app["wheel_inflight"] = set()
        req = self._make_request(
            {"action": "ask", "context": "hi", "request_id": "dup"},
            app=app,
        )
        response = run_async(_handle_wheel(req))
        assert response.status == 200
        assert json.loads(response.body)["result"] == "cached"

    def test_busy_server_returns_503(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        app = self._app()
        app["wheel_sem"] = asyncio.Semaphore(0)
        req = self._make_request(
            {"action": "ask", "context": "hi", "request_id": "1"},
            app=app,
        )
        with patch("tom.desktop.wheel_server._BUSY_ACQUIRE_TIMEOUT_S", 0.01):
            response = run_async(_handle_wheel(req))
        assert response.status == 503

    def test_timeout_returns_504(self) -> None:
        from tom.desktop.wheel_server import _handle_wheel

        app = self._app()

        async def slow_run(prompt: str) -> str:
            await asyncio.sleep(1)
            return "late"

        app["orchestrator"].run = slow_run
        req = self._make_request(
            {"action": "ask", "context": "hi", "request_id": "1"},
            app=app,
        )
        with patch("tom.desktop.wheel_server._TIMEOUT_S", 0.01):
            response = run_async(_handle_wheel(req))
        assert response.status == 504


# ---------------------------------------------------------------------------
# Tests for build_app
# ---------------------------------------------------------------------------


class TestBuildApp:
    def test_app_has_orchestrator_and_executor(self) -> None:
        from tom.desktop.wheel_server import build_app

        orc = _make_orchestrator()
        exc = _make_executor()
        app = build_app(orc, exc)
        assert app["orchestrator"] is orc
        assert app["executor"] is exc

    def test_app_has_wheel_and_health_routes(self) -> None:
        from tom.desktop.wheel_server import build_app

        app = build_app(_make_orchestrator(), _make_executor())
        routes = [str(r.resource) for r in app.router.routes()]
        assert any("/wheel" in r for r in routes)
        assert any("/health" in r for r in routes)

    def test_app_has_bounded_state(self) -> None:
        from tom.desktop.wheel_server import build_app

        app = build_app(_make_orchestrator(), _make_executor())
        assert "wheel_cache" in app
        assert "wheel_inflight" in app
        assert "wheel_sem" in app


class TestWheelConfirmation:
    def test_uses_explicit_wheel_approval(self) -> None:
        from tom.desktop.wheel_server import _WHEEL_APPROVAL, WheelConfirmationHook
        from tom.security.confirmation import ConfirmationRequest

        hook = WheelConfirmationHook()
        hook._audit.log_decision = MagicMock()
        request = ConfirmationRequest(tool_name="personal.note", params={"action": "create"})
        token = _WHEEL_APPROVAL.set(True)
        try:
            assert run_async(hook.request_confirmation(request)) is True
        finally:
            _WHEEL_APPROVAL.reset(token)
        hook._audit.log_decision.assert_called_once()

    def test_missing_wheel_approval_defaults_to_console_denial(self) -> None:
        from tom.desktop.wheel_server import WheelConfirmationHook
        from tom.security.confirmation import ConfirmationRequest

        hook = WheelConfirmationHook()
        hook._console.request_confirmation = AsyncMock(return_value=False)
        request = ConfirmationRequest(tool_name="personal.note", params={"action": "create"})
        assert run_async(hook.request_confirmation(request)) is False

    def test_explicit_wheel_denial_is_audited_and_denied(self) -> None:
        from tom.desktop.wheel_server import _WHEEL_APPROVAL, WheelConfirmationHook
        from tom.security.confirmation import ConfirmationRequest

        hook = WheelConfirmationHook()
        hook._audit.log_decision = MagicMock()
        request = ConfirmationRequest(tool_name="personal.reminder", params={"action": "create"})
        token = _WHEEL_APPROVAL.set(False)
        try:
            assert run_async(hook.request_confirmation(request)) is False
        finally:
            _WHEEL_APPROVAL.reset(token)
        hook._audit.log_decision.assert_called_once()
        assert hook._audit.log_decision.call_args.kwargs["decision"] == "DENIED"
