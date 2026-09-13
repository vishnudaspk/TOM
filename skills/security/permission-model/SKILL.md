---
name: security-permission-model
description: >
  How TOM's permission and security layer works, and how to implement it correctly.
  Use when classifying a new tool's risk level, implementing confirmation flows,
  handling secrets, reviewing code for permission bypasses, or designing any feature
  that touches shell execution, filesystem, process control, input control, credentials,
  network, or IPC. This skill applies to ALL phases of TOM development.
---

# Security — TOM Permission Model

Every action TOM takes on behalf of a user must go through the permission system.
This skill covers how to classify, implement, and enforce permissions correctly.

---

## Permission Levels (from plan.md)

| Level | Meaning | Examples |
|-------|---------|---------|
| `SAFE` | Execute automatically | CPU/GPU/battery info, time, read-only system stats |
| `ASK` | Require explicit user confirmation | Delete file, install software, send message, modify system settings |
| `BLOCK` | Never execute via LLM request | Arbitrary shell commands, credential access, unrestricted destructive operations |

Classify at tool registration time — not derived from the LLM's phrasing at runtime.

---

## The Core Rule

**The LLM can request actions. It cannot bypass the permission layer.**

```
LLM generates tool call
    ↓
Tool Registry (validate tool exists)
    ↓
Permission Layer (SAFE / ASK / BLOCK)
    ↓  ← user confirmation if ASK
Tool Executor
    ↓
Validated result
```

No shortcut through this pipeline is acceptable.

---

## Confirmation Flow

When a tool is `ASK`-level, TOM must pause and get explicit confirmation:

```
TOM: "I found 3 duplicate files. Delete them? [yes/no]"
User: "yes"
    ↓
Permission granted → execute
    ↓
Task resumes
```

The task enters `WAITING_FOR_CONFIRMATION` state.
On denial: task moves to `CANCELLED` or `FAILED` depending on context.
On timeout without response: treat as denied.

---

## Secrets Handling

Never expose secrets to the LLM.

```python
# Wrong
prompt = f"Use API key {api_key} to call the service"

# Correct — the model never sees the actual credential
result = await service.call_with_credentials(service_name="web_search")
# The model receives: "web_search service is available"
```

Store secrets in:
- Environment variables for development.
- A secure secret storage abstraction (e.g. platform keychain) for production.

**Never log secrets.** Filter them in the telemetry layer.

---

## High-Risk Operation Checklist

Before implementing any operation that touches:

- Shell / subprocess execution
- Filesystem (write, move, delete)
- Process control (kill, start)
- Keyboard / mouse input
- Network requests to external services
- Model/tool access
- IPC
- Credentials

Ask:
1. What is the minimum required privilege?
2. Can this cause irreversible harm?
3. Does the user need to confirm before this runs?
4. Can this be audited after the fact?
5. What is the safe failure state?

---

## Least Privilege

Tools should request only the access they need.

```python
# Wrong — overly broad
class FileToolInput(BaseModel):
    path: str       # any path on the filesystem

# Better — scoped to expected directories
class FileSearchInput(BaseModel):
    directory: str  # validated against allowed_directories config
    pattern: str
```

Validate paths against an `allowed_directories` allowlist before any file operation.

---

## Auditability

Every tool call should be logged with:
- Tool name
- Parameters (sanitised — no secrets)
- Permission level
- Whether confirmation was required and the outcome
- Result status
- Timestamp

```json
{
  "timestamp": "...",
  "component": "tool_executor",
  "tool": "files.delete",
  "permission_level": "ASK",
  "user_confirmed": true,
  "success": true
}
```

---

## Safe Failure

When a tool fails, TOM must not silently continue as if it succeeded.

```python
if not result.success:
    raise ToolFailed(f"{tool.name} failed: {result.error}")
# Never: return None and continue
```

Cancellation stops future tool calls. It does not undo operations already completed.
The relevant subsystem must determine if rollback is possible.

---

## What NOT to Do

- Never use `subprocess.run(llm_generated_string)`.
- Never inject LLM output into SQL queries.
- Never store API keys in memory/database.
- Never disable confirmation for a `ASK`-level tool "for convenience".
- Never log the user's credentials even in debug mode.
- Never bypass the permission layer with a flag or environment variable.

---

## Related Skills

- `python/tool-system` — Tool definitions and execution
- `security/secrets` — Secrets management implementation
- `coding/validation` — Input validation patterns
- `agent-development/tool-design` — Designing tools with safety in mind
