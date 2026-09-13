---
name: coding-validation
description: >
  How to validate inputs, outputs, and structured data in TOM. Use when writing input
  validation for tools, IPC messages, configuration schemas, memory records, or any
  boundary where untrusted or LLM-generated data enters a deterministic subsystem.
  Covers both Python (Pydantic) and Rust (serde) validation patterns.
---

# Coding — Input Validation

Validation is TOM's defence against LLM-generated bad data reaching deterministic
subsystems. Every boundary where unstructured or model-generated data enters the system
must be validated.

---

## Where Validation is Required

```
LLM generates tool call parameters → validate before execution
IPC request arrives (Rust) → validate before handler dispatch
Memory operation request → validate before writing to SQLite
Configuration file loaded → validate on startup
User input to voice pipeline → sanitise before processing
```

---

## Python — Pydantic

Use Pydantic v2 models for all structured data that crosses subsystem boundaries.

```python
from pydantic import BaseModel, Field, field_validator
from pathlib import Path

class FileReadInput(BaseModel):
    path: str = Field(description="Absolute path to the file to read")
    max_bytes: int = Field(default=1_000_000, gt=0, le=10_000_000)

    @field_validator("path")
    @classmethod
    def must_be_absolute_and_allowed(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            raise ValueError("path must be absolute")
        if not any(str(p).startswith(d) for d in ALLOWED_DIRS):
            raise ValueError(f"path {v!r} is outside allowed directories")
        return str(p)
```

### Key Pydantic Patterns

- Use `Field(ge=, le=, gt=, lt=)` for numeric bounds.
- Use `Literal[...]` for enumerated string values.
- Use `field_validator` for cross-field or complex validation.
- Use `model_validator` for validation across multiple fields.
- Always validate at the boundary — not inside handler logic.

---

## Python — Configuration Validation

Load and validate YAML config at startup using Pydantic:

```python
class ModelConfig(BaseModel):
    provider: Literal["local", "cloud"]
    model: str
    quantization: Optional[str] = None
    vram_required_mb: int = Field(gt=0)

class TOMConfig(BaseModel):
    models: dict[str, ModelConfig]
    voice: VoiceConfig
    permissions: PermissionsConfig
```

Fail fast at startup if the config is invalid — do not silently use defaults for
security-relevant settings.

---

## Rust — serde

Use `serde` for deserialising IPC messages. Reject unknown fields in test mode.

```rust
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]  // reject extra fields during development
pub struct IpcRequest {
    pub id: String,
    pub version: u32,
    pub method: String,
    pub params: serde_json::Value,
}
```

For handler params, deserialise into strongly-typed structs:

```rust
#[derive(Deserialize)]
pub struct GpuTempParams {
    // currently empty — future params can be added safely
}

fn handle_gpu_temp(params: serde_json::Value) -> Result<serde_json::Value> {
    let _params: GpuTempParams = serde_json::from_value(params)
        .context("invalid gpu_temperature params")?;
    // ...
}
```

---

## Security-Critical Validation

Path traversal prevention:

```python
def is_within_allowed_directories(path: str, allowed: list[str]) -> bool:
    resolved = Path(path).resolve()
    return any(
        str(resolved).startswith(str(Path(d).resolve()))
        for d in allowed
    )
```

Never use string prefix matching on raw (unresolved) paths — `../` attacks bypass it.

Command injection prevention:

```python
# Never do this
import subprocess
subprocess.run(f"tool {user_input}", shell=True)

# Tools receive structured params, never raw strings for execution
```

---

## Validation Failures

When validation fails:
- Raise a descriptive exception with the field and reason.
- Log the validation failure (without logging the potentially sensitive value).
- Return an error response — do not crash or silently continue.

```python
try:
    validated = FileReadInput.model_validate(raw_params)
except ValidationError as e:
    raise ToolValidationError(f"invalid file read parameters: {e}")
```

---

## Related Skills

- `security/permission-model` — Validation is the input side; permissions are the output side
- `python/tool-system` — Where validation is applied in the tool pipeline
- `rust/ipc` — Rust-side message validation
