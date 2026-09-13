---
name: agent-development-tool-design
description: >
  How to design safe, well-scoped tools for TOM's agent layer. Use when creating a new
  tool that an LLM agent will call, designing tool input/output schemas, deciding on
  tool granularity (one big tool vs many small ones), or reviewing existing tools for
  safety or schema quality. This is not about the tool execution infrastructure
  (see python/tool-system for that) — it's about design decisions for individual tools.
---

# Agent Development — Tool Design

Well-designed tools make agents more reliable and safer. Poorly designed tools lead
to hallucinated parameters, ambiguous results, and permission issues.

---

## Design Principles

**1. One responsibility per tool**

A tool should do exactly one thing. Avoid "utility" tools that do multiple operations
based on a string parameter.

```python
# Wrong: overloaded tool
async def file_operation(action: Literal["read", "write", "delete"], path: str, ...):

# Correct: separate tools
async def read_file(path: str) -> str: ...
async def write_file(path: str, content: str) -> None: ...
async def delete_file(path: str) -> None: ...
```

This makes permission classification unambiguous and reduces hallucination risk.

**2. Schemas that guide the model**

Write Pydantic schemas with descriptions that tell the model what values are expected.

```python
class WebSearchInput(BaseModel):
    query: str = Field(description="The search query to execute")
    max_results: int = Field(default=5, ge=1, le=20, description="Number of results to return")
```

Field descriptions become part of the tool spec the model sees.

**3. Validate inputs, never trust them**

Even though the model generates parameters, validate them before execution.
Do not let the model bypass validation by generating a technically-valid-but-dangerous value.

```python
class FileReadInput(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def must_be_within_allowed_dirs(cls, v):
        if not is_within_allowed_directories(v):
            raise ValueError(f"path {v!r} is outside allowed directories")
        return v
```

**4. Return structured results**

Return typed Pydantic models, not raw dicts or strings.

```python
class FileReadOutput(BaseModel):
    content: str
    size_bytes: int
    encoding: str
```

The model can reason more reliably over structured results.

---

## Tool Naming Convention

Use dot-notation with category prefix:

```
system.cpu_usage
system.gpu_temperature
files.read
files.delete
web.search
computer.screenshot
memory.search
```

This makes the tool's category immediately visible in logs and permission checks.

---

## Tool Granularity

**Too coarse:** one tool per category (e.g. `system_tool(action: str)`)
→ Model must guess which actions exist. Permission classification is impossible.

**Too fine:** a separate tool for every minor variation
→ Too many tools confuse the model; it can't choose between them.

**Right level:** one tool per distinct, independently-permissionable action.

A good signal: if two operations would have different permission levels, they must be
separate tools.

---

## Tools and Cancellation

Long-running tools must declare `cancellable: bool` and respect it.

Tools that are safe to interrupt (web requests, AI generation):
```python
cancellable=True
```

Tools that must complete or roll back (file moves, database transactions):
```python
cancellable=False
# Document: "cannot be safely interrupted mid-execution"
```

---

## Tool Documentation for the LLM

Write clear, unambiguous descriptions. The model uses the description to decide when to
call the tool.

```python
ToolDefinition(
    name="system.gpu_temperature",
    description=(
        "Get the current GPU temperature in Celsius. "
        "Use when the user asks about GPU temperature, heat, or thermal status. "
        "Returns the temperature as a float."
    ),
    ...
)
```

Avoid vague descriptions like "gets GPU info" — the model needs to know exactly what
it returns.

---

## Idempotency

Prefer idempotent tools where possible (calling twice = same result as calling once).
Non-idempotent tools (create file, send message) must be `ASK`-level or higher.

---

## Testing Your Tool Design

Before integrating a new tool into the agent:

1. Write a unit test for the handler with valid inputs.
2. Write a unit test for the handler with invalid/boundary inputs.
3. Test the schema by passing it to a real model and checking the generated call.
4. Verify the tool appears correctly in the agent's tool list.

---

## Related Skills

- `python/tool-system` — Tool registration, executor, and permission enforcement
- `security/permission-model` — How to classify the tool's permission level
- `python/pydantic-agents` — How agents discover and use tools
