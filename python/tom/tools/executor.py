"""TOM Tool Execution Engine.

This module coordinates deterministic tool execution:
1. Tool lookup via ToolRegistry.
2. Pre-execution security check via PermissionEngine (SAFE / ASK_USER / BLOCK).
3. Interactive user confirmation via ConfirmationHook when required.
4. Input argument validation via Pydantic v2 schemas.
5. Timeout enforcement (ToolsConfig.default_timeout_seconds) and cancellation handling.
6. Execution duration timing using monotonic clock.
7. Structured ToolResult packaging.
8. Secret scrubbing of confirmation parameters, results, and errors.
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
from tom.security.secrets import (
    SecretScrubber,
    get_default_scrubber,
)
from tom.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    default_registry,
)


async def _drain_cancelled_task(task: asyncio.Task[Any], timeout: float = 0.5) -> None:
    """Safely cancel and drain a child task with bounded timeout to avoid hanging indefinitely."""
    if not task.done():
        task.cancel()
        try:
            await asyncio.wait({task}, timeout=timeout)
        except (asyncio.CancelledError, Exception):
            pass


class ToolExecutor:
    """Orchestrates validation, security checks, timeouts, and execution of deterministic tools."""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        permission_engine: PermissionEngine | None = None,
        confirmation_hook: ConfirmationHook | None = None,
        config: ToolsConfig | None = None,
        secret_scrubber: SecretScrubber | None = None,
    ) -> None:
        self.registry = registry or default_registry
        self.permission_engine = permission_engine or PermissionEngine()
        self.confirmation_hook = confirmation_hook or ConsoleConfirmationHook()
        self.config = config or ToolsConfig()
        self.secret_scrubber = secret_scrubber or get_default_scrubber()

    def _safe_scrub_string(self, text: str) -> str:
        """Scrub secrets from text with fail-safe fallback to prevent credential leakage."""
        try:
            return self.secret_scrubber.scrub_string(text)
        except Exception:
            return "***REDACTION_ERROR***"

    def _safe_scrub_model(self, model: BaseModel) -> Any:
        """Scrub secrets from a Pydantic model while preserving its model class if possible."""
        try:
            scrubbed_dict = self.secret_scrubber.scrub_object(model.model_dump())
            try:
                return type(model).model_validate(scrubbed_dict)
            except Exception:
                return scrubbed_dict
        except Exception:
            return "***REDACTION_ERROR***"

    def _safe_scrub_data(self, data: Any) -> Any:
        """Scrub secrets from result data, preserving BaseModel instances if possible."""
        if isinstance(data, BaseModel):
            return self._safe_scrub_model(data)
        return self._safe_scrub_object(data)

    def _safe_scrub_object(self, obj: Any) -> Any:
        """Scrub secrets from object with fail-safe fallback to prevent credential leakage."""
        try:
            return self.secret_scrubber.scrub_object(obj)
        except Exception:
            return "***REDACTION_ERROR***"

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any] | BaseModel | None = None,
        timeout_seconds: float | None = None,
        resource_priority: int = 1,
    ) -> ToolResult:
        """Execute a registered tool under the authoritative security and validation pipeline.

        Args:
            tool_name: Registered name of the tool to invoke.
            params: Tool input arguments as a dictionary or Pydantic model.
            timeout_seconds: Optional per-invocation timeout override.
            resource_priority: TaskPriority numeric value (lower = higher priority) for resource arbitration.

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
            # Scrub parameters for confirmation display only; preserve original for execution
            scrubbed_params = self._safe_scrub_object(raw_params)
            req = ConfirmationRequest(
                tool_name=tool_def.name,
                description=tool_def.description,
                params=scrubbed_params,
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
                scrubbed_error = self._safe_scrub_string(
                    f"Argument validation failed for tool '{tool_name}': {e}"
                )
                return ToolResult.fail(
                    error=scrubbed_error,
                    execution_time_ms=elapsed_ms,
                )

        # 5. Handler Argument Binding
        call_args: tuple[Any, ...] = ()
        call_kwargs: dict[str, Any] = {}

        # Resolve annotations (handle string forward references from __future__.annotations)
        try:
            sig = inspect.signature(tool_def.handler, eval_str=True)
        except TypeError:
            # Python < 3.10 or eval_str not supported
            sig = inspect.signature(tool_def.handler)
        sig_params = list(sig.parameters.values())

        # Find the parameter that should receive the validated model (if any)
        # Match by annotation against tool_def.input_schema
        model_param = None
        other_params = []
        input_schema = tool_def.input_schema

        for p in sig_params:
            is_model_param = False
            if validated_model is not None and input_schema is not None:
                # Check if annotation matches input_schema (handle string annotations)
                annotation = p.annotation
                if isinstance(annotation, str):
                    # String annotation - compare names
                    if annotation == input_schema.__name__:
                        is_model_param = True
                elif annotation is input_schema:
                    is_model_param = True
                elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    # Fallback: any BaseModel annotation
                    is_model_param = True

            if is_model_param:
                model_param = p
            else:
                other_params.append(p)

        if model_param is not None:
            # Handler has a BaseModel parameter - pass validated model to it
            call_kwargs[model_param.name] = validated_model
            # Other parameters get their values from raw_params or defaults
            for p in other_params:
                if p.name in raw_params:
                    call_kwargs[p.name] = raw_params[p.name]
                elif p.default is not inspect.Parameter.empty:
                    # Use default value (don't include in call_kwargs)
                    pass
                else:
                    # Required parameter not provided in raw_params
                    pass
        elif len(sig_params) == 1:
            # Single parameter, no model annotation - pass as positional or kwarg
            param_obj = sig_params[0]
            if validated_model is not None:
                if (
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
            # Multiple parameters, no model annotation - pass raw_params as kwargs
            call_kwargs = raw_params

        # 6. Inject resource_priority if handler accepts it
        if "resource_priority" in sig.parameters:
            call_kwargs["resource_priority"] = resource_priority

        # 7. Timeout and Cancellation-Safe Invocation
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
            await _drain_cancelled_task(task, timeout=0.5)
            elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
            scrubbed_error = self._safe_scrub_string(
                f"Tool '{tool_name}' timed out after {effective_timeout} seconds"
            )
            return ToolResult.fail(
                error=scrubbed_error,
                execution_time_ms=elapsed_ms,
            )
        except asyncio.CancelledError:
            await _drain_cancelled_task(task, timeout=0.5)
            raise
        except Exception as e:
            elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
            scrubbed_error = self._safe_scrub_string(f"Tool '{tool_name}' execution failed: {e}")
            return ToolResult.fail(
                error=scrubbed_error,
                execution_time_ms=elapsed_ms,
            )

        # 7. Result Packaging and Timing
        elapsed_ms = max((time.perf_counter() - start_time) * 1000.0, 0.001)
        if isinstance(raw_result, ToolResult):
            # Scrub data and error fields of existing ToolResult without mutating it
            scrubbed_data = self._safe_scrub_data(raw_result.data)
            scrubbed_error = (
                self._safe_scrub_string(raw_result.error) if raw_result.error is not None else None
            )
            return ToolResult(
                success=raw_result.success,
                data=scrubbed_data,
                error=scrubbed_error,
                execution_time_ms=elapsed_ms,
            )

        scrubbed_result = self._safe_scrub_data(raw_result)
        return ToolResult.ok(data=scrubbed_result, execution_time_ms=elapsed_ms)
