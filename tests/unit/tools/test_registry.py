"""Unit tests for TOM Tool Definition, Base Models, and Central Registry."""

import inspect
import json

import pytest
from pydantic import BaseModel, ValidationError
from tom.tools.registry import (
    PermissionLevel,
    ToolAlreadyExistsError,
    ToolDefinition,
    ToolError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    tool,
)

# ---------------------------------------------------------------------------
# Sample Schema Fixtures
# ---------------------------------------------------------------------------


class MockCpuInput(BaseModel):
    include_per_core: bool = False


class MockCpuOutput(BaseModel):
    usage_percent: float
    core_count: int


class MockFileInput(BaseModel):
    path: str
    max_bytes: int = 5000


class MockFileOutput(BaseModel):
    content: str
    truncated: bool = False


# ---------------------------------------------------------------------------
# Test PermissionLevel
# ---------------------------------------------------------------------------


class TestPermissionLevel:
    """Test 3-tier permission model classification and string normalization."""

    def test_enum_members_and_values(self) -> None:
        assert PermissionLevel.SAFE == "SAFE"
        assert PermissionLevel.ASK_USER == "ASK_USER"
        assert PermissionLevel.BLOCK == "BLOCK"

    def test_str_comparison(self) -> None:
        assert PermissionLevel.SAFE == "SAFE"
        assert PermissionLevel.ASK_USER == "ASK_USER"
        assert PermissionLevel.BLOCK == "BLOCK"

    def test_from_str_exact(self) -> None:
        assert PermissionLevel.from_str("SAFE") is PermissionLevel.SAFE
        assert PermissionLevel.from_str("ASK_USER") is PermissionLevel.ASK_USER
        assert PermissionLevel.from_str("BLOCK") is PermissionLevel.BLOCK

    def test_from_str_case_insensitive_and_trimmed(self) -> None:
        assert PermissionLevel.from_str(" safe ") is PermissionLevel.SAFE
        assert PermissionLevel.from_str("ask_user") is PermissionLevel.ASK_USER
        assert PermissionLevel.from_str("block") is PermissionLevel.BLOCK

    def test_from_str_normalizes_ask_alias(self) -> None:
        assert PermissionLevel.from_str("ASK") is PermissionLevel.ASK_USER
        assert PermissionLevel.from_str("ask") is PermissionLevel.ASK_USER

    def test_from_str_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            PermissionLevel.from_str("INVALID_LEVEL")


# ---------------------------------------------------------------------------
# Test ToolResult
# ---------------------------------------------------------------------------


class TestToolResult:
    """Test structured tool execution outcome container."""

    def test_ok_factory(self) -> None:
        res = ToolResult.ok(data={"status": "ready"}, execution_time_ms=1.23)
        assert res.success is True
        assert res.data == {"status": "ready"}
        assert res.error is None
        assert res.execution_time_ms == 1.23

    def test_fail_factory(self) -> None:
        res = ToolResult.fail(error="Disk full", execution_time_ms=0.45)
        assert res.success is False
        assert res.data is None
        assert res.error == "Disk full"
        assert res.execution_time_ms == 0.45

    def test_fail_with_data(self) -> None:
        res = ToolResult.fail(error="Partial failure", data={"items_done": 2})
        assert res.success is False
        assert res.data == {"items_done": 2}
        assert res.error == "Partial failure"

    def test_serialization(self) -> None:
        res = ToolResult.ok(
            data=MockCpuOutput(usage_percent=42.5, core_count=8), execution_time_ms=2.5
        )
        dumped = res.model_dump()
        assert dumped["success"] is True
        assert dumped["data"] == {"usage_percent": 42.5, "core_count": 8}
        assert dumped["error"] is None
        assert dumped["execution_time_ms"] == 2.5

        json_str = res.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["success"] is True
        assert parsed["data"]["core_count"] == 8

    def test_negative_execution_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolResult(success=True, execution_time_ms=-1.0)


# ---------------------------------------------------------------------------
# Test ToolDefinition
# ---------------------------------------------------------------------------


class TestToolDefinition:
    """Test tool metadata definition, schema extraction, and serialization."""

    def test_direct_instantiation(self) -> None:
        tool_def = ToolDefinition(
            name="system.cpu_info",
            description="Query host CPU utilization and frequency",
            category="system",
            input_schema=MockCpuInput,
            output_schema=MockCpuOutput,
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=10.0,
        )
        assert tool_def.name == "system.cpu_info"
        assert tool_def.description == "Query host CPU utilization and frequency"
        assert tool_def.category == "system"
        assert tool_def.input_schema is MockCpuInput
        assert tool_def.output_schema is MockCpuOutput
        assert tool_def.permission_level is PermissionLevel.SAFE
        assert tool_def.timeout_seconds == 10.0

    def test_defaults(self) -> None:
        tool_def = ToolDefinition(
            name="system.ping",
            description="Ping host",
            category="system",
            input_schema={"type": "object"},
        )
        assert tool_def.permission_level is PermissionLevel.SAFE
        assert tool_def.timeout_seconds == 15.0
        assert tool_def.output_schema is None

    def test_permission_level_normalization_on_init(self) -> None:
        tool_def = ToolDefinition(
            name="files.delete",
            description="Delete file",
            category="files",
            input_schema={"type": "object"},
            permission_level="ASK",  # type: ignore[arg-type]
        )
        assert tool_def.permission_level is PermissionLevel.ASK_USER

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="",
                description="desc",
                category="cat",
                input_schema={"type": "object"},
            )

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="   ",
                description="desc",
                category="cat",
                input_schema={"type": "object"},
            )

    def test_non_positive_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolDefinition(
                name="system.ping",
                description="desc",
                category="system",
                input_schema={"type": "object"},
                timeout_seconds=0.0,
            )

    def test_json_schema_export(self) -> None:
        tool_def = ToolDefinition(
            name="files.read",
            description="Read file contents safely",
            category="files",
            input_schema=MockFileInput,
            output_schema=MockFileOutput,
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=5.0,
        )
        schema = tool_def.to_json_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "files.read"
        assert schema["function"]["description"] == "Read file contents safely"
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert "path" in params["properties"]
        assert "max_bytes" in params["properties"]
        assert params["required"] == ["path"]

    def test_model_dump_json_clean_serialization(self) -> None:
        tool_def = ToolDefinition(
            name="system.cpu",
            description="CPU metrics",
            category="system",
            input_schema=MockCpuInput,
            output_schema=MockCpuOutput,
        )
        json_str = tool_def.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["name"] == "system.cpu"
        assert parsed["input_schema"]["type"] == "object"
        assert "include_per_core" in parsed["input_schema"]["properties"]
        assert parsed["output_schema"]["type"] == "object"


# ---------------------------------------------------------------------------
# Test @tool Decorator
# ---------------------------------------------------------------------------


class TestToolDecorator:
    """Test @tool declaration with automated schema extraction."""

    def test_decorated_async_function_with_pydantic_model(self) -> None:
        @tool(
            name="system.cpu_info",
            description="Get CPU usage",
            category="system",
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=8.0,
        )
        async def cpu_tool(params: MockCpuInput) -> MockCpuOutput:
            return MockCpuOutput(usage_percent=15.0, core_count=4)

        assert hasattr(cpu_tool, "__tool_def__")
        tool_def = cpu_tool.__tool_def__
        assert tool_def.name == "system.cpu_info"
        assert tool_def.description == "Get CPU usage"
        assert tool_def.category == "system"
        assert tool_def.input_schema is MockCpuInput
        assert tool_def.output_schema is MockCpuOutput
        assert tool_def.permission_level is PermissionLevel.SAFE
        assert tool_def.timeout_seconds == 8.0
        assert tool_def.handler is not None

    def test_decorated_sync_function_with_keyword_args(self) -> None:
        @tool(
            name="files.search",
            description="Search files by pattern",
            category="files",
        )
        def search_files(pattern: str, max_results: int = 10) -> list[str]:
            """Docstring should not override explicit description."""
            return [pattern]

        tool_def = search_files.__tool_def__
        assert tool_def.name == "files.search"
        assert tool_def.description == "Search files by pattern"
        assert inspect.isclass(tool_def.input_schema)
        assert issubclass(tool_def.input_schema, BaseModel)

        # Verify dynamic model fields
        schema = tool_def.get_input_json_schema()
        assert "pattern" in schema["properties"]
        assert schema["properties"]["pattern"]["type"] == "string"
        assert "max_results" in schema["properties"]
        assert schema["properties"]["max_results"]["default"] == 10
        assert schema["required"] == ["pattern"]

    def test_bare_decorator_with_docstring(self) -> None:
        @tool
        def ping() -> dict[str, str]:
            """Health check ping."""
            return {"status": "ok"}

        tool_def = ping.__tool_def__
        assert tool_def.name == "ping"
        assert tool_def.description == "Health check ping."
        assert tool_def.category == "general"
        assert tool_def.permission_level is PermissionLevel.SAFE

    def test_category_inferred_from_namespace_dot(self) -> None:
        @tool
        def system_status() -> None:
            pass

        @tool(name="system.status")
        def status_tool() -> None:
            pass

        assert status_tool.__tool_def__.category == "system"
        assert system_status.__tool_def__.category == "general"

    def test_decorated_function_callable_directly(self) -> None:
        import asyncio

        @tool
        async def multiply(a: int, b: int = 2) -> int:
            return a * b

        result = asyncio.run(multiply(5, b=3))
        assert result == 15

    def test_decorator_with_target_registry(self) -> None:
        custom_registry = ToolRegistry()

        @tool(name="test.custom_registered", registry=custom_registry)
        def custom_tool(x: int) -> int:
            return x

        assert "test.custom_registered" in custom_registry
        assert custom_registry.get("test.custom_registered").name == "test.custom_registered"


# ---------------------------------------------------------------------------
# Test ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """Test tool cataloging, duplicate detection, category filtering, and lookup."""

    @pytest.fixture
    def registry(self) -> ToolRegistry:
        return ToolRegistry()

    def test_empty_registry(self, registry: ToolRegistry) -> None:
        assert len(registry) == 0
        assert registry.list_tools() == []
        assert registry.list_categories() == []

    def test_register_and_lookup_tool_definition(self, registry: ToolRegistry) -> None:
        tool_def = ToolDefinition(
            name="system.cpu",
            description="CPU info",
            category="system",
            input_schema=MockCpuInput,
        )
        registry.register(tool_def)
        assert len(registry) == 1
        assert "system.cpu" in registry
        assert registry.has("system.cpu")
        assert registry.get("system.cpu") is tool_def
        assert registry["system.cpu"] is tool_def

    def test_register_callable(self, registry: ToolRegistry) -> None:
        def my_tool(x: int) -> int:
            """Sample tool."""
            return x

        tool_def = registry.register(my_tool)
        assert tool_def.name == "my_tool"
        assert "my_tool" in registry

    def test_duplicate_registration_raises_error(self, registry: ToolRegistry) -> None:
        tool_def1 = ToolDefinition(
            name="system.cpu",
            description="CPU info 1",
            category="system",
            input_schema=MockCpuInput,
        )
        tool_def2 = ToolDefinition(
            name="system.cpu",
            description="CPU info 2",
            category="system",
            input_schema=MockCpuInput,
        )
        registry.register(tool_def1)

        with pytest.raises(ToolAlreadyExistsError) as exc_info:
            registry.register(tool_def2)

        # Also verify inheritance: ToolAlreadyExistsError is both ToolError and ValueError
        assert isinstance(exc_info.value, ToolError)
        assert isinstance(exc_info.value, ValueError)
        assert "system.cpu" in str(exc_info.value)

    def test_duplicate_registration_with_replace(self, registry: ToolRegistry) -> None:
        tool_def1 = ToolDefinition(
            name="system.cpu",
            description="Original",
            category="system",
            input_schema=MockCpuInput,
        )
        tool_def2 = ToolDefinition(
            name="system.cpu",
            description="Updated",
            category="system",
            input_schema=MockCpuInput,
        )
        registry.register(tool_def1)
        registry.register(tool_def2, replace=True)

        assert len(registry) == 1
        assert registry.get("system.cpu").description == "Updated"

    def test_lookup_missing_tool_raises_tool_not_found(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError) as exc_info:
            registry.get("nonexistent.tool")

        # Also verify inheritance: ToolNotFoundError is both ToolError and KeyError
        assert isinstance(exc_info.value, ToolError)
        assert isinstance(exc_info.value, KeyError)

    def test_get_optional(self, registry: ToolRegistry) -> None:
        assert registry.get_optional("missing") is None
        tool_def = ToolDefinition(
            name="files.read",
            description="Read",
            category="files",
            input_schema={"type": "object"},
        )
        registry.register(tool_def)
        assert registry.get_optional("files.read") is tool_def

    def test_unregister_tool(self, registry: ToolRegistry) -> None:
        tool_def = ToolDefinition(
            name="files.read",
            description="Read",
            category="files",
            input_schema={"type": "object"},
        )
        registry.register(tool_def)
        removed = registry.unregister("files.read")
        assert removed is tool_def
        assert "files.read" not in registry
        assert len(registry) == 0

    def test_unregister_missing_tool_raises_error(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError):
            registry.unregister("nonexistent")

    def test_clear(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name="t1", description="d1", category="c1", input_schema={"type": "object"}
            )
        )
        registry.register(
            ToolDefinition(
                name="t2", description="d2", category="c2", input_schema={"type": "object"}
            )
        )
        assert len(registry) == 2
        registry.clear()
        assert len(registry) == 0

    def test_list_tools_and_categories_with_filtering(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name="system.cpu",
                description="CPU",
                category="system",
                input_schema={"type": "object"},
            )
        )
        registry.register(
            ToolDefinition(
                name="system.memory",
                description="RAM",
                category="system",
                input_schema={"type": "object"},
            )
        )
        registry.register(
            ToolDefinition(
                name="files.read",
                description="Read",
                category="files",
                input_schema={"type": "object"},
            )
        )

        assert len(registry.list_tools()) == 3
        assert len(registry.list_tools(category="system")) == 2
        assert len(registry.list_tools(category="files")) == 1
        assert registry.list_tools(category="nonexistent") == []
        assert registry.list_categories() == ["files", "system"]

    def test_registry_tool_decorator_method(self, registry: ToolRegistry) -> None:
        @registry.tool(
            name="computer.screenshot", description="Capture display", category="computer"
        )
        def screenshot(monitor: int = 1) -> bytes:
            return b"fake-png"

        assert "computer.screenshot" in registry
        tool_def = registry.get("computer.screenshot")
        assert tool_def.category == "computer"
        assert screenshot(1) == b"fake-png"

    def test_export_json_schemas(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolDefinition(
                name="system.cpu_info",
                description="Get CPU info",
                category="system",
                input_schema=MockCpuInput,
            )
        )
        registry.register(
            ToolDefinition(
                name="files.read_file",
                description="Read file",
                category="files",
                input_schema=MockFileInput,
            )
        )

        all_schemas = registry.export_json_schemas()
        assert len(all_schemas) == 2
        names = {s["function"]["name"] for s in all_schemas}
        assert names == {"system.cpu_info", "files.read_file"}

        system_schemas = registry.export_json_schemas(category="system")
        assert len(system_schemas) == 1
        assert system_schemas[0]["function"]["name"] == "system.cpu_info"

    def test_invalid_registration_target_raises_error(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolValidationError):
            registry.register("not a tool or function")  # type: ignore[arg-type]
