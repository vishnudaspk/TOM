# Final Phase 10 Implementation Plan

## Scope Architecture

Phase 10 extends the existing architecture rather than replacing it:

- Preserve the canonical ToolRegistry → ToolExecutor → PermissionEngine pipeline.
- Preserve the existing 28-tool baseline.
- Add six tools for a final total of **34**.
- Reuse existing VisionManager, ProactiveScheduler, SQLite MemoryManager boundaries, configuration schemas, and resource controls.
- No new memory subsystem.
- No cloud models, local-runtime-only model execution, or parallel vision subsystem.

## Final Phase 10 Tool List

| Tool | Responsibility | Permission |
|---|---|---|
| `web.search` | Search a configured local or self-hosted source | SAFE |
| `web.fetch` | Retrieve and extract a public URL | SAFE, read-only |
| `personal.note` | Create, update, retrieve, or delete notes | ASK_USER for mutations |
| `personal.reminder` | Create, update, retrieve, cancel, or delete reminders | ASK_USER for mutations |
| `personal.calendar` | Create, update, retrieve, or delete calendar events | ASK_USER for mutations |
| `vision.analyze` | Analyze an image or captured frame through VisionManager | SAFE for analysis |

The resulting count is:

**28 + 2 web tools + 3 personal tools + 1 vision tool = 34**

Each tool must expose typed Pydantic input and output schemas, deterministic registration metadata, timeout, and an explicit permission tier.

## Architecture Constraints

- Web access is read-only and must never execute content, write files, invoke UI automation, or influence permission decisions.
- Fetched content is untrusted data and must be sanitized, bounded, and isolated from tool authorization.
- SQLite remains authoritative for personal notes, reminders, and calendar events.
- Qdrant remains optional semantic indexing and is not used for personal productivity data.
- Raw audio and screenshots are not persisted in SQLite or Qdrant.
- Personal data must not be incorporated into the existing memory subsystem.
- Image analysis delegates exclusively to VisionManager.
- Reminders use ProactiveScheduler policy and must not become arbitrary executable tasks.
- Tool mutations require explicit confirmation through the existing permission pipeline.
- No browser automation, email, messaging, external calendar synchronization, cloud vision, or remote screenshot handling.

## Security Model

### Web Search

- Search backend must be configurable and may be a local SearXNG-compatible endpoint.
- No API key is required by the default contract.
- Search results must contain URL, title, summary, source, and retrieval metadata.
- Search must enforce response and result-count limits.

### Web Fetch

- Only HTTP and HTTPS are permitted.
- Reject loopback, private, link-local, multicast, reserved, and non-public destinations.
- Resolve and validate every host before connection.
- Revalidate redirects and every redirected destination.
- Reject DNS rebinding and host changes after initial validation.
- Enforce connection timeout, total timeout, redirect limit, response-size limit, and content-type validation.
- Return sanitized extracted text only.
- Never execute scripts, embedded active content, or model-generated code.
- Mark fetched content as untrusted; fetched content cannot affect permission decisions.

### Personal Data

- Use dedicated SQLite schemas and tables.
- Use UTC timestamps and stable identifiers.
- Reject malformed or overlong fields.
- Add retention and deletion policies.
- Keep personal data out of Qdrant and existing memory records.
- Prevent sensitive data from being copied into model context unless explicitly requested and authorized.

### Image Analysis

- Delegate to `VisionManager.analyze`.
- Use existing OCR/CV/VLM capability selection and VRAM checks.
- Do not create a parallel image pipeline.
- Do not persist raw images or screenshots.
- Treat the image as privacy-sensitive input and apply the existing sensitive-window controls.

## Required Dependencies

### Existing dependencies

- `httpx`
- Pydantic
- Python asyncio
- SQLite standard library
- Existing VisionManager, ResourceManager, ProactiveScheduler, and ToolExecutor

### Web extraction

- BeautifulSoup-compatible parsing.
- Readability-oriented extraction.
- These are required only for bounded content extraction and must not introduce a remote browser.

### Web search

- A configurable local SearXNG-compatible endpoint or equivalent local provider adapter.
- No cloud search endpoint is permitted by default.

### Personal persistence

- No new external database dependency.
- Existing SQLite mechanism and schema migration approach.

### Image analysis

- Existing VisionManager and local model/runtime dependencies.
- No new cloud model dependency.

## Iteration Breakdown

### Iteration 0 — Architecture Contract

- Confirm the 34-tool list and names.
- Confirm individual tool contracts.
- Lock permission boundaries.
- Lock dependency selection.
- Lock SSRF and untrusted-content policy.
- Define exact schema and test expectations.
- **No code changes.**

### Iteration 1 — Web Search

- Add `web.search`.
- Add configurable local search integration.
- Add typed result schemas.
- Add result and response limits.
- Add offline tests using a deterministic fake transport.

### Iteration 2 — Web Fetch

- Add `web.fetch`.
- Add URL normalization and SSRF validation.
- Add redirect and DNS rebinding handling.
- Add response-size and timeout limits.
- Add content extraction and sanitization.
- Add untrusted-content tests.

### Iteration 3 — Personal Notes and Calendar

- Add `personal.note`.
- Add `personal.calendar`.
- Add dedicated SQLite schemas.
- Add create, retrieve, update, delete, and retention behavior.
- Add authorization and validation tests.

### Iteration 4 — Personal Reminders

- Add `personal.reminder`.
- Add reminder recurrence and lifecycle semantics.
- Integrate with ProactiveScheduler.
- Add user preemption, quiet-hours, rate-limit, cancellation, and duplicate-suppression behavior.
- Add migration-safe persistence.

### Iteration 5 — Image Analysis

- Add `vision.analyze`.
- Delegate to VisionManager.
- Reuse capability routing and VRAM safeguards.
- Add privacy and non-persistence validation.
- Add injected-manager tests without invoking real model inference.

### Iteration 6 — Tool Integration

- Register all 34 tools through the existing bootstrap path.
- Add final permission mappings.
- Add configuration fields and YAML defaults.
- Update tool-count and registry-export tests.
- Verify no duplicate or conflicting registrations.

### Iteration 7 — Security and Verification

- Validate SSRF, redirects, DNS rebinding, response limits, and prompt-injection posture.
- Validate persistence and deletion behavior.
- Validate permission enforcement.
- Run affected-module tests only.
- Confirm the final tool count and exit gates.
- No full-suite run is required during planning or approval.

## Exit Gates

An iteration is complete only when:

- Its input/output schemas are validated.
- Its permission classification is explicit.
- Its errors are structured and bounded.
- Its unit tests pass.
- Its integration behavior is deterministic.
- Its security boundary is exercised.
- No unrelated tool or subsystem is modified.
- No new cloud or remote service is introduced.

### Final Phase 10 Exit Gate

- All 34 tools are registered.
- Every tool passes schema, permission, and execution tests.
- Web access is read-only and SSRF-safe.
- Personal data is SQLite-authoritative.
- Vision analysis reuses VisionManager.
- No prohibited capabilities are present.
- Targeted affected-module verification passes.
- Documentation and rollback records are complete.

## Rollback Strategy

- Each iteration must be independently reversible.
- Web tools are disabled through configuration or registration removal.
- Personal tools are disabled without deleting existing SQLite data.
- Reminders are disabled through scheduler lifecycle controls.
- Vision analysis is disabled without changing the existing VisionManager.
- Existing SQLite tables must use forward-compatible migrations.
- No destructive migration, database reset, or model unload behavior is permitted as rollback.
- If a tool fails its security or boundary tests, its registration must be disabled until the defect is corrected.
- Rollback must never revert completed Phase 9 architecture or the 28-tool baseline.

## Documentation Reconciliation

The stale Phase 9.5 handoff language must not be carried forward into Phase 10.

The documentation must explicitly state:

- Phase 9.0–9.4 are complete.
- Phase 9.5 and 9.6 are unrelated deferred work.
- Phase 10 begins only after explicit approval.
- Phase 10 does not continue or depend on the deferred Phase 9.5/9.6 work.
- No Phase 9.5 iteration name, continuation claim, or hidden dependency is reused in the Phase 10 plan.

## Approval Decisions Required Before Iteration 0

1. Confirm the final tool list and **34-tool total**.
2. Approve the six tool names:
   - `web.search`
   - `web.fetch`
   - `personal.note`
   - `personal.reminder`
   - `personal.calendar`
   - `vision.analyze`
3. Approve local SearXNG-compatible search as the default backend.
4. Approve BeautifulSoup/readability extraction as the default web content-processing layer.
5. Approve the exact fetch limits:
   - HTTP/HTTPS only
   - Maximum 1 MiB response body
   - Maximum 10 redirects
   - 3-second connect timeout
   - 10-second total timeout
6. Approve strict public-address validation and DNS-rebinding protection.
7. Approve SQLite as authoritative for personal notes, reminders, and calendar data.
8. Approve Qdrant remains optional and is not used for personal productivity records.
9. Approve separate personal note, reminder, and calendar tools with mutation confirmation.
10. Approve `vision.analyze` as a wrapper over existing VisionManager, not a parallel subsystem.
11. Approve the reminder recurrence model as one-shot, daily, weekly, or monthly.
12. Approve no browser automation, external calendar sync, email, messaging, cloud models, remote screenshots, or new memory subsystem.
13. Approve the iteration sequence and final exit gates above.
14. Approve that no implementation files are modified until these decisions are accepted.
15. Approve that no full test suite will be run during Phase 10 planning or initial implementation.
