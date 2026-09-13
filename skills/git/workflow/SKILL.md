---
name: git-workflow
description: >
  Git workflow and commit conventions for TOM development. Use when creating a commit,
  writing a commit message, structuring a PR, handling a merge conflict, or setting up
  .gitignore rules. Also use when reviewing whether large files or secrets are
  accidentally staged for commit.
---

# Git Workflow — TOM Project

---

## Commit Message Format

Use conventional commits:

```
<type>(<scope>): <short description>

[optional body]

[optional footer]
```

**Types:**

| Type | Use |
|------|-----|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Internal restructure, no behaviour change |
| `test` | Adding or modifying tests |
| `docs` | Documentation only |
| `chore` | Build, config, CI, dependencies |
| `perf` | Performance improvement |
| `security` | Security fix or improvement |

**Scopes:** use the component name from the repo structure:

```
feat(rust/ipc): add protocol version checking
fix(python/memory): prevent duplicate embedding on update
test(security/permissions): add block-level tool rejection test
```

---

## Branching

Work in feature branches. Never commit directly to `main`.

```
main          — stable, always working
feat/...      — new features
fix/...       — bug fixes
refactor/...  — restructuring
```

Name branches after the component and change:

```
feat/rust-ipc-server
feat/python-memory-manager
fix/ipc-protocol-version-mismatch
```

---

## .gitignore Rules

The following must never be committed:

```gitignore
# Secrets and credentials
.env
.env.*
*.pem
*.key

# Model files
models/
*.gguf
*.safetensors
*.bin

# Runtime data
data/memory/tom.db
data/logs/
data/cache/
data/runtime/
data/benchmarks/*.json

# Build artifacts
target/        # Rust
__pycache__/
*.pyc
.venv/
dist/
build/

# IDE
.idea/
.vscode/settings.json
*.code-workspace
```

---

## Pre-Commit Checks

Before committing, verify:

- [ ] No secrets in staged files (`git diff --staged | grep -i "api_key\|password\|token"`)
- [ ] No model binary files staged (`.gguf`, `.safetensors`)
- [ ] No SQLite databases staged
- [ ] Tests pass for the changed modules
- [ ] Linting/formatting passes (`ruff check`, `cargo fmt`, `cargo clippy`)

---

## Commit Granularity

- One logical change per commit.
- Avoid "WIP" commits in final PR history (squash before merging).
- Tests for a change should be in the same commit as the change.

---

## Large Files

Never commit large binary files (model weights, audio files, images) to the repository.

Use `data/` and `models/` directories which are gitignored.
Add a download script to `scripts/download_models.py` instead.

---

## After Meaningful Work

Update the project state files (as instructed in AGENTS.md):

```
docs/STATE.md
docs/PROGRESS.md
docs/HANDOFF.md
```

Then commit them in the same PR as the implementation, or as a separate documentation commit.

---

## Related Skills

- `documentation/project-state` — Keeping state documents current
- `security/secrets` — Preventing secret commits
