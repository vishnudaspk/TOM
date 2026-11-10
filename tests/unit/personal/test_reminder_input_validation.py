"""Validation at the personal.reminder tool input boundary."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from tom.schemas.personal import PersonalReminderInput


def test_create_requires_future_utc_time() -> None:
    future = datetime.now(UTC) + timedelta(minutes=10)
    result = PersonalReminderInput(
        action="create",
        message="check the oven",
        fire_utc=future,
    )
    assert result.fire_utc == future


def test_create_rejects_past_time() -> None:
    with pytest.raises(ValidationError, match="fire_utc must be in the future"):
        PersonalReminderInput(
            action="create",
            message="check the oven",
            fire_utc=datetime.now(UTC) - timedelta(minutes=1),
        )


def test_update_rejects_past_time() -> None:
    with pytest.raises(ValidationError, match="fire_utc must be in the future"):
        PersonalReminderInput(
            action="update",
            reminder_id="rem_123456789abc",
            fire_utc=datetime.now(UTC) - timedelta(minutes=1),
        )


def test_create_rejects_missing_message_and_time() -> None:
    with pytest.raises(ValidationError):
        PersonalReminderInput(action="create")
