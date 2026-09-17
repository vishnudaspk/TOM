"""TOM Tool System — Definitions, Models, Decorators, and Central Registry.

This module provides the core abstractions for deterministic, typed, and permissioned
tools in TOM. Tools are completely decoupled from LLMs and agent frameworks; they are
standard typed Python functions wrapped with Pydantic v2 schemas and permission tiers.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    field_serializer,
    field_validator,
)

from tom.security.permissions import PermissionLevel


class ToolError(Exception):
    """Base exception for all tool subsystem errors."""


class ToolAlreadyExistsError(ToolError, ValueError):
    """Raised when registering a tool whose name is already registered."""


class ToolNotFoundError(ToolError, KeyError):
    """Raised when looking up a tool name that does not exist in the registry."""


class ToolValidationError(ToolError, ValueError):
    """Raised when a tool definition or argument validation fails."""


class ToolResult(BaseModel):
    """Standardized structured outcome of any tool execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    success: bool = Field(description="Whether the tool execution succeeded")
    data: Any = Field(default=None, description="Structured output payload on success")
    error: str | None = Field(default=None, description="Error message or details on failure")
    execution_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Total tool execution duration in milliseconds",
    )

    @classmethod
    def ok(cls, data: Any = None, execution_time_ms: float = 0.0) -> ToolResult:
        """Create a successful tool execution result."""
        return cls(success=True, data=data, error=None, execution_time_ms=execution_time_ms)

    @classmethod
    def fail(
        cls,
        error: str,
        execution_time_ms: float = 0.0,
        data: Any = None,
    ) -> ToolResult:
        """Create a failed tool execution result."""
        return cls(success=False, data=data, error=error, execution_time_ms=execution_time_ms)


class ToolDefinition(BaseModel):
    """Authoritative metadata and schema definition for a registered tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    name: str = Field(
        min_length=1,
        description="Unique tool identifier, e.g. 'system.cpu_info'",
    )
    description: str = Field(
        min_length=1,
        description="Human and LLM-readable summary of the tool's purpose",
    )
    category: str = Field(
        min_length=1,
        description="Categorical namespace, e.g. 'system', 'files', 'computer'",
    )
    input_schema: Any = Field(
        description="Pydantic model class, type, or JSON schema dict defining input arguments",
    )
    output_schema: Any = Field(
        default=None,
        description="Pydantic model class, type, or JSON schema dict defining tool output",
    )
    permission_level: PermissionLevel = Field(
        default=PermissionLevel.SAFE,
        description="Authoritative permission tier (SAFE, ASK_USER, BLOCK)",
    )
    timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description="Maximum execution timeout deadline in seconds",
    )
    handler: Callable[..., Any] | None = Field(
        default=None,
        exclude=True,
        description="Callable implementation function",
    )

    @field_validator("permission_level", mode="before")
    @classmethod
    def _validate_permission_level(cls, v: Any) -> PermissionLevel:
        if isinstance(v, PermissionLevel):
            return v
        if isinstance(v, str):
            return PermissionLevel.from_str(v)
        raise ValueError(f"Invalid permission level: {v}")

    @field_validator("name", "category", "description", mode="before")
    @classmethod
    def _validate_non_empty_strings(cls, v: Any) -> str:
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("Value cannot be empty or whitespace-only")
            return cleaned
        raise ValueError(f"Expected string, got {type(v).__name__}")

    @field_serializer("input_schema", mode="plain", when_used="json")
    def _serialize_input_schema(self, v: Any) -> Any:
        if isinstance(v, type) and issubclass(v, BaseModel):
            return v.model_json_schema()
        if isinstance(v, dict):
            return v
        return {"type": "object", "properties": {}}

    @field_serializer("output_schema", mode="plain", when_used="json")
    def _serialize_output_schema(self, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, type) and issubclass(v, BaseModel):
            return v.model_json_schema()
        if isinstance(v, dict):
            return v
        if isinstance(v, type):
            return {"type": v.__name__}
        return {"type": str(v)}

    def get_input_json_schema(self) -> dict[str, Any]:
        """Return the JSON schema representation of tool input arguments."""
        if isinstance(self.input_schema, type) and issubclass(self.input_schema, BaseModel):
            return self.input_schema.model_json_schema()
        if isinstance(self.input_schema, dict):
            return self.input_schema
        return {"type": "object", "properties": {}}

    def get_output_json_schema(self) -> dict[str, Any] | None:
        """Return the JSON schema representation of tool output, if available."""
        if self.output_schema is None:
            return None
        if isinstance(self.output_schema, type) and issubclass(self.output_schema, BaseModel):
            return self.output_schema.model_json_schema()
        if isinstance(self.output_schema, dict):
            return self.output_schema
        if isinstance(self.output_schema, type):
            return {"type": self.output_schema.__name__}
        return {"type": str(self.output_schema)}

    def to_json_schema(self) -> dict[str, Any]:
        """Export tool definition as OpenAI/Qwen compatible function tool specification."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_input_json_schema(),
            },
        }

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        category: str | None = None,
        input_schema: type[BaseModel] | dict[str, Any] | None = None,
        output_schema: type[BaseModel] | type | dict[str, Any] | None = None,
        permission_level: PermissionLevel | str = PermissionLevel.SAFE,
        timeout_seconds: float = 15.0,
    ) -> ToolDefinition:
        """Extract tool metadata and schemas from a callable function."""
        # 1. Name
        resolved_name = name or fn.__name__

        # 2. Description
        resolved_desc = description or inspect.getdoc(fn) or resolved_name
        resolved_desc = resolved_desc.strip()
        if not resolved_desc:
            resolved_desc = resolved_name

        # 3. Category
        if category:
            resolved_cat = category
        elif "." in resolved_name:
            resolved_cat = resolved_name.split(".", 1)[0]
        else:
            resolved_cat = "general"

        # 4. Input schema extraction
        sig = inspect.signature(fn)
        if input_schema is not None:
            resolved_input: type[BaseModel] | dict[str, Any] = input_schema
        else:
            params = [p for p in sig.parameters.values() if p.name not in ("self", "cls")]
            if (
                len(params) == 1
                and isinstance(params[0].annotation, type)
                and issubclass(params[0].annotation, BaseModel)
            ):
                resolved_input = params[0].annotation
            elif len(params) == 0:
                # Empty schema model
                model_name = f"{''.join(word.capitalize() for word in resolved_name.replace('.', '_').split('_'))}Input"
                resolved_input = create_model(model_name)
            else:
                fields: dict[str, Any] = {}
                for param in params:
                    # Skip 'ctx' or 'context' parameter if it's auxiliary
                    if param.name in ("ctx", "context") and len(params) > 1:
                        continue
                    ann = (
                        param.annotation if param.annotation is not inspect.Parameter.empty else Any
                    )
                    default = param.default if param.default is not inspect.Parameter.empty else ...
                    fields[param.name] = (ann, default)
                model_name = f"{''.join(word.capitalize() for word in resolved_name.replace('.', '_').split('_'))}Input"
                resolved_input = create_model(model_name, **fields)

        # 5. Output schema extraction
        if output_schema is not None:
            resolved_output = output_schema
        elif (
            sig.return_annotation is not inspect.Signature.empty
            and sig.return_annotation is not None
        ):
            resolved_output = sig.return_annotation
        else:
            resolved_output = None

        return cls(
            name=resolved_name,
            description=resolved_desc,
            category=resolved_cat,
            input_schema=resolved_input,
            output_schema=resolved_output,
            permission_level=permission_level,
            timeout_seconds=timeout_seconds,
            handler=fn,
        )


class ToolRegistry:
    """Central repository for deterministic tool discovery, registration, and inspection."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        tool_or_fn: ToolDefinition | Callable[..., Any],
        replace: bool = False,
    ) -> ToolDefinition:
        """Register a tool definition or a callable with attached metadata.

        Args:
            tool_or_fn: A ToolDefinition instance, a @tool decorated callable, or a function.
            replace: If True, overwrite an existing registration with the same name.
                     If False (default), raise ToolAlreadyExistsError.

        Returns:
            The registered ToolDefinition.
        """
        if isinstance(tool_or_fn, ToolDefinition):
            tool_def = tool_or_fn
        elif hasattr(tool_or_fn, "__tool_def__") and isinstance(
            tool_or_fn.__tool_def__, ToolDefinition
        ):
            tool_def = tool_or_fn.__tool_def__
        elif callable(tool_or_fn):
            tool_def = ToolDefinition.from_function(tool_or_fn)
        else:
            raise ToolValidationError(
                f"Expected ToolDefinition or callable, got {type(tool_or_fn).__name__}"
            )

        if tool_def.name in self._tools and not replace:
            raise ToolAlreadyExistsError(
                f"Tool '{tool_def.name}' is already registered in this registry"
            )

        self._tools[tool_def.name] = tool_def
        return tool_def

    def get(self, name: str) -> ToolDefinition:
        """Retrieve a registered tool by name.

        Raises:
            ToolNotFoundError: If the tool does not exist.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered")
        return self._tools[name]

    def get_optional(self, name: str) -> ToolDefinition | None:
        """Retrieve a registered tool by name, or None if absent."""
        return self._tools.get(name)

    def unregister(self, name: str) -> ToolDefinition:
        """Remove a tool from the registry.

        Raises:
            ToolNotFoundError: If the tool does not exist.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Cannot unregister unknown tool '{name}'")
        return self._tools.pop(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __getitem__(self, name: str) -> ToolDefinition:
        return self.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def list_tools(self, category: str | None = None) -> list[ToolDefinition]:
        """List registered tools, optionally filtered by category."""
        if category is not None:
            return [t for t in self._tools.values() if t.category == category]
        return list(self._tools.values())

    def list_categories(self) -> list[str]:
        """Return a sorted list of all distinct tool categories."""
        return sorted({t.category for t in self._tools.values()})

    def clear(self) -> None:
        """Remove all registered tools from the registry."""
        self._tools.clear()

    def export_json_schemas(self, category: str | None = None) -> list[dict[str, Any]]:
        """Export tools in OpenAI/Qwen compatible JSON schema function tool specification."""
        return [t.to_json_schema() for t in self.list_tools(category=category)]

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        category: str | None = None,
        input_schema: type[BaseModel] | dict[str, Any] | None = None,
        output_schema: type[BaseModel] | type | dict[str, Any] | None = None,
        permission_level: PermissionLevel | str = PermissionLevel.SAFE,
        timeout_seconds: float = 15.0,
        replace: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to declare and immediately register a tool into this registry."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_def = ToolDefinition.from_function(
                fn,
                name=name,
                description=description,
                category=category,
                input_schema=input_schema,
                output_schema=output_schema,
                permission_level=permission_level,
                timeout_seconds=timeout_seconds,
            )
            self.register(tool_def, replace=replace)
            fn.__tool_def__ = tool_def  # type: ignore[attr-defined]
            fn.tool_definition = tool_def  # type: ignore[attr-defined]
            return fn

        return decorator


# Global default registry instance
default_registry = ToolRegistry()


def tool(
    name_or_fn: str | Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    input_schema: type[BaseModel] | dict[str, Any] | None = None,
    output_schema: type[BaseModel] | type | dict[str, Any] | None = None,
    permission_level: PermissionLevel | str = PermissionLevel.SAFE,
    timeout_seconds: float = 15.0,
    registry: ToolRegistry | None = None,
    replace: bool = False,
) -> Any:
    """Decorator to declare a typed deterministic tool.

    Usage as a bare decorator:
        @tool
        def ping() -> dict:
            return {"status": "ok"}

    Usage with parameters:
        @tool(
            name="system.cpu_info",
            description="Query host CPU utilization and frequency",
            category="system",
            permission_level=PermissionLevel.SAFE,
        )
        async def cpu_info(params: CpuInfoInput) -> CpuInfoOutput:
            ...
    """

    def make_decorator(
        dec_name: str | None,
        dec_desc: str | None,
        dec_cat: str | None,
        dec_in: type[BaseModel] | dict[str, Any] | None,
        dec_out: type[BaseModel] | type | dict[str, Any] | None,
        dec_perm: PermissionLevel | str,
        dec_timeout: float,
        dec_reg: ToolRegistry | None,
        dec_replace: bool,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_def = ToolDefinition.from_function(
                fn,
                name=dec_name,
                description=dec_desc,
                category=dec_cat,
                input_schema=dec_in,
                output_schema=dec_out,
                permission_level=dec_perm,
                timeout_seconds=dec_timeout,
            )
            fn.__tool_def__ = tool_def  # type: ignore[attr-defined]
            fn.tool_definition = tool_def  # type: ignore[attr-defined]

            if dec_reg is not None:
                dec_reg.register(tool_def, replace=dec_replace)

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            wrapper.__tool_def__ = tool_def  # type: ignore[attr-defined]
            wrapper.tool_definition = tool_def  # type: ignore[attr-defined]
            return wrapper

        return decorator

    # Handle bare decorator: @tool
    if callable(name_or_fn):
        return make_decorator(
            dec_name=name,
            dec_desc=description,
            dec_cat=category,
            dec_in=input_schema,
            dec_out=output_schema,
            dec_perm=permission_level,
            dec_timeout=timeout_seconds,
            dec_reg=registry,
            dec_replace=replace,
        )(name_or_fn)

    # Handle parameterized decorator: @tool(...)
    resolved_name = name or (name_or_fn if isinstance(name_or_fn, str) else None)
    return make_decorator(
        dec_name=resolved_name,
        dec_desc=description,
        dec_cat=category,
        dec_in=input_schema,
        dec_out=output_schema,
        dec_perm=permission_level,
        dec_timeout=timeout_seconds,
        dec_reg=registry,
        dec_replace=replace,
    )
