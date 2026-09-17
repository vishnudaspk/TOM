"""TOM Tool Execution Engine.

This module coordinates deterministic tool execution:
1. Tool lookup via ToolRegistry.
2. Pre-execution security check via PermissionEngine (SAFE / ASK_USER / BLOCK).
3. Interactive user confirmation via ConfirmationHook when required.
4. Input argument validation via Pydantic v2 schemas.
5. Timeout enforcement (ToolsConfig.default_timeout_seconds) and cancellation handling.
6. Execution duration timing using monotonic clock.
7. Structured ToolResult packaging.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from tom.schemas.config import ToolsConfig
from tom.security.confirmation import (
    ConfirmationHook,
    ConfirmationRequest,
    ConsoleConfirmationHook,
)
from tom.security.permissions import (
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    default_registry,
)


class ToolExecutor:
    """Orchestrates validation, security checks, timeouts, and execution of deterministic tools."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        permission_engine: PermissionEngine | None = None,
        confirmation_hook: ConfirmationHook | None = None,
        config: ToolsConfig | None = None,
    ) -> None:
        self.registry = registry or default_registry
        self.permission_engine = permission_engine or PermissionEngine()
        self.confirmation_hook = confirmation_hook or ConsoleConfirmationHook()
        self.config = config or ToolsConfig()

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any] | BaseModel | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        """Execute a registered tool under the authoritative security and validation pipeline.

        Args:
            tool_name: Registered name of the tool to invoke.
            params: Tool input arguments as a dictionary or Pydantic model.
            timeout_seconds: Optional per-invocation timeout override.

        Returns:
            Structured ToolResult containing success status, output data, error, and timing.

        Raises:
            ToolNotFoundError: If the tool is not registered.
            PermissionDeniedError: If the tool is blocked or confirmation is denied.
            ToolValidationError: If the tool has no callable handler.
            asyncio.CancelledError: If the caller cancels execution.
        """
        start_time = time.perf_counter()

        # 1. Registry Lookup (propagates ToolNotFoundError if missing)
        tool_def: ToolDefinition = self.registry.get(tool_name)
        if tool_def.handler is None:
            raise ToolValidationError(f"Tool '{tool_name}' has no callable handler registered")

        # Normalize raw parameters dictionary
        if params is None:
            raw_params: dict[str, Any] = {}
        elif isinstance(params, BaseModel):
            raw_params = params.model_dump()
        elif isinstance(params, dict):
            raw_params = params
        else:
            raw_params = {"value": params}

        # 2. Centralized Pre-Execution Permission Evaluation
        decision = self.permission_engine.evaluate(tool_def, params=raw_params)

        if not decision.allowed or decision.permission_level == PermissionLevel.BLOCK:
            raise PermissionDeniedError(
                f"Tool '{tool_name}' execution prohibited by security policy: {decision.reason}"
            )

        # 3. Interactive Confirmation if ASK_USER
        if decision.requires_confirmation:
            confirmation_timeout = self.permission_engine.config.confirmation_timeout_seconds
            req = ConfirmationRequest(
                tool_name=tool_def.name,
                description=tool_def.description,
                params=raw_params,
                timeout_seconds=confirmation_timeout,
            )
            confirmed = await self.confirmation_hook.request_confirmation(req)
            if not confirmed:
                raise PermissionDeniedError(
                    f"User confirmation denied for tool '{tool_name}': {decision.reason}"
                )

        # 4. Input Argument Validation
        validated_model: BaseModel | None = None
        if isinstance(tool_def.input_schema, type) and issubclass(tool_def.input_schema, BaseModel):
            try:
                validated_model = tool_def.input_schema.model_validate(raw_params)
            except ValidationError as e:
                elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
                return ToolResult.fail(
                    error=f"Argument validation failed for tool '{tool_name}': {e}",
                    execution_time_ms=elapsed_ms,
                )

        # 5. Handler Argument Binding
        call_args: tuple[Any, ...] = ()
        call_kwargs: dict[str, Any] = {}
        sig = inspect.signature(tool_def.handler)
        params_count = len(sig.parameters)

        if params_count == 0:
            call_args = ()
            call_kwargs = {}
        elif params_count == 1:
            param_obj = list(sig.parameters.values())[0]
            if validated_model is not None:
                if isinstance(param_obj.annotation, type) and issubclass(
                    param_obj.annotation, BaseModel
                ):
                    call_args = (validated_model,)
                elif (
                    param_obj.name in validated_model.__dict__
                    and len(type(validated_model).model_fields) == 1
                ):
                    call_kwargs = validated_model.model_dump()
                else:
                    call_args = (validated_model,)
            else:
                if param_obj.name in raw_params and len(raw_params) == 1:
                    call_kwargs = raw_params
                else:
                    call_args = (raw_params,)
        else:
            if validated_model is not None:
                call_kwargs = validated_model.model_dump()
            else:
                call_kwargs = raw_params

        # 6. Timeout and Cancellation-Safe Invocation
        if timeout_seconds is not None:
            effective_timeout = timeout_seconds
        elif self.config and self.config.default_timeout_seconds != 15.0:
            effective_timeout = self.config.default_timeout_seconds
        elif tool_def.timeout_seconds is not None:
            effective_timeout = tool_def.timeout_seconds
        else:
            effective_timeout = self.config.default_timeout_seconds

        is_coroutine_func = inspect.iscoroutinefunction(
            tool_def.handler
        ) or inspect.iscoroutinefunction(getattr(tool_def.handler, "__wrapped__", None))

        if is_coroutine_func:
            task_coro = tool_def.handler(*call_args, **call_kwargs)
        else:
            task_coro = asyncio.to_thread(tool_def.handler, *call_args, **call_kwargs)

        task = asyncio.create_task(task_coro)

        try:
            raw_result = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=effective_timeout,
            )
        except TimeoutError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
            return ToolResult.fail(
                error=f"Tool '{tool_name}' timed out after {effective_timeout} seconds",
                execution_time_ms=elapsed_ms,
            )
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        except Exception as e:
            elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
            return ToolResult.fail(
                error=f"Tool '{tool_name}' execution failed: {e}",
                execution_time_ms=elapsed_ms,
            )

        # 7. Result Packaging and Timing
        elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
        if isinstance(raw_result, ToolResult):
            raw_result.execution_time_ms = elapsed_ms
            return raw_result

        return ToolResult.ok(data=raw_result, execution_time_ms=elapsed_ms)
