"""Adversarial Path Traversal and Filesystem Security Tests (Iteration 11.5).

Verifies concrete adversarial filesystem properties against PathPolicy and PathGuard:
- URL-encoded traversal patterns (%2e%2e%2f, %252e%252e) cannot escape sandbox.
- Alternate Data Stream (ADS) colons are rejected.
- Drive-relative paths (C:file.txt) and cross-drive paths are rejected.
- Windows device-namespace paths (\\\\.\\, \\\\?\\, //./, //?/) are rejected.
- Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) are rejected.
- Symlink breakouts pointing outside sandbox are detected and rejected.
- Legitimate non-existent write targets within sandbox pass validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tom.security.permissions import SecurityError
from tom.security.policies import PathPolicy
from tom.tools.files import PathGuard


def _can_create_symlinks(tmp: Path) -> bool:
    """Check if current process has symlink creation privileges on the OS."""
    try:
        link = tmp / "_probe_symlink"
        target = tmp / "_probe_target"
        target.write_text("probe")
        link.symlink_to(target)
        link.unlink()
        target.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


# ---------------------------------------------------------------------------
# URL-Encoded Traversal Tests
# ---------------------------------------------------------------------------


class TestUrlEncodedTraversal:
    """URL-encoded traversal strings must not cause sandbox escapes."""

    def test_encoded_dots_do_not_escape_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])

        # PathPolicy treats %2e as literal string characters (no URL decode)
        # Therefore it resolves to sandbox/%2e%2e%2ffile.txt which is inside sandbox
        # or rejected if traversal syntax is somehow interpreted
        encoded_path = "%2e%2e%2fsecret.txt"
        resolved = policy.validate(encoded_path)
        # Resolved path must remain strictly inside the sandbox
        assert resolved.is_relative_to(sandbox.resolve())

    def test_double_encoded_dots_do_not_escape_sandbox(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])

        encoded_path = "%252e%252e%252fsecret.txt"
        resolved = policy.validate(encoded_path)
        assert resolved.is_relative_to(sandbox.resolve())


# ---------------------------------------------------------------------------
# Alternate Data Streams (ADS) Tests
# ---------------------------------------------------------------------------


class TestAlternateDataStreams:
    """Windows NTFS Alternate Data Stream syntax must be rejected."""

    @pytest.mark.parametrize(
        "ads_path",
        [
            "test.txt:hidden",
            "file.txt:stream:$DATA",
            "sub/nested.txt:secret",
            "log.txt:$INDEX_ALLOCATION",
        ],
    )
    def test_ads_paths_rejected(self, tmp_path: Path, ads_path: str) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])

        with pytest.raises(SecurityError, match="Alternate Data Stream"):
            policy.validate(ads_path)

    def test_pathguard_rejects_ads(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        guard = PathGuard(allowed_directories=[sandbox])

        with pytest.raises(SecurityError, match="Alternate Data Stream"):
            guard.validate_path("notes.txt:hidden_stream")


# ---------------------------------------------------------------------------
# Drive-Relative and Device-Namespace Tests
# ---------------------------------------------------------------------------


class TestWindowsPathTraps:
    """Windows-specific path traps (drive-relative, UNC, device namespaces)."""

    @pytest.mark.parametrize(
        "drive_rel",
        [
            "C:file.txt",
            "C:sub/file.txt",
            "D:data.db",
        ],
    )
    def test_drive_relative_rejected(self, tmp_path: Path, drive_rel: str) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(SecurityError, match="Drive-relative"):
            policy.validate(drive_rel)

    @pytest.mark.parametrize(
        "device_path",
        [
            r"\\.\NUL",
            r"\\.\CON",
            r"\\.\COM1",
            r"\\?\C:\Windows\System32\cmd.exe",
            "//./COM1",
            "//?/C:/secret.txt",
        ],
    )
    def test_device_namespace_rejected(self, tmp_path: Path, device_path: str) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(SecurityError, match="device-namespace"):
            policy.validate(device_path)

    @pytest.mark.parametrize(
        "reserved_name",
        [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM9",
            "LPT1",
            "LPT9",
            "CON.txt",
            "nul.log",
            "sub/COM2.dat",
            "a/b/aux.json",
        ],
    )
    def test_reserved_device_names_rejected(self, tmp_path: Path, reserved_name: str) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(SecurityError, match="reserved device"):
            policy.validate(reserved_name)


# ---------------------------------------------------------------------------
# Symlink Breakout Adversarial Tests
# ---------------------------------------------------------------------------


class TestSymlinkBreakout:
    """Symlink breakout attempts must be detected and rejected."""

    def test_symlink_escaping_sandbox_rejected(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        if not _can_create_symlinks(sandbox):
            pytest.skip("Process lacks OS symlink creation privileges")

        target_file = outside / "secret.env"
        target_file.write_text("SUPER_SECRET=1")

        escape_link = sandbox / "breakout_link"
        escape_link.symlink_to(target_file)

        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        with pytest.raises(SecurityError, match="Symlink|outside allowed"):
            policy.validate(escape_link)

    def test_symlinked_directory_breakout_rejected(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside_dir"
        outside.mkdir()
        outside_file = outside / "victim.txt"
        outside_file.write_text("critical data")

        if not _can_create_symlinks(sandbox):
            pytest.skip("Process lacks OS symlink creation privileges")

        dir_link = sandbox / "outside_link"
        dir_link.symlink_to(outside)

        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        with pytest.raises(SecurityError):
            policy.validate(dir_link / "victim.txt")


# ---------------------------------------------------------------------------
# Non-Existent Target and PathGuard Compatibility
# ---------------------------------------------------------------------------


class TestNonExistentTargetsAndPathGuard:
    """Legitimate non-existent targets inside sandbox succeed; missing parents handled safely."""

    def test_non_existent_file_inside_sandbox_allowed(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])

        target = sandbox / "new_dir" / "new_file.txt"
        resolved = policy.validate(target)
        assert resolved == target.resolve()
        assert resolved.is_relative_to(sandbox.resolve())

    def test_must_exist_flag_enforced(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])

        ghost = sandbox / "does_not_exist.txt"
        with pytest.raises(FileNotFoundError):
            policy.validate(ghost, must_exist=True)

    def test_pathguard_delegates_to_policy(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        guard = PathGuard(allowed_directories=[sandbox])

        valid_file = sandbox / "valid.txt"
        valid_file.write_text("content")

        assert guard.validate_path(valid_file) == valid_file.resolve()

        with pytest.raises(SecurityError, match="outside allowed directories"):
            guard.validate_path(tmp_path / "escape.txt")
