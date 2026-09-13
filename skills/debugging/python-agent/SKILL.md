---
name: debugging-python-agent
description: >
  How to diagnose and fix issues in TOM's Python agent layer: unexpected LLM outputs,
  tool call failures, structured output validation errors, memory retrieval problems,
  IPC timeouts, and agent loop failures. Use when an agent is producing wrong outputs,
  calling the wrong tool, failing to use memory correctly, or when Pydantic validation
  is rejecting model outputs.
---

# Debugging — Python Agent Issues

This skill covers diagnosis patterns for common failures in TOM's Python brain layer.

---

## Wrong or Missing Tool Call

**Symptom:** The LLM generates a response but doesn't call the expected tool,
or calls the wrong tool.

**Diagnosis:**
1. Enable debug logging for agent runs.
2. Check if the tool description is clear enough for the model to select it.
3. Check if the model's system prompt explains when tools should be used.
4. Reduce schema complexity — smaller models (Qwen3-1.7B) need simpler schemas.

```python
# Pydantic AI: log the model messages for inspection
result = await agent.run(prompt, deps=deps, message_history=[])
for msg in result.all_messages():
    print(msg)
```

---

## Pydantic Validation Error on Agent Output

**Symptom:** `ModelRetry` or `ValidationError` when the agent returns its result.

**Diagnosis:**
1. Print the raw LLM response before validation.
2. Check if the `result_type` schema is too complex for the model.
3. Simplify or split the schema.

```python
# Temporarily catch and inspect
try:
    result = await agent.run(prompt, deps=deps)
except Exception as e:
    print(f"Raw error: {e}")
```

**Fix:** Simplify the result schema. Use `Literal` types where possible.
Avoid nested optional models for smaller router models.

---

## IPC Timeout

**Symptom:** A tool call raises `IPCTimeout` when trying to reach tom-engine.

**Diagnosis steps:**
1. Is `tom-engine` running? Check process list.
2. Is the IPC socket/pipe path correct? Check `ipc/protocol.py` constants.
3. Is the method name correct? Log the method name before the call.
4. Is the timeout too short? Check `timeout_ms` in the tool definition.

```python
# Temporary debug: lower timeout to expose quick failures
result = await ipc.call("system.cpu_usage", timeout_ms=5000)
```

---

## Memory Retrieval Returns Wrong Results

**Symptom:** Semantic search returns irrelevant memories.

**Diagnosis:**
1. Check the embedding model — is it the same one used at store time?
2. Check if Qdrant is in sync with SQLite (no orphaned records).
3. Test with a direct embedding similarity check.
4. Reduce `limit` — you may be returning too many results.

```python
# Debug: check what embedding is generated for the query
embedding = await embeddings.embed("TTS engine preference")
print(f"Embedding dimension: {len(embedding)}")
```

---

## Tool Returns Unexpected Data

**Symptom:** A tool returns a valid result but the data is wrong (e.g. GPU temperature
shows 0.0).

**Diagnosis:**
1. Is the IPC response correct? Log `raw = await ipc.call(...)` before parsing.
2. Is the Pydantic output model mapping the fields correctly?
3. Is the Rust handler returning the correct field name?

```python
raw = await ctx.ipc.call("system.gpu_temperature")
print(f"Raw IPC response: {raw}")
result = GpuTempOutput(**raw)
```

---

## Agent Doesn't Stop on Cancellation

**Symptom:** A cancelled task continues running agent steps.

**Diagnosis:**
1. Check that the cancel token is being checked inside the agent tool.
2. Check that the orchestrator's cancel propagation reaches the agent deps.

```python
@agent.tool
async def long_running_tool(ctx):
    if ctx.deps.cancel_token.is_set():
        raise ToolCancelled("task was cancelled")
    # proceed
```

---

## Permission Denied Unexpectedly

**Symptom:** A `SAFE`-level tool raises `PermissionBlocked`.

**Diagnosis:**
1. Check the tool definition — is the `permission_level` set correctly in `registry.py`?
2. Check if a config override is blocking the tool.
3. Check if the tool name matches exactly (case-sensitive).

---

## Structured Logging for Debugging

TOM uses structured logs. Use these fields to filter:

```json
{"component": "tool_executor", "tool": "system.gpu_temperature", "event": "tool_call"}
```

Filter in development:
```bash
python -m tom 2>&1 | python -c "import sys,json; [print(json.loads(l)) for l in sys.stdin if 'tool_executor' in l]"
```

---

## Checklist: Agent Run Not Behaving as Expected

- [ ] Enable debug logging and inspect all agent messages.
- [ ] Verify the tool description is unambiguous.
- [ ] Verify the result_type schema is valid for the model size.
- [ ] Check IPC is reachable and the method exists.
- [ ] Check permission level is correctly classified.
- [ ] Check cancel token is wired through to tool execution.

---

## Related Skills

- `python/pydantic-agents` — Correct agent structure
- `python/tool-system` — Tool registration and execution
- `python/ipc-client` — IPC connection and timeout handling
- `debugging/rust-errors` — If the IPC call reaches Rust but fails there
