"""Pytest configuration for e2e tests with log isolation."""
import os
import tempfile
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_logs():
    """Isolate e2e test logs to a temporary file with unique session ID."""
    # Create a temp log file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", prefix="tom_e2e_", delete=False
    ) as f:
        log_file = f.name

    # Set environment variables for log isolation
    old_log_file = os.environ.get("TOM_LOG_FILE")
    old_session_id = os.environ.get("TOM_SESSION_ID")

    session_id = uuid.uuid4().hex[:8]
    os.environ["TOM_LOG_FILE"] = log_file
    os.environ["TOM_SESSION_ID"] = session_id

    yield {"log_file": log_file, "session_id": session_id}

    # Restore environment
    if old_log_file is not None:
        os.environ["TOM_LOG_FILE"] = old_log_file
    else:
        os.environ.pop("TOM_LOG_FILE", None)

    if old_session_id is not None:
        os.environ["TOM_SESSION_ID"] = old_session_id
    else:
        os.environ.pop("TOM_SESSION_ID", None)

    # Clean up temp log file
    try:
        os.unlink(log_file)
    except OSError:
        pass