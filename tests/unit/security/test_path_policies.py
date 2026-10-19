r"""Tests for PathPolicy (Iteration 11.2) and PathGuard compatibility.

Coverage:
- Valid paths inside and outside allowed roots.
- Traversal variants (raw '..' in parts, mixed separators).
- Alternate Data Streams (ADS) — colon after filename.
- Drive-relative paths (C:foo) — rejected.
- Cross-drive absolute paths — rejected by containment.
- UNC paths — rejected.
- Windows device-namespace paths (\\.\ \\?\) — rejected.
- Symlink escapes (real temp-dir symlinks).
- Sensitive file blacklist (case-insensitive, nested paths, safe exclusions).
- Non-existent write targets — validated via parent chain.
- Resolution failures — fail-closed.
- PathGuard public API compatibility (allowed_directories, max_read_bytes,
  validate_path with must_exist / check_is_file / check_is_dir).
- Existing PathGuard test regression (boundary, traversal, reserved names).

Platform notes:
- Symlink tests are skipped if the process lacks the privilege to create them
  (SeCreateSymbolicLinkPrivilege on Windows, or not root/superuser on Linux).
- Junction tests are explicitly skipped on non-Windows platforms.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tom.security.permissions import SecurityError
from tom.security.policies import PathPolicy, _check_ads
from tom.tools.files import PathGuard

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_create_symlinks(tmp: Path) -> bool:
    """Return True if we can create symlinks in *tmp*."""
    try:
        link = tmp / "_probe_link"
        target = tmp / "_probe_target"
        target.write_text("x")
        link.symlink_to(target)
        link.unlink()
        target.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


# ---------------------------------------------------------------------------
# PathPolicy: Basic Containment
# ---------------------------------------------------------------------------


class TestPathPolicyContainment:
    def test_valid_file_inside_root(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        resolved = policy.validate(f)
        assert resolved == f.resolve()

    def test_valid_nested_file(self, tmp_path: Path) -> None:
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        f = sub / "data.txt"
        f.write_text("x")
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        assert policy.validate(f) == f.resolve()

    def test_reject_outside_root(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        with pytest.raises(SecurityError, match="outside allowed directories"):
            policy.validate(outside)

    def test_reject_parent_of_root(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        with pytest.raises(SecurityError):
            policy.validate(tmp_path)

    def test_multiple_roots(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        f_a = root_a / "f.txt"
        f_b = root_b / "f.txt"
        f_a.write_text("a")
        f_b.write_text("b")
        policy = PathPolicy(allowed_directories=[root_a.resolve(), root_b.resolve()])
        assert policy.validate(f_a) == f_a.resolve()
        assert policy.validate(f_b) == f_b.resolve()

    def test_no_roots_rejects_all(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[])
        with pytest.raises(SecurityError, match="No allowed directories"):
            policy.validate(tmp_path / "file.txt")

    def test_relative_path_anchored_to_first_root(self, tmp_path: Path) -> None:
        f = tmp_path / "relative.txt"
        f.write_text("x")
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        resolved = policy.validate("relative.txt")
        assert resolved == (tmp_path / "relative.txt").resolve()


# ---------------------------------------------------------------------------
# PathPolicy: Traversal
# ---------------------------------------------------------------------------


class TestPathPolicyTraversal:
    def _policy(self, tmp_path: Path) -> PathPolicy:
        return PathPolicy(allowed_directories=[tmp_path.resolve()])

    def test_double_dot_in_parts(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="traversal"):
            self._policy(tmp_path).validate("../escape.txt")

    def test_double_dot_backslash(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="traversal"):
            self._policy(tmp_path).validate("..\\escape.txt")

    def test_double_dot_nested(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="traversal"):
            self._policy(tmp_path).validate("sub/../../escape.txt")

    def test_just_double_dot(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="traversal"):
            self._policy(tmp_path).validate("..")

    def test_mixed_separators(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="traversal"):
            self._policy(tmp_path).validate("sub\\../escape.txt")

    def test_normal_dot_not_rejected(self, tmp_path: Path) -> None:
        # A single "." (current dir) should not trigger traversal rejection.
        f = tmp_path / "hello.txt"
        f.write_text("x")
        # "." resolves to CWD, which may be outside tmp_path — test containment, not traversal.
        # Just verify traversal check itself does not raise on "."
        try:
            self._policy(tmp_path).validate(".")
        except SecurityError as exc:
            assert "traversal" not in str(exc), f"Single dot triggered traversal rejection: {exc}"


# ---------------------------------------------------------------------------
# PathPolicy: Windows-Specific Path Traps
# ---------------------------------------------------------------------------


class TestPathPolicyWindowsPaths:
    def _policy(self, tmp_path: Path) -> PathPolicy:
        return PathPolicy(allowed_directories=[tmp_path.resolve()])

    def test_ads_colon_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="Alternate Data Stream"):
            self._policy(tmp_path).validate("file.txt:hidden")

    def test_ads_colon_in_subfolder(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="Alternate Data Stream"):
            self._policy(tmp_path).validate("sub/file.txt:stream")

    def test_drive_letter_colon_not_ads(self) -> None:
        """The drive-letter colon in 'C:\\foo' must NOT trigger the ADS check."""
        # Direct unit test of the ADS helper
        _check_ads("C:\\foo\\bar.txt")  # should not raise
        _check_ads("C:/foo/bar.txt")  # should not raise

    def test_ads_colon_after_drive_rejected(self) -> None:
        """C:\\foo.txt:stream should be rejected."""
        with pytest.raises(SecurityError, match="Alternate Data Stream"):
            _check_ads("C:\\foo.txt:stream")

    def test_drive_relative_path_rejected(self, tmp_path: Path) -> None:
        # "C:foo" — has a drive letter but no root separator
        with pytest.raises(SecurityError, match="Drive-relative"):
            self._policy(tmp_path).validate("C:foo.txt")

    def test_unc_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="UNC"):
            self._policy(tmp_path).validate(r"\\server\share\file.txt")

    def test_unc_forward_slash_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="UNC"):
            self._policy(tmp_path).validate("//server/share/file.txt")

    def test_device_namespace_backslash_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="device-namespace"):
            self._policy(tmp_path).validate(r"\\.\NUL")

    def test_device_namespace_extended_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="device-namespace"):
            self._policy(tmp_path).validate(r"\\?\C:\Windows\file.txt")

    def test_windows_reserved_nul(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="reserved device"):
            self._policy(tmp_path).validate("NUL")

    def test_windows_reserved_con_ext(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="reserved device"):
            self._policy(tmp_path).validate("CON.txt")

    def test_windows_reserved_lpt(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError, match="reserved device"):
            self._policy(tmp_path).validate("LPT1")

    def test_tilde_in_legitimate_filename_not_rejected(self, tmp_path: Path) -> None:
        """Filenames containing '~' that are not short-name patterns must not be rejected.
        Policy: we do NOT lexically reject '~' because it breaks legitimate filenames.
        Post-resolution containment is the mitigation."""
        f = tmp_path / "hello~world.txt"
        f.write_text("x")
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        resolved = policy.validate(f)
        assert resolved == f.resolve()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: cross-drive test")
    def test_cross_drive_absolute_rejected(self, tmp_path: Path) -> None:
        """An absolute path on a different drive must fail containment."""
        # Find a drive that is not the same as tmp_path's drive
        tmp_drive = Path(tmp_path).resolve().drive  # e.g. "C:"
        other_drive = "D:" if tmp_drive.upper() == "C:" else "C:"
        other_path = Path(other_drive + "\\Windows\\System32\\drivers\\etc\\hosts")
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(SecurityError):
            policy.validate(other_path)


# ---------------------------------------------------------------------------
# PathPolicy: Symlink Escape
# ---------------------------------------------------------------------------


class TestPathPolicySymlinks:
    @pytest.fixture()
    def sandbox(self, tmp_path: Path):
        box = tmp_path / "sandbox"
        box.mkdir()
        return box

    @pytest.fixture()
    def outside(self, tmp_path: Path):
        out = tmp_path / "outside"
        out.mkdir()
        return out

    def test_symlink_inside_sandbox_allowed(self, sandbox: Path, outside: Path) -> None:
        if not _can_create_symlinks(sandbox):
            pytest.skip("Cannot create symlinks (privilege or OS restriction)")
        target = sandbox / "real.txt"
        target.write_text("x")
        link = sandbox / "link.txt"
        link.symlink_to(target)
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        resolved = policy.validate(link)
        assert resolved == target.resolve()

    def test_symlink_escaping_sandbox_rejected(self, sandbox: Path, outside: Path) -> None:
        if not _can_create_symlinks(sandbox):
            pytest.skip("Cannot create symlinks (privilege or OS restriction)")
        secret = outside / "secret.txt"
        secret.write_text("forbidden")
        link = sandbox / "escape_link"
        link.symlink_to(secret)
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        with pytest.raises(SecurityError, match="Symlink|outside allowed"):
            policy.validate(link)

    def test_symlink_to_outside_dir_rejected(self, sandbox: Path, outside: Path) -> None:
        """A symlink to an outside directory should be rejected even if the file itself
        is referenced inside the symlinked dir."""
        if not _can_create_symlinks(sandbox):
            pytest.skip("Cannot create symlinks (privilege or OS restriction)")
        link_dir = sandbox / "linked_dir"
        link_dir.symlink_to(outside)
        policy = PathPolicy(allowed_directories=[sandbox.resolve()])
        # Referencing a file through the escaping symlink dir
        with pytest.raises(SecurityError):
            policy.validate(link_dir / "anything.txt")


# ---------------------------------------------------------------------------
# PathPolicy: Non-Existent Targets
# ---------------------------------------------------------------------------


class TestPathPolicyNonExistent:
    def test_non_existent_write_target_inside_root(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        new_file = tmp_path / "new_file.txt"
        # Should not raise — parent is inside root
        resolved = policy.validate(new_file)
        assert resolved == new_file.resolve()

    def test_non_existent_nested_target(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        nested = tmp_path / "a" / "b" / "c.txt"
        resolved = policy.validate(nested)
        assert resolved == nested.resolve()

    def test_must_exist_raises_for_missing(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(FileNotFoundError):
            policy.validate(tmp_path / "ghost.txt", must_exist=True)

    def test_check_is_file_raises_for_dir(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        with pytest.raises(ValueError, match="not a regular file"):
            policy.validate(tmp_path, must_exist=True, check_is_file=True)

    def test_check_is_dir_raises_for_file(self, tmp_path: Path) -> None:
        policy = PathPolicy(allowed_directories=[tmp_path.resolve()])
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            policy.validate(f, must_exist=True, check_is_dir=True)


# ---------------------------------------------------------------------------
# PathPolicy: Sensitive File Blacklist
# ---------------------------------------------------------------------------


class TestPathPolicySensitiveFiles:
    def _policy(self, tmp_path: Path) -> PathPolicy:
        return PathPolicy(allowed_directories=[tmp_path.resolve()])

    def _create(self, tmp_path: Path, name: str) -> Path:
        f = tmp_path / name
        f.write_text("secret")
        return f

    def test_env_file_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, ".env")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_env_local_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, ".env.local")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_env_production_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, ".env.production")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_id_rsa_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "id_rsa")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_id_rsa_pub_rejected(self, tmp_path: Path) -> None:
        # id_rsa.pub — still starts with id_rsa
        f = self._create(tmp_path, "id_rsa.pub")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_id_ed25519_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "id_ed25519")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_credentials_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "credentials")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_aws_credentials_rejected(self, tmp_path: Path) -> None:
        # "credentials" is a substring of "aws_credentials"
        f = self._create(tmp_path, "aws_credentials")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_pem_file_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "server.pem")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_key_file_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "private.key")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_known_hosts_rejected(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "known_hosts")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitivity test")
    def test_case_insensitive_windows(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "ID_RSA")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(f)

    def test_nested_ssh_id_rsa_rejected(self, tmp_path: Path) -> None:
        """Blacklist must block .ssh/id_rsa even when nested in a subdirectory."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key = ssh_dir / "id_rsa"
        key.write_text("key content")
        with pytest.raises(SecurityError, match="sensitive file"):
            self._policy(tmp_path).validate(key)

    # --- Whitelisted safe files must NOT be rejected ---

    def test_gitignore_allowed(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, ".gitignore")
        result = self._policy(tmp_path).validate(f)
        assert result == f.resolve()

    def test_pyproject_toml_allowed(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "pyproject.toml")
        result = self._policy(tmp_path).validate(f)
        assert result == f.resolve()

    def test_requirements_txt_allowed(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "requirements.txt")
        result = self._policy(tmp_path).validate(f)
        assert result == f.resolve()

    def test_normal_python_file_allowed(self, tmp_path: Path) -> None:
        f = self._create(tmp_path, "main.py")
        result = self._policy(tmp_path).validate(f)
        assert result == f.resolve()


# ---------------------------------------------------------------------------
# PathGuard Public API Compatibility
# ---------------------------------------------------------------------------


class TestPathGuardCompatibility:
    """Ensure PathGuard retains its existing public contract after delegation."""

    def test_allowed_directories_property(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        assert guard.allowed_directories == [tmp_path.resolve()]

    def test_max_read_bytes_default(self) -> None:
        guard = PathGuard()
        assert guard.max_read_bytes == 5_000_000

    def test_max_read_bytes_custom(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path], max_read_bytes=1024)
        assert guard.max_read_bytes == 1024

    def test_validate_path_returns_resolved_path(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        f = tmp_path / "hello.txt"
        f.write_text("content")
        resolved = guard.validate_path(f)
        assert resolved == f.resolve()
        assert isinstance(resolved, Path)

    def test_validate_path_must_exist(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        missing = tmp_path / "ghost.txt"
        with pytest.raises(FileNotFoundError):
            guard.validate_path(missing, must_exist=True)

    def test_validate_path_check_is_file(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        with pytest.raises(ValueError, match="not a regular file"):
            guard.validate_path(tmp_path, must_exist=True, check_is_file=True)

    def test_validate_path_check_is_dir(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            guard.validate_path(f, must_exist=True, check_is_dir=True)

    def test_traversal_still_rejected(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        guard = PathGuard(allowed_directories=[sandbox])
        with pytest.raises(SecurityError, match="traversal"):
            guard.validate_path("../escape.txt")

    def test_outside_rejected(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "other" / "file.txt"
        guard = PathGuard(allowed_directories=[sandbox])
        with pytest.raises(SecurityError, match="outside allowed directories"):
            guard.validate_path(outside)

    def test_windows_reserved_nul_rejected(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        with pytest.raises(SecurityError, match="reserved device"):
            guard.validate_path("NUL")

    def test_windows_reserved_con_txt_rejected(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        with pytest.raises(SecurityError, match="reserved device"):
            guard.validate_path("CON.txt")

    def test_default_root_is_cwd(self) -> None:
        guard = PathGuard()
        assert guard.allowed_directories == [Path.cwd().resolve()]

    def test_multiple_allowed_dirs(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        fa = a / "f.txt"
        fb = b / "g.txt"
        fa.write_text("x")
        fb.write_text("x")
        guard = PathGuard(allowed_directories=[a, b])
        assert guard.validate_path(fa) == fa.resolve()
        assert guard.validate_path(fb) == fb.resolve()
