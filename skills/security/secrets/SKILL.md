---
name: security-secrets
description: >
  How to handle secrets (API keys, passwords, tokens, credentials) safely in TOM.
  Use when adding a feature that requires credentials, integrating a new external service,
  setting up environment configuration, or reviewing code that may be leaking secrets.
  Also use when implementing TOM's secrets abstraction layer.
---

# Security — Secrets Management

Secrets must never be exposed to LLMs, stored in memory databases, logged, or hardcoded.

---

## What Counts as a Secret

- API keys (OpenAI, cloud services, web search)
- Passwords
- Authentication tokens (OAuth, session cookies)
- SSH credentials / private keys
- Database connection strings with credentials
- Any string that, if leaked, grants access or causes harm

---

## Core Rule

The LLM must never receive a secret directly.

```python
# Wrong
system_prompt = f"Your OpenAI key is {openai_key}"

# Correct — model receives capability, not credential
provider = await service_registry.get("openai")
# Model sees: "openai service is configured and available"
```

---

## Storage Strategy

### Development

Use environment variables via `.env` (loaded with `python-dotenv`).
Never commit `.env` files — add them to `.gitignore`.

```python
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")
```

### Production / User Deployment

Use an OS-native secrets store abstraction:
- Windows: Credential Manager (via `keyring` library)
- Linux: libsecret / keyring
- macOS: Keychain

```python
import keyring

def get_secret(service: str, key: str) -> str | None:
    return keyring.get_password(service, key)

def store_secret(service: str, key: str, value: str) -> None:
    keyring.set_password(service, key, value)
```

---

## Secrets Abstraction

TOM should have a `security/secrets.py` module that is the **only** place that reads credentials.

```python
class SecretsManager:
    def get(self, key: str) -> str | None:
        """Returns the secret value or None if not configured."""
        return os.environ.get(key) or keyring.get_password("tom", key)

    def is_configured(self, key: str) -> bool:
        """Check if a secret is available without exposing its value."""
        return self.get(key) is not None
```

Tool implementations receive the services, not the raw secrets:

```python
# Correct: tool receives a configured client
async def web_search(params, ctx):
    return await ctx.web_client.search(params.query)
    # ctx.web_client was initialized with the key — tool never sees the key
```

---

## Log Filtering

The telemetry/logging layer must filter secrets before writing to disk or console.

```python
SENSITIVE_KEYS = {"api_key", "password", "token", "secret", "credential"}

def sanitise_log_record(record: dict) -> dict:
    return {
        k: "***" if k.lower() in SENSITIVE_KEYS else v
        for k, v in record.items()
    }
```

Apply this in the structured logging middleware — do not rely on each call site to redact.

---

## Memory Safety

Secrets must never be stored in TOM's memory (SQLite/Qdrant).

```python
# memory/policies.py
def _contains_credentials(self, content: str) -> bool:
    # Pattern-match for common credential forms
    patterns = [r'(?i)(api.?key|password|token)\s*[:=]\s*\S+']
    return any(re.search(p, content) for p in patterns)
```

If a memory candidate contains credential-like content, reject it.

---

## Configuration Files

Config files (`config/*.yaml`) must not contain secrets.
They may contain placeholders:

```yaml
# config/models.yaml
providers:
  cloud:
    api_key: ${OPENAI_API_KEY}  # resolved from env at runtime
```

Never commit config files with actual key values.

---

## Checklist When Adding a New External Service

1. Does this service require credentials? → Use `SecretsManager`.
2. Is the credential stored only in env/keyring? → Verify `.gitignore` is correct.
3. Does the tool/provider expose the raw key? → Replace with capability pattern.
4. Does logging record the credential? → Add to `SENSITIVE_KEYS` filter.
5. Could the LLM prompt contain the key? → Audit the prompt construction.

---

## Related Skills

- `security/permission-model` — Permission system that surrounds tools using secrets
- `coding/validation` — Validating inputs that may carry sensitive data
