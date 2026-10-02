"""TOM Deterministic OS Input Automation Tools.

Provides typed, deterministic OS input automation tools delegating strictly to tom-engine:
- os.input.click (ASK_USER): Click at coordinate (x, y) with specified button.
- os.input.type_text (ASK_USER): Type text into active window (max 500 characters).
- os.input.hotkey (ASK_USER): Send key combination (e.g. ["ctrl", "c"]).
- os.input.get_cursor_pos (SAFE): Query current mouse cursor position (x, y).

Adheres to:
- Phase 7 Vision & OS Automation Specification (Iteration 4)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- Zero direct OS input injection from Python: all actuation routes via EngineClient IPC to Rust.
- Coordinate, text, and key validation strictly enforced.
- 500-character maximum typing length enforced.
- Offline dry-run and mock support for deterministic testing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

if TYPE_CHECKING:
    from tom.core.engine import EngineClient


# ---------------------------------------------------------------------------
# Pydantic Input and Output Schemas
# ---------------------------------------------------------------------------


class InputClickInput(BaseModel):
    """Input parameters for os.input.click tool."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(
        ...,
        ge=0,
        description="Target X screen coordinate in virtual desktop pixels",
    )
    y: int = Field(
        ...,
        ge=0,
        description="Target Y screen coordinate in virtual desktop pixels",
    )
    button: Literal["left", "right", "middle"] = Field(
        default="left",
        description="Mouse button to click (left, right, or middle)",
    )
    click_type: Literal["single", "double", "triple"] = Field(
        default="single",
        description="Click repetition type (single, double, triple)",
    )


class InputClickOutput(BaseModel):
    """Output result for os.input.click tool."""

    model_config = ConfigDict(extra="ignore")

    success: bool = Field(description="Whether the click command was dispatched")
    x: int = Field(description="X coordinate clicked")
    y: int = Field(description="Y coordinate clicked")
    button: str = Field(description="Mouse button used")
    click_type: str = Field(description="Click repetition type")
    simulated: bool = Field(default=False, description="Whether execution was simulated (dry-run)")


class InputTypeTextInput(BaseModel):
    """Input parameters for os.input.type_text tool."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Text string to type into the active window (maximum 500 characters)",
    )

    @field_validator("text")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Typed text cannot be empty or purely whitespace")
        return v


class InputTypeTextOutput(BaseModel):
    """Output result for os.input.type_text tool."""

    model_config = ConfigDict(extra="ignore")

    success: bool = Field(description="Whether the typing command was dispatched")
    characters_typed: int = Field(description="Number of characters typed")
    simulated: bool = Field(default=False, description="Whether execution was simulated (dry-run)")


class InputHotkeyInput(BaseModel):
    """Input parameters for os.input.hotkey tool."""

    model_config = ConfigDict(extra="forbid")

    keys: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Key combination to press simultaneously, e.g. ['ctrl', 'c'] or ['alt', 'tab']",
    )

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip().lower() for k in v if k.strip()]
        if not cleaned:
            raise ValueError("Hotkey combination cannot be empty")
        return cleaned


class InputHotkeyOutput(BaseModel):
    """Output result for os.input.hotkey tool."""

    model_config = ConfigDict(extra="ignore")

    success: bool = Field(description="Whether the hotkey command was dispatched")
    keys: list[str] = Field(description="Key combination sent")
    simulated: bool = Field(default=False, description="Whether execution was simulated (dry-run)")


class InputGetCursorPosInput(BaseModel):
    """Input parameters for os.input.get_cursor_pos tool (zero arguments)."""

    model_config = ConfigDict(extra="forbid")


class InputGetCursorPosOutput(BaseModel):
    """Output result for os.input.get_cursor_pos tool."""

    model_config = ConfigDict(extra="ignore")

    x: int = Field(description="Current mouse cursor X position in virtual desktop pixels")
    y: int = Field(description="Current mouse cursor Y position in virtual desktop pixels")
    simulated: bool = Field(default=False, description="Whether cursor position was simulated")


# ---------------------------------------------------------------------------
# Tool Factory & Registration
# ---------------------------------------------------------------------------


def create_input_tools(
    client: EngineClient | None = None,
    dry_run: bool = False,
) -> list[ToolDefinition]:
    """Create deterministic OS input automation tools bound to EngineClient.

    Args:
        client: Optional live EngineClient for IPC input delegation to tom-engine.
        dry_run: If True or if client is None, simulates input without dispatching IPC.
            Python never directly injects input into Windows APIs.

    Returns:
        List of ToolDefinition instances (os.input.click, os.input.type_text,
        os.input.hotkey, os.input.get_cursor_pos).
    """

    async def click(params: InputClickInput) -> InputClickOutput:
        """Click at coordinate (x, y) with specified button."""
        if client is not None and not dry_run:
            await client.mouse_click(
                x=params.x,
                y=params.y,
                button=params.button,
                click_type=params.click_type,
            )
            return InputClickOutput(
                success=True,
                x=params.x,
                y=params.y,
                button=params.button,
                click_type=params.click_type,
                simulated=False,
            )

        return InputClickOutput(
            success=True,
            x=params.x,
            y=params.y,
            button=params.button,
            click_type=params.click_type,
            simulated=True,
        )

    async def type_text(params: InputTypeTextInput) -> InputTypeTextOutput:
        """Type text into the active window (enforces max 500 chars)."""
        if client is not None and not dry_run:
            await client.type_text(text=params.text)
            return InputTypeTextOutput(
                success=True,
                characters_typed=len(params.text),
                simulated=False,
            )

        return InputTypeTextOutput(
            success=True,
            characters_typed=len(params.text),
            simulated=True,
        )

    async def hotkey(params: InputHotkeyInput) -> InputHotkeyOutput:
        """Send key combination."""
        if client is not None and not dry_run:
            await client.send_hotkey(keys=params.keys)
            return InputHotkeyOutput(
                success=True,
                keys=params.keys,
                simulated=False,
            )

        return InputHotkeyOutput(
            success=True,
            keys=params.keys,
            simulated=True,
        )

    async def get_cursor_pos(params: InputGetCursorPosInput) -> InputGetCursorPosOutput:
        """Query current mouse cursor position."""
        if client is not None and not dry_run:
            pos = await client.get_cursor_pos()
            return InputGetCursorPosOutput(
                x=pos.get("x", 0),
                y=pos.get("y", 0),
                simulated=False,
            )

        return InputGetCursorPosOutput(
            x=0,
            y=0,
            simulated=True,
        )

    return [
        ToolDefinition(
            name="os.input.click",
            description="Click mouse at specified (x, y) virtual desktop coordinates with button selection.",
            category="os.input",
            input_schema=InputClickInput,
            output_schema=InputClickOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=click,
        ),
        ToolDefinition(
            name="os.input.type_text",
            description="Type a sequence of characters into the active window (maximum 500 characters).",
            category="os.input",
            input_schema=InputTypeTextInput,
            output_schema=InputTypeTextOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=type_text,
        ),
        ToolDefinition(
            name="os.input.hotkey",
            description="Send a key combination (e.g. ['ctrl', 'c'] or ['alt', 'tab']) to the active window.",
            category="os.input",
            input_schema=InputHotkeyInput,
            output_schema=InputHotkeyOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=hotkey,
        ),
        ToolDefinition(
            name="os.input.get_cursor_pos",
            description="Query the current mouse cursor position (x, y) on the virtual desktop.",
            category="os.input",
            input_schema=InputGetCursorPosInput,
            output_schema=InputGetCursorPosOutput,
            permission_level=PermissionLevel.SAFE,
            handler=get_cursor_pos,
        ),
    ]


def register_input_tools(
    registry: ToolRegistry | None = None,
    client: EngineClient | None = None,
    dry_run: bool = False,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register all deterministic OS input automation tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry (defaults to default_registry).
        client: Optional live EngineClient.
        dry_run: Whether to run in simulated mode without IPC dispatch.
        replace: Whether to replace existing registrations.

    Returns:
        List of registered ToolDefinition instances.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_input_tools(client=client, dry_run=dry_run)
    for tool in tools:
        target_registry.register(tool, replace=replace)
    return tools
