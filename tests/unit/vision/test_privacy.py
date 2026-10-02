"""Unit tests for TOM vision privacy shield and ephemeral lifecycle.

Adheres to Phase 7 Iteration 1 requirements and skills/testing/python-testing:
- Window title pattern blacklist matching (case-insensitive, wildcard, substring)
- Pre-capture blocking with SensitiveScreenContentError
- Regex redaction of credit cards, SSNs, and API keys
- Ephemeral in-memory lifecycle enforcement
- Telemetry log sanitation against raw bytes and base64 images
"""

import pytest
from tom.schemas.vision import CapturedFrame
from tom.vision.privacy import (
    PrivacyShield,
    SensitiveScreenContentError,
)


def test_default_patterns_loaded():
    """Verify default sensitive patterns include password managers, banking, and incognito."""
    shield = PrivacyShield()
    assert "*password*" in shield.sensitive_patterns
    assert "*bank*" in shield.sensitive_patterns
    assert "*incognito*" in shield.sensitive_patterns
    assert "*bitwarden*" in shield.sensitive_patterns


@pytest.mark.parametrize(
    "title,expected_sensitive",
    [
        ("Bitwarden - Password Manager", True),
        ("1Password - Vault", True),
        ("KeePass - Passwords.kdbx", True),
        ("Chase Bank - Online Banking - Google Chrome", True),
        ("Wells Fargo Bank - Accounts", True),
        ("New Incognito Tab - Google Chrome", True),
        ("Private Browsing - Mozilla Firefox", True),
        ("PASSWORD RESET REQUIRED", True),
        ("Visual Studio Code - main.py", False),
        ("Terminal - powershell.exe", False),
        ("Calculator", False),
        ("README.md - TOM", False),
        ("", False),
        (None, False),
    ],
)
def test_window_title_sensitivity_matching(title: str | None, expected_sensitive: bool):
    """Verify case-insensitive and wildcard pattern matching on window titles."""
    shield = PrivacyShield()
    assert shield.is_window_sensitive(title) == expected_sensitive


def test_custom_sensitive_patterns():
    """Verify configuring custom patterns works as expected."""
    shield = PrivacyShield(sensitive_patterns=["*confidential*", "*tax*"])
    assert shield.is_window_sensitive("2025 Tax Return.pdf") is True
    assert shield.is_window_sensitive("Confidential Project Plan") is True
    assert shield.is_window_sensitive("My Bank Account") is False


def test_assert_capture_allowed_raises_on_sensitive_foreground():
    """Verify assert_capture_allowed raises SensitiveScreenContentError when sensitive window is active."""
    shield = PrivacyShield(foreground_window_provider=lambda: "Bitwarden - Master Password")
    with pytest.raises(SensitiveScreenContentError) as exc_info:
        shield.assert_capture_allowed()
    assert "Bitwarden - Master Password" in str(exc_info.value)
    assert "bitwarden" in str(exc_info.value).lower()


def test_assert_capture_allowed_passes_on_safe_foreground():
    """Verify assert_capture_allowed succeeds quietly when foreground window is safe."""
    shield = PrivacyShield(foreground_window_provider=lambda: "TOM - Development Console")
    # Should not raise
    shield.assert_capture_allowed()


def test_filter_extracted_text_credit_cards():
    """Verify credit card number redaction in various formatting styles."""
    shield = PrivacyShield()
    text = (
        "Customer card: 4111 2222 3333 4444 and secondary 5500-0000-0000-0004 "
        "and unspaced 4111222233334444 should be redacted."
    )
    filtered = shield.filter_extracted_text(text)
    assert "4111 2222 3333 4444" not in filtered
    assert "5500-0000-0000-0004" not in filtered
    assert "4111222233334444" not in filtered
    assert "[REDACTED_CREDIT_CARD]" in filtered


def test_filter_extracted_text_ssn():
    """Verify US Social Security Number redaction."""
    shield = PrivacyShield()
    text = "User SSN is 123-45-6789 and must never leak."
    filtered = shield.filter_extracted_text(text)
    assert "123-45-6789" not in filtered
    assert "[REDACTED_SSN]" in filtered


def test_filter_extracted_text_api_keys_and_tokens():
    """Verify API keys and bearer tokens are properly redacted."""
    shield = PrivacyShield()
    text = (
        "Authorization: Bearer abcd1234efgh5678ijkl9012mnop3456\n"
        "api_key: TEST_API_123545678900000000000000000\n"
        "github_key: ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
        "secret_key = 'abcdef1234567890abcdef12'\n"
        "password='SecretPassword123!'"
    )
    filtered = shield.filter_extracted_text(text)
    assert "TEST_API_123545678900000000000000000" not in filtered
    assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in filtered
    assert "SecretPassword123!" not in filtered
    assert "[REDACTED_TOKEN]" in filtered or "[REDACTED_API_KEY]" in filtered
    assert "[REDACTED_PASSWORD]" in filtered


def test_enforce_ephemeral_lifecycle():
    """Verify ephemeral lifecycle enforcement clears raw bytes and verifies ephemeral flag."""
    shield = PrivacyShield()
    frame = CapturedFrame(
        width=100,
        height=100,
        channels=4,
        format="RGBA",
        raw_bytes=b"\x00" * 40000,
        is_ephemeral=True,
    )
    assert len(frame.raw_bytes) == 40000
    shield.enforce_ephemeral_lifecycle(frame)
    assert frame.raw_bytes == b""

    non_ephemeral_frame = CapturedFrame(
        width=100,
        height=100,
        channels=4,
        format="RGBA",
        raw_bytes=b"\x00" * 40000,
        is_ephemeral=False,
    )
    with pytest.raises(ValueError, match="is_ephemeral=True"):
        shield.enforce_ephemeral_lifecycle(non_ephemeral_frame)


def test_sanitize_log_metadata_omits_bytes_and_base64():
    """Verify log sanitization strips raw bytes and base64 data URLs."""
    raw_payload = {
        "event": "capture_complete",
        "frame_bytes": b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        "data_url": "data:image/png;base64," + "A" * 100,
        "width": 1920,
        "height": 1080,
        "nested": {
            "more_bytes": b"binary_data",
        },
    }
    sanitized = PrivacyShield.sanitize_log_metadata(raw_payload)
    assert isinstance(sanitized["frame_bytes"], str)
    assert "BYTES_OMITTED" in sanitized["frame_bytes"]
    assert sanitized["data_url"] == "[IMAGE_PAYLOAD_REDACTED]"
    assert sanitized["width"] == 1920
    assert "BYTES_OMITTED" in sanitized["nested"]["more_bytes"]
