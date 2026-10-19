"""Unit tests for TOM SecretScrubber and secret detection."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from tom.security.secrets import (
    SecretScrubber,
    get_default_scrubber,
    scrub_object,
    scrub_string,
    shannon_entropy,
)

# ---------------------------------------------------------------------------
# Shannon Entropy Tests
# ---------------------------------------------------------------------------


class TestShannonEntropy:
    """Test Shannon entropy calculation."""

    def test_empty_string(self) -> None:
        assert shannon_entropy("") == 0.0

    def test_single_char(self) -> None:
        assert shannon_entropy("a") == 0.0

    def test_uniform_string(self) -> None:
        # All same character -> entropy 0
        assert shannon_entropy("aaaaaaaa") == 0.0

    def test_binary_string(self) -> None:
        # Equal 0/1 -> entropy 1.0
        entropy = shannon_entropy("01010101")
        assert abs(entropy - 1.0) < 0.01

    def test_high_entropy_random(self) -> None:
        # Random-ish string should have high entropy
        entropy = shannon_entropy("aB3$kL9@mN2#pQ5*")
        assert entropy > 3.5

    def test_low_entropy_english(self) -> None:
        # English prose has lower entropy
        entropy = shannon_entropy("this is a normal english sentence")
        assert entropy < 4.0


# ---------------------------------------------------------------------------
# Whitelist Tests
# ---------------------------------------------------------------------------


class TestWhitelist:
    """Test that benign high-entropy formats are preserved."""

    def test_uuid_preserved(self) -> None:
        scrubber = SecretScrubber()
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert scrubber.scrub_string(uuid) == uuid

    def test_sha256_preserved(self) -> None:
        scrubber = SecretScrubber()
        sha = "a" * 64
        assert scrubber.scrub_string(sha) == sha

    def test_iso_timestamp_preserved(self) -> None:
        scrubber = SecretScrubber()
        ts = "2024-01-15T10:30:45Z"
        assert scrubber.scrub_string(ts) == ts
        ts2 = "2024-01-15T10:30:45.123+00:00"
        assert scrubber.scrub_string(ts2) == ts2

    def test_data_url_preserved(self) -> None:
        scrubber = SecretScrubber()
        data_url = "data:text/plain;base64,SGVsbG8gV29ybGQ="
        assert scrubber.scrub_string(data_url) == data_url

    def test_base64_padding_preserved(self) -> None:
        scrubber = SecretScrubber()
        b64 = "SGVsbG8gV29ybGQ="
        assert scrubber.scrub_string(b64) == b64

    def test_git_sha_preserved(self) -> None:
        scrubber = SecretScrubber()
        sha = "a" * 40
        assert scrubber.scrub_string(sha) == sha


# ---------------------------------------------------------------------------
# Catalog Pattern Tests
# ---------------------------------------------------------------------------


class TestCatalogPatterns:
    """Test detection of known credential patterns."""

    def test_openai_key_legacy(self) -> None:
        scrubber = SecretScrubber()
        key = "sk-abcdefghijklmnopqrstuvwxyz123456"
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_openai_key_project(self) -> None:
        scrubber = SecretScrubber()
        key = "sk-proj-abcdefghijklmnopqrstuvwxyz123456789012345678901234"
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_anthropic_key(self) -> None:
        scrubber = SecretScrubber()
        key = "sk-ant-abcdefghijklmnopqrstuvwxyz123456"
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_github_pat_short(self) -> None:
        scrubber = SecretScrubber()
        key = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_github_pat_long(self) -> None:
        scrubber = SecretScrubber()
        key = "github_pat_" + "a" * 82
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_aws_access_key_id(self) -> None:
        scrubber = SecretScrubber()
        key = "AKIAIOSFODNN7EXAMPLE"
        result = scrubber.scrub_string(key)
        assert result == "***REDACTED***"

    def test_pem_private_key(self) -> None:
        scrubber = SecretScrubber()
        pem = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"""
        result = scrubber.scrub_string(pem)
        assert result == "***REDACTED***"

    def test_pem_ec_private_key(self) -> None:
        scrubber = SecretScrubber()
        pem = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEI...
-----END EC PRIVATE KEY-----"""
        result = scrubber.scrub_string(pem)
        assert result == "***REDACTED***"

    def test_jwt_token(self) -> None:
        scrubber = SecretScrubber()
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = scrubber.scrub_string(jwt)
        assert result == "***REDACTED***"

    def test_bearer_token(self) -> None:
        scrubber = SecretScrubber()
        bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        result = scrubber.scrub_string(bearer)
        assert result == "***REDACTED***"

    def test_multiple_patterns_in_string(self) -> None:
        scrubber = SecretScrubber()
        text = (
            "key1=sk-abcdefghijklmnopqrstuvwxyz123456 key2=sk-ant-abcdefghijklmnopqrstuvwxyz123456"
        )
        result = scrubber.scrub_string(text)
        assert result.count("***REDACTED***") == 2

    def test_pattern_in_middle_of_text(self) -> None:
        scrubber = SecretScrubber()
        text = "My API key is sk-abcdefghijklmnopqrstuvwxyz123456 for testing"
        result = scrubber.scrub_string(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "***REDACTED***" in result


# ---------------------------------------------------------------------------
# Entropy Heuristic Tests
# ---------------------------------------------------------------------------


class TestEntropyHeuristic:
    """Test Shannon entropy-based detection."""

    def test_high_entropy_flagged(self) -> None:
        scrubber = SecretScrubber()
        # 32 char random-like string with mixed case, digits, symbols
        token = "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6"
        result = scrubber.scrub_string(token)
        assert result == "***REDACTED***"

    def test_low_entropy_not_flagged(self) -> None:
        scrubber = SecretScrubber()
        # Low entropy: repetitive
        token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        result = scrubber.scrub_string(token)
        assert result == token

    def test_english_prose_not_flagged(self) -> None:
        scrubber = SecretScrubber()
        text = "this is a normal english sentence with some words"
        result = scrubber.scrub_string(text)
        # Should not redact the whole sentence
        assert "this" in result
        assert "sentence" in result

    def test_short_string_not_scanned(self) -> None:
        scrubber = SecretScrubber(min_entropy_length=24)
        # 20 chars - below threshold
        token = "aB3$kL9@mN2#pQ5*rT7!"
        result = scrubber.scrub_string(token)
        assert result == token

    def test_insufficient_char_classes_not_flagged(self) -> None:
        scrubber = SecretScrubber(min_char_classes=3)
        # Only lowercase and digits (2 classes)
        token = "abcdefghijklmnopqrstuvwxyz123456"
        result = scrubber.scrub_string(token)
        assert result == token

    def test_code_block_not_over_redacted(self) -> None:
        scrubber = SecretScrubber()
        code = """
def hello():
    print("hello world")
    x = 123456789012345678901234
    return x
"""
        result = scrubber.scrub_string(code)
        # Should preserve code structure, not over-redact
        assert "def hello" in result
        assert "print" in result


# ---------------------------------------------------------------------------
# Recursive Object Scrubbing Tests
# ---------------------------------------------------------------------------


class TestRecursiveScrubbing:
    """Test scrub_object on nested structures."""

    def test_nested_dict(self) -> None:
        scrubber = SecretScrubber()
        data = {
            "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
            "nested": {
                "token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
                "safe": "value",
            },
        }
        result = scrubber.scrub_object(data)
        assert result["api_key"] == "***REDACTED***"
        assert result["nested"]["token"] == "***REDACTED***"
        assert result["nested"]["safe"] == "value"

    def test_list_of_dicts(self) -> None:
        scrubber = SecretScrubber()
        data = [
            {"key": "sk-abcdefghijklmnopqrstuvwxyz123456"},
            {"key": "ghp_abcdefghijklmnopqrstuvwxyz123456"},
            {"safe": "value"},
        ]
        result = scrubber.scrub_object(data)
        assert result[0]["key"] == "***REDACTED***"
        assert result[1]["key"] == "***REDACTED***"
        assert result[2]["safe"] == "value"

    def test_tuple_preserved(self) -> None:
        scrubber = SecretScrubber()
        data = ("sk-abcdefghijklmnopqrstuvwxyz123456", "safe", 42)
        result = scrubber.scrub_object(data)
        assert isinstance(result, tuple)
        assert result[0] == "***REDACTED***"
        assert result[1] == "safe"
        assert result[2] == 42

    def test_set_preserved(self) -> None:
        scrubber = SecretScrubber()
        data = {
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "safe",
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
        }
        result = scrubber.scrub_object(data)
        assert isinstance(result, set)
        assert "***REDACTED***" in result
        assert "safe" in result

    def test_pydantic_model_scrubbed(self) -> None:
        scrubber = SecretScrubber()

        class Config(BaseModel):
            api_key: str
            name: str

        config = Config(api_key="sk-abcdefghijklmnopqrstuvwxyz123456", name="test")
        result = scrubber.scrub_object(config)
        assert isinstance(result, dict)
        assert result["api_key"] == "***REDACTED***"
        assert result["name"] == "test"

    def test_non_string_values_preserved(self) -> None:
        scrubber = SecretScrubber()
        data = {
            "number": 42,
            "float": 3.14,
            "bool": True,
            "none": None,
            "secret": "sk-abcdefghijklmnopqrstuvwxyz123456",
        }
        result = scrubber.scrub_object(data)
        assert result["number"] == 42
        assert result["float"] == 3.14
        assert result["bool"] is True
        assert result["none"] is None
        assert result["secret"] == "***REDACTED***"

    def test_no_mutation_of_input(self) -> None:
        scrubber = SecretScrubber()
        original = {"key": "sk-abcdefghijklmnopqrstuvwxyz123456"}
        result = scrubber.scrub_object(original)
        # Original should be unchanged
        assert original["key"] == "sk-abcdefghijklmnopqrstuvwxyz123456"
        # Result should be redacted
        assert result["key"] == "***REDACTED***"

    def test_deeply_nested(self) -> None:
        scrubber = SecretScrubber()
        data = {
            "level1": {
                "level2": [
                    {"level3": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                    "ghp_abcdefghijklmnopqrstuvwxyz123456",
                ]
            }
        }
        result = scrubber.scrub_object(data)
        assert result["level1"]["level2"][0]["level3"] == "***REDACTED***"
        assert result["level1"]["level2"][1] == "***REDACTED***"


# ---------------------------------------------------------------------------
# ToolResult and Integration Tests
# ---------------------------------------------------------------------------


class TestToolResultIntegration:
    """Test scrubbing of ToolResult-like structures."""

    def test_tool_result_success(self) -> None:
        scrubber = SecretScrubber()
        from tom.tools.registry import ToolResult

        result = ToolResult.ok(data={"token": "sk-abcdefghijklmnopqrstuvwxyz123456"})
        scrubbed = scrubber.scrub_object(result)
        assert isinstance(scrubbed, dict)  # model_dump converts to dict
        assert scrubbed["data"]["token"] == "***REDACTED***"

    def test_tool_result_failure(self) -> None:
        scrubber = SecretScrubber()
        from tom.tools.registry import ToolResult

        result = ToolResult.fail(error="Failed with key sk-abcdefghijklmnopqrstuvwxyz123456")
        scrubbed = scrubber.scrub_object(result)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in scrubbed["error"]
        assert "***REDACTED***" in scrubbed["error"]


# ---------------------------------------------------------------------------
# Fail-Safe Behavior Tests
# ---------------------------------------------------------------------------


class TestFailSafe:
    """Test fail-safe behavior on malformed inputs."""

    def test_scrub_string_exception_returns_error_marker(self) -> None:
        # This is hard to trigger without mocking internals,
        # but we verify the constant exists and is used
        from tom.security.secrets import _REDACTION_ERROR

        assert _REDACTION_ERROR == "***REDACTION_ERROR***"

    def test_scrub_object_unknown_type_preserved(self) -> None:
        # Unknown object types are preserved as-is (not scrubbed)
        class CustomObject:
            def __init__(self, value):
                self.value = value

        scrubber = SecretScrubber()
        obj = CustomObject("sk-abcdefghijklmnopqrstuvwxyz123456")
        result = scrubber.scrub_object(obj)
        assert result is obj  # Preserved as-is

    def test_pydantic_model_dump_failure_returns_error(self) -> None:
        from pydantic import BaseModel
        from tom.security.secrets import _REDACTION_ERROR

        class BadModel(BaseModel):
            def model_dump(self):
                raise RuntimeError("dump failed")

        scrubber = SecretScrubber()
        result = scrubber.scrub_object(BadModel())
        assert result == _REDACTION_ERROR


# ---------------------------------------------------------------------------
# Convenience Function Tests
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_scrub_string_function(self) -> None:
        result = scrub_string("sk-abcdefghijklmnopqrstuvwxyz123456")
        assert result == "***REDACTED***"

    def test_scrub_object_function(self) -> None:
        result = scrub_object({"key": "sk-abcdefghijklmnopqrstuvwxyz123456"})
        assert result["key"] == "***REDACTED***"

    def test_get_default_scrubber_singleton(self) -> None:
        s1 = get_default_scrubber()
        s2 = get_default_scrubber()
        assert s1 is s2


# ---------------------------------------------------------------------------
# Logging Integration Tests
# ---------------------------------------------------------------------------


class TestLoggingIntegration:
    """Test integration with telemetry.logging.redact_secrets."""

    def test_redact_secrets_keys_and_values(self) -> None:
        from tom.telemetry.logging import redact_secrets

        data = {
            "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",  # key-based
            "message": "Token is ghp_abcdefghijklmnopqrstuvwxyz123456",  # value-based
            "nested": {
                "password": "secret123",  # key-based
                "note": "JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGhpcyBpcyBhIHRlc3Q",
            },
        }
        result = redact_secrets(data)
        assert result["api_key"] == "***REDACTED***"
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in result["message"]
        assert "***REDACTED***" in result["message"]
        assert result["nested"]["password"] == "***REDACTED***"
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result["nested"]["note"]
        assert "***REDACTED***" in result["nested"]["note"]

    def test_redact_secrets_preserves_non_secrets(self) -> None:
        from tom.telemetry.logging import redact_secrets

        data = {
            "user_id": "alice",
            "action": "login",
            "metadata": {
                "ip": "192.168.1.1",
                "user_agent": "Mozilla/5.0",
            },
        }
        result = redact_secrets(data)
        assert result == data  # No changes expected


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_containers(self) -> None:
        scrubber = SecretScrubber()
        assert scrubber.scrub_object({}) == {}
        assert scrubber.scrub_object([]) == []
        assert scrubber.scrub_object(()) == ()
        assert scrubber.scrub_object(set()) == set()

    def test_none_input(self) -> None:
        scrubber = SecretScrubber()
        assert scrubber.scrub_object(None) is None
        assert scrubber.scrub_string("") == ""

    def test_unicode_strings(self) -> None:
        scrubber = SecretScrubber()
        # Unicode should not crash
        text = "sk-abcdefghijklmnopqrstuvwxyz123456 你好 世界 🎉"
        result = scrubber.scrub_string(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "你好" in result
        assert "世界" in result
        assert "🎉" in result

    def test_very_long_string_performance(self) -> None:
        scrubber = SecretScrubber()
        # 10KB string with no secrets
        text = "a" * 10000
        result = scrubber.scrub_string(text)
        assert result == text

    def test_overlapping_patterns(self) -> None:
        scrubber = SecretScrubber()
        # JWT that also looks like it could match other patterns
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGhpcyBpcyBhIHRlc3Q"
        result = scrubber.scrub_string(jwt)
        assert result == "***REDACTED***"

    def test_sha256_with_trailing_period_preserved(self) -> None:
        scrubber = SecretScrubber()
        text = "Benign hash: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855."
        result = scrubber.scrub_string(text)
        assert result == text

    def test_high_entropy_with_trailing_period(self) -> None:
        scrubber = SecretScrubber()
        text = "Secret: aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6."
        result = scrubber.scrub_string(text)
        assert result == "Secret: ***REDACTED***."

    def test_openai_proj_key_truncated_in_validation_error(self) -> None:
        scrubber = SecretScrubber()
        text = "input_value='sk-proj-mocktestkey12345...fghijklmnopqrstuvwxyz12'"
        result = scrubber.scrub_string(text)
        assert "***REDACTED***" in result
        assert "sk-proj-mocktestkey12345" not in result

    def test_secrets_embedded_in_prose_with_various_punctuation(self) -> None:
        scrubber = SecretScrubber()
        prose = (
            "Warning: found key sk-abcdefghijklmnopqrstuvwxyz123456! "
            "Did you see token aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6? "
            "Also note ghp_mocktestingpat1234567890abcdefghijkl; "
            "and bearer Bearer secrettokenvalue1234567890abcdef:, finally."
        )
        result = scrubber.scrub_string(prose)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6" not in result
        assert "ghp_mocktestingpat1234567890abcdefghijkl" not in result
        assert "secrettokenvalue1234567890abcdef" not in result
        assert "Warning: found key ***REDACTED***!" in result
        assert "Did you see token ***REDACTED***?" in result
        assert "Also note ***REDACTED***;" in result
        assert "and bearer ***REDACTED***:, finally." in result

    def test_secret_adjacent_to_redacted_content(self) -> None:
        scrubber = SecretScrubber()
        # Secret directly adjacent to a catalog key that gets redacted first
        text = "sk-abcdefghijklmnopqrstuvwxyz123456aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6"
        result = scrubber.scrub_string(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result
        assert "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6" not in result
        assert "***REDACTED***" in result

        # Pre-existing redaction marker directly preceding an entropy secret
        text2 = "***REDACTED***aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6"
        result2 = scrubber.scrub_string(text2)
        assert "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6" not in result2
        assert result2 == "***REDACTED******REDACTED***"

        # Pre-existing redaction marker directly following an entropy secret
        text3 = "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6***REDACTED***"
        result3 = scrubber.scrub_string(text3)
        assert "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6" not in result3
        assert result3 == "***REDACTED******REDACTED***"

    def test_entropy_calculation_failure_fails_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tom.security.secrets as sec_mod

        scrubber = SecretScrubber()
        token = "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6."

        def mock_entropy(t: str) -> float:
            raise RuntimeError("Entropy calculation crashed")

        monkeypatch.setattr(sec_mod, "shannon_entropy", mock_entropy)
        result = scrubber.scrub_string(token)
        # Must fail safe to REDACTION_ERROR rather than leaking cleartext
        assert "aB3$kL9@mN2#pQ5*rT7!vW1^xY4&zA6" not in result
        assert sec_mod._REDACTION_ERROR in result
        assert result == "***REDACTION_ERROR***."
