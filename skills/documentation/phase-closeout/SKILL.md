---
name: documentation-phase-closeout
description: >
  How to formally close and archive a completed TOM development phase.
  Use when the final iteration of a phase is complete, the phase exit gate has passed,
  and the phase is ready for closeout. Covers updating STATE.md, PROGRESS.md, and HANDOFF.md,
  migrating unique historical context, deleting the completed phase-specific implementation plan
  (such as PHASE3_IMPLEMENTATIONPLAN.md while KEEPING master IMPLEMENTATIONPLAN.md), verifying
  deletion, purging stale references, and repository sanity checks. Trigger whenever user
  mentions phase complete, phase closeout, phase finished, phase archival, finalize phase,
  pack up phase, delete phase implementation plan, update STATE/PROGRESS/HANDOFF, or phase exit gate.
---

# Documentation — Phase Closeout & Archival

This skill defines the authoritative procedure for formally closing, documenting, and archiving a completed development phase in the TOM project.

---

## Core Philosophy

In TOM, **phase-specific implementation plans are temporary execution documents**, not permanent project memory. 

- **Permanent Project Memory**: `STATE.md`, `PROGRESS.md`, `HANDOFF.md`, and the master roadmap `IMPLEMENTATIONPLAN.md`.
- **Ephemeral Execution Guides**: `PHASE[N]_IMPLEMENTATIONPLAN.md` (e.g. `PHASE2_IMPLEMENTATIONPLAN.md`, `PHASE3_IMPLEMENTATIONPLAN.md`).

Leaving completed phase implementation plans in the repository causes future agents to mistakenly treat stale, phase-specific execution instructions as current authoritative directives. Once a phase has passed its exit gate, its unique historical decisions must be migrated into persistent documentation, its execution plan deleted, and the repository left clean for the next phase.

---

## Phase Closeout Trigger & Preconditions

### When to Use This Skill
Apply this skill **ONLY** when:
1. The final iteration of the current phase is completely implemented.
2. The full phase exit gate has been executed and passed (unit tests, integration tests, Rust tests, linting, formatting).
3. The user or workflow explicitly calls to close out, finalize, or archive the phase.

### When NOT to Use This Skill
- **During iteration work**: Do not run phase closeout while iterations are still pending.
- **When exit criteria fail**: Do not close a phase if any exit gate test or quality check is failing.
- **When planning future phases**: Planning is covered by master roadmap documents.
- **For routine task commits**: Regular task updates use `documentation/project-state` and `git/workflow`.
- **Never close a phase prematurely.**

---

## The Mandatory Closeout Workflow

Execute these steps in strict sequential order:

```text
Final iteration complete
        ↓
1. Run phase exit gate
        ↓
2. Verify all required tests & quality gates
        ↓
3. Update STATE.md
        ↓
4. Update PROGRESS.md
        ↓
5. Update HANDOFF.md
        ↓
6. Migrate unique historical information
        ↓
7. Delete completed phase implementation plan (PHASE[N]_IMPLEMENTATIONPLAN.md)
        ↓
8. Verify file deletion
        ↓
9. Search for and purge stale references
        ↓
10. Repository sanity check
        ↓
11. Mark phase formally CLOSED
```

---

### Step 1: Run Phase Exit Gate
Inspect the exit criteria defined in the phase specification and run the complete validation suite from the dedicated virtual environment (`C:\Users\vishnuu\Projects\TOM\.venv`):

```powershell
# 1. Verify virtual environment
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"

# 2. Run all Python unit and integration tests
.\.venv\Scripts\pytest.exe tests/ -v

# 3. Run Python linting and formatting checks
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .

# 4. Run Rust formatting, clippy, and engine tests
cd rust\tom-engine
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cd ..\..
```

---

### Step 2: Verify All Quality Gates
Verify that:
- Zero tests failed.
- Zero regressions against previous phase baselines.
- Zero Ruff lint violations or formatting diffs.
- Zero Cargo clippy warnings or test failures.
- All phase-specific exit criteria (e.g. latency targets, security invariants) are explicitly met.

If any check fails, **STOP**. Resolve the underlying issue before continuing closeout.

---

### Step 3: Update `STATE.md`
`STATE.md` represents the active, current snapshot of the repository.
- Mark the phase status as `COMPLETE` or `CLOSED & COMPLETE` (e.g. `Phase 3 — COMPLETE (6/6 iterations)`).
- Record the updated test baseline (Python unit, Python integration, Rust engine, total passed).
- Document new Architectural Decisions (with permanent decision IDs, rationale, and consequences).
- Set the next development target to the upcoming phase (e.g. `Phase 4 — Agent Framework & Local Model Routing`).
- Remove any stale "currently implementing" or "pending" language for the completed phase.
- Ensure no language presents the completed phase plan as an active directive.

---

### Step 4: Update `PROGRESS.md`
`PROGRESS.md` is the chronological development ledger.
- Mark all iterations of the completed phase as complete.
- Summarize key deliverables, completed modules, and architectural capabilities.
- Record the verified test counts and quality gate results.
- Record any new Architectural Decisions added during the phase.
- Establish the next phase milestone boundary.

---

### Step 5: Update `HANDOFF.md`
`HANDOFF.md` is written specifically for the next developer or fresh agent session.
- State clearly that the previous phase is **CLOSED & COMPLETE**.
- Summarize the architectural foundations built in the phase that future phases must build upon.
- Identify canonical entry points, bootstrap seams, and core abstractions (e.g. `tom.tools.bootstrap.setup_default_tools()`).
- **Explicit warning**: Instruct the next agent NOT to rebuild or redesign completed phase components.
- Point the next agent directly to the master roadmap (`IMPLEMENTATIONPLAN.md`) for the next phase.
- Purge stale iteration instructions from the completed phase.

---

### Step 6: Migrate Unique Historical Information
Before deleting the phase-specific plan:
1. Review the completed `PHASE[N]_IMPLEMENTATIONPLAN.md`.
2. Check if it contains architectural rationale, security guarantees, performance baselines, or protocol details not yet recorded in persistent documents.
3. Migrate these critical details into `STATE.md` (decisions/architecture) or `PROGRESS.md` (milestone notes).
4. **Do NOT** copy the entire execution plan verbatim. Persistent files must remain concise, readable, and focused on current state and history.

---

### Step 7: Delete the Completed Phase Implementation Plan
Delete the phase-specific execution plan for the closed phase:

```powershell
Remove-Item -Path "PHASE<N>_IMPLEMENTATIONPLAN.md" -Force
```

*(Where `<N>` is the dynamically determined completed phase number, e.g. `PHASE3_IMPLEMENTATIONPLAN.md`)*.

#### The Critical Distinction:
| Document | Action | Reason |
| :--- | :---: | :--- |
| `IMPLEMENTATIONPLAN.md` | **KEEP** | Master 15-phase roadmap. Authoritative for future phases. **NEVER DELETE.** |
| `PHASE[N]_IMPLEMENTATIONPLAN.md` | **DELETE** | Phase-specific execution plan. Stale once phase is closed. **MUST DELETE.** |

---

### Step 8: Verify Deletion
Verify programmatically that the file no longer exists:

```powershell
Test-Path "PHASE<N>_IMPLEMENTATIONPLAN.md"
# Must return: False
```

If it still exists, delete it with force and re-verify.

---

### Step 9: Search for and Purge Stale References
Search the repository for references to the deleted filename:

```powershell
git grep "PHASE<N>_IMPLEMENTATIONPLAN"
```

- **Update Stale Active References**: Remove or update references in `README.md` (file tree listings), docstrings, and configuration comments that refer to the file as an active document.
- **Preserve Legitimate Historical Logs**: If a historical decision log or git commit message mentions the plan in past tense as part of history, it does not need to be rewritten. Ensure no document points to the deleted file as a source of truth.

---

### Step 10: Repository Sanity Check
Check repository cleanliness before concluding:

```powershell
git status
```

Verify:
- Only expected files were modified (`STATE.md`, `PROGRESS.md`, `HANDOFF.md`, and references).
- The completed `PHASE[N]_IMPLEMENTATIONPLAN.md` is shown as deleted.
- Master `IMPLEMENTATIONPLAN.md` is intact and untouched.
- No unintended source code changes were made.
- No temporary files, cache artifacts, or editor junk were introduced.
- **Do not commit or push** unless the user's explicit prompt instructs to do so.

---

### Step 11: Mark Phase Formally CLOSED
Conclude the session with a concise report:
- Confirmed phase closeout status.
- Final test baseline summary.
- List of updated persistent state files.
- Confirmation of plan deletion and verification.
- Clear pointer to the next phase in the master roadmap.
- **Do NOT begin implementing the next phase.** The next phase begins in a separate session.

---

## Idempotency and Safety Rules

If an agent invokes this skill on a repository where closeout was partially completed:
1. **Already Updated Documents**: If `STATE.md`, `PROGRESS.md`, or `HANDOFF.md` already reflect the completed phase, verify their accuracy and refine if needed rather than duplicating entries.
2. **Already Deleted Plan**: If `PHASE[N]_IMPLEMENTATIONPLAN.md` is already deleted, verify its absence via `Test-Path` rather than failing or attempting to restore it.
3. **Never Recreate**: Never recreate a deleted phase implementation plan merely because a reference was found. Remove the stale reference instead.
4. **Favor Verification**: Always check actual repository state before executing destructive operations.
