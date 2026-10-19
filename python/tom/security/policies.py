"""TOM Security Subsystem — Centralized Filesystem Path Policy.

PathPolicy provides a single, authoritative implementation of Windows-hardened
filesystem path validation used by all file-accessing tools via PathGuard.

Security checks performed in order:
1. Raw lexical checks on the input string (before any resolution).
2. Canonical resolution: Path.resolve() + os.path.realpath() for symlink/junction expansion.
3. Sandbox containment: resolved path must be inside an allowed base directory.
4. Symlink escape check: walk ancestry of the resolved path and reject any symlink
   that resolves outside the sandbox (best-effort; TOCTOU limitations documented).
5. Sensitive file blacklist: case-insensitive match of the file's base name.

Residual Limitations (documented, not silently ignored):
- TOCTOU: Python user-space validation cannot atomically prevent a hostile concurrent
  process from swapping a directory with an NTFS junction between validate_path() and
  the subsequent open() call. These checks mitigate accidental and agent-driven
  traversal but not adversarial kernel-level races.
- 8.3 Short Names: Python's Path.resolve() on Windows expands 8.3 aliases because
  the kernel returns the long name. Lexical rejection of '~' digits is NOT applied
  because it would break legitimate filenames like 'hello~world.txt'. The short-name
  vector is mitigated by the post-resolution containment check.
- Encoded Traversal: Windows filesystem paths are NOT URL-decoded by the OS.
  We check for '..' after pathlib normalisation (which handles '/', '\\', and mixed
  separators) but do NOT apply URL-decoding; doing so would misinterpret filenames
  that legitimately contain '%2e' characters.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from tom.security.permissions import SecurityError

# ---------------------------------------------------------------------------
# Sensitive File Blacklist (case-insensitive stem / name matching)
# ---------------------------------------------------------------------------

# Exact file names that must never be opened or written, regardless of path.
_SENSITIVE_EXACT_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.test",
        ".env.staging",
        "credentials",
        ".credentials",
        "known_hosts",
    }
)

# Patterns (as compiled regex on the lower-cased filename).
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^id_rsa"),
    re.compile(r"^id_ed25519"),
    re.compile(r"^id_ecdsa"),
    re.compile(r"^id_dsa"),
    re.compile(r"^id_xmss"),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"credentials"),  # substring — catches aws_credentials, etc.
)

# Legitimate project files explicitly whitelisted (bypass sensitive check).
_SAFE_NAMES: frozenset[str] = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "ruff.toml",
        "mypy.ini",
        ".flake8",
        "tools.yaml",
        "config.yaml",
    }
)

# ---------------------------------------------------------------------------
# Windows Device-Namespace Prefixes
# ---------------------------------------------------------------------------

# Prefixes that route to Windows device namespace; always reject.
_DEVICE_PREFIXES: tuple[str, ...] = (
    "\\\\.\\",  # \\.\  — legacy device namespace
    "\\\\?\\",  # \\?\  — extended-length path namespace
    "//./",  # forward-slash variants
    "//?/",
)


# ---------------------------------------------------------------------------
# PathPolicy
# ---------------------------------------------------------------------------


class PathPolicy:
    """Centralized, Windows-hardened filesystem path validation policy.

    Used by PathGuard in python/tom/tools/files.py; not called directly by tools.
    All security decisions raise SecurityError on violation.
    """

    def __init__(
        self,
        allowed_directories: list[Path],
    ) -> None:
        """Initialise with pre-resolved allowed base directories.

        Args:
            allowed_directories: Caller-resolved base directories (already absolute).
                                 An empty list means no path is ever allowed.
        """
        self._allowed: list[Path] = allowed_directories

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        raw_path: str | Path,
        *,
        must_exist: bool = False,
        check_is_file: bool = False,
        check_is_dir: bool = False,
    ) -> Path:
        """Validate *raw_path* against all security policies.

        Returns the canonically resolved Path if all checks pass.

        Raises:
            SecurityError: On any policy violation (traversal, escape, ADS, etc.).
            FileNotFoundError: If must_exist is True and path is absent.
            ValueError: If check_is_file or check_is_dir fails.
        """
        path_str = str(raw_path)

        # 1. Raw lexical checks (before resolution)
        self._check_raw_lexical(path_str)

        # 2. Resolve to canonical absolute path
        resolved = self._resolve(raw_path, path_str)

        # 3. Windows reserved device names on resolved components
        self._check_windows_reserved(resolved)

        # 4. Sandbox containment
        self._check_contained(resolved, raw_path)

        # 5. Symlink / reparse-point escape check (best-effort)
        self._check_no_symlink_escape(resolved)

        # 6. Sensitive file blacklist
        self._check_sensitive(resolved, raw_path)

        # 7. Existence and type checks
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path does not exist: {resolved}")
        if resolved.exists():
            if check_is_file and not resolved.is_file():
                raise ValueError(f"Path is not a regular file: {resolved}")
            if check_is_dir and not resolved.is_dir():
                raise ValueError(f"Path is not a directory: {resolved}")

        return resolved

    # ------------------------------------------------------------------
    # Internal Checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_raw_lexical(path_str: str) -> None:
        """Reject known-bad patterns on the raw (unresolved) path string."""
        # --- Traversal: '..' components ---
        # pathlib normalises separators; check both slash forms on the raw string.
        p = Path(path_str)
        if ".." in p.parts:
            raise SecurityError(f"Path traversal sequence '..' detected in '{path_str}'")
        # Belt-and-suspenders for raw strings with mixed separators pathlib may not split
        if "../" in path_str or "..\\" in path_str:
            raise SecurityError(f"Path traversal sequence '..' detected in '{path_str}'")

        # --- Windows device namespaces: \\.\  \\?\  and forward-slash equivalents ---
        lower = path_str.lower().replace("/", "\\")
        if lower.startswith("\\\\.\\") or lower.startswith("\\\\?\\"):
            raise SecurityError(f"Windows device-namespace path rejected: '{path_str}'")

        # --- Alternate Data Streams: colon after the drive specifier ---
        # Valid: "C:\foo\bar" — one colon at index 1 followed by \ or /
        # Invalid: "foo.txt:stream", "C:\foo:stream"
        # Strategy: after stripping a leading drive letter colon, any remaining
        # colon in the path is an ADS indicator.
        _check_ads(path_str)

        # --- Drive-relative paths: "C:foo" (drive letter present but no root separator) ---
        # These resolve relative to the per-drive CWD, not the allowed root.
        if p.drive and not p.root:
            raise SecurityError(
                f"Drive-relative path rejected (use an absolute or sandbox-relative path): "
                f"'{path_str}'"
            )

        # --- UNC paths: \\server\share or //server/share ---
        # Reject all UNC paths; they bypass the local sandbox entirely.
        if p.drive.startswith("\\\\") or path_str.startswith("//"):
            raise SecurityError(f"UNC path rejected: '{path_str}'")

    def _resolve(self, raw_path: str | Path, path_str: str) -> Path:
        """Resolve the path canonically, fail closed on any error."""
        try:
            p = Path(raw_path)
            if not p.is_absolute() and self._allowed:
                # Anchor relative paths inside the first allowed root
                candidate = self._allowed[0] / p
            else:
                candidate = p
            # Path.resolve() on Windows expands 8.3 short names and
            # follows junctions/symlinks one level; os.path.realpath
            # follows the full chain. We use realpath for the final check.
            resolved = Path(os.path.realpath(candidate.resolve()))
        except Exception as exc:
            raise SecurityError(f"Failed to resolve path '{path_str}': {exc}") from exc
        return resolved

    @staticmethod
    def _check_windows_reserved(p: Path) -> None:
        """Reject Windows reserved device names in any path component."""
        for part in p.parts:
            stem = part.split(".")[0].upper().strip()
            if stem in _WINDOWS_RESERVED_NAMES or part.upper().strip() in _WINDOWS_RESERVED_NAMES:
                raise SecurityError(f"Access to Windows reserved device name '{part}' is forbidden")

    def _check_contained(self, resolved: Path, raw_path: str | Path) -> None:
        """Raise SecurityError if resolved path is outside all allowed roots."""
        if not self._allowed:
            raise SecurityError("No allowed directories configured; all access denied")
        for base in self._allowed:
            if _is_relative_to(resolved, base):
                return
        allowed_repr = [str(b) for b in self._allowed]
        raise SecurityError(
            f"Access to path '{raw_path}' is outside allowed directories: {allowed_repr}"
        )

    def _check_no_symlink_escape(self, resolved: Path) -> None:
        """Walk ancestry and reject any symlink that escapes the sandbox.

        Limitation: this is a best-effort check. A concurrent process could
        replace a checked ancestor directory with a junction after this check
        completes (TOCTOU). Python user-space cannot prevent that race.
        """
        # Walk from the resolved path upward. For each component that exists
        # and is a symlink, check that its *real* target is still inside a root.
        current = resolved
        while True:
            if current.is_symlink():
                link_target = Path(os.path.realpath(current))
                # The link target itself must be inside an allowed root
                contained = any(_is_relative_to(link_target, base) for base in self._allowed)
                if not contained:
                    raise SecurityError(
                        f"Symlink '{current}' resolves outside allowed directories: '{link_target}'"
                    )
            parent = current.parent
            if parent == current:
                break
            current = parent

    @staticmethod
    def _check_sensitive(resolved: Path, raw_path: str | Path) -> None:
        """Reject access to sensitive credential files by base name."""
        name_lower = resolved.name.lower()

        # Whitelist beats blacklist for known-safe project files
        if name_lower in _SAFE_NAMES:
            return

        if name_lower in _SENSITIVE_EXACT_NAMES:
            raise SecurityError(f"Access to sensitive file '{resolved.name}' is forbidden")
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(name_lower):
                raise SecurityError(f"Access to sensitive file '{resolved.name}' is forbidden")


# ---------------------------------------------------------------------------
# Module-Level Helpers
# ---------------------------------------------------------------------------


def _is_relative_to(path: Path, base: Path) -> bool:
    """Return True if *path* is inside *base* (component-aware, no string prefix tricks)."""
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _check_ads(path_str: str) -> None:
    """Raise SecurityError if the path contains an Alternate Data Stream colon.

    Rules:
    - A drive letter colon at position 1 ('C:') is always allowed.
    - Any additional colon in the remainder of the string is an ADS indicator.
    """
    remainder = path_str
    # Strip a leading drive letter ("C:" or "c:")
    if len(remainder) >= 2 and remainder[1] == ":" and remainder[0].isalpha():
        remainder = remainder[2:]
    if ":" in remainder:
        raise SecurityError(f"Alternate Data Stream path rejected: '{path_str}'")


# ---------------------------------------------------------------------------
# Windows Reserved Names (same set as PathGuard in files.py)
# ---------------------------------------------------------------------------

_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)
