"""LM Studio and OpenAI-compatible HTTP Model Provider.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- Default endpoint: http://localhost:1234/v1 (or http://127.0.0.1:1234/v1)
"""

from tom.models.providers.http import HttpModelProvider, LMStudioProvider

__all__ = [
    "HttpModelProvider",
    "LMStudioProvider",
]
