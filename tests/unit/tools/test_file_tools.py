"""Unit tests for TOM Sandboxed File Tools & Path-Traversal Guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tom.security.confirmation import ConfirmationHook, ConfirmationRequest
from tom.security.permissions import (
    PermissionDeniedError,
    PermissionLevel,
    SecurityError,
)
from tom.tools.executor import ToolExecutor
from tom.tools.files import (
    DeleteFileOutput,
    FileInfo,
    ListDirectoryOutput,
    PathGuard,
    ReadFileOutput,
    SearchFilesOutput,
    WriteFileOutput,
    register_file_tools,
)
from tom.tools.registry import ToolRegistry


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class AutoApproveHook(ConfirmationHook):
    async def request_confirmation(self, req: ConfirmationRequest) -> bool:
        return True


class AutoDenyHook(ConfirmationHook):
    async def request_confirmation(self, req: ConfirmationRequest) -> bool:
        return False


# ===========================================================================
# 1. PathGuard Security Tests
# ===========================================================================


class TestPathGuard:
    """Test PathGuard sandboxing, normalization, and traversal guards."""

    def test_allowed_path_resolution(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        sample = tmp_path / "hello.txt"
        sample.write_text("content", encoding="utf-8")

        resolved = guard.validate_path("hello.txt")  # if relative to cwd? wait
        # Testing relative to base or absolute inside tmp_path:
        resolved = guard.validate_path(sample)
        assert resolved == sample.resolve()

    def test_rejection_outside_allowed(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("forbidden", encoding="utf-8")

        guard = PathGuard(allowed_directories=[sandbox])

        with pytest.raises(SecurityError, match="outside allowed directories"):
            guard.validate_path(secret)

    def test_rejection_traversal_syntax(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        guard = PathGuard(allowed_directories=[sandbox])

        traversal_attempts = [
            "../secret.txt",
            "..\\secret.txt",
            "subdir/../../outside.txt",
            "..",
        ]
        for attempt in traversal_attempts:
            with pytest.raises(SecurityError, match="traversal"):
                guard.validate_path(attempt)

    def test_rejection_windows_reserved_names(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])

        reserved_names = [
            "CON",
            "con",
            "con.txt",
            "PRN",
            "aux",
            "AUX.json",
            "NUL",
            "COM1",
            "com9",
            "LPT1",
            "lpt3.log",
        ]
        for name in reserved_names:
            with pytest.raises(SecurityError, match="Windows reserved device name"):
                guard.validate_path(name)

    def test_must_exist_flag(self, tmp_path: Path) -> None:
        guard = PathGuard(allowed_directories=[tmp_path])
        non_existent = tmp_path / "missing.txt"

        with pytest.raises(FileNotFoundError):
            guard.validate_path(non_existent, must_exist=True)


# ===========================================================================
# 2. File Tools Integration Tests via ToolExecutor
# ===========================================================================


class TestFileToolsWithExecutor:
    """Test sandboxed file tools execution, permissions, and safeguards."""

    @pytest.fixture
    def sandbox_dir(self, tmp_path: Path) -> Path:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        return sandbox

    @pytest.fixture
    def guard(self, sandbox_dir: Path) -> PathGuard:
        return PathGuard(allowed_directories=[sandbox_dir], max_read_bytes=10_000)

    @pytest.fixture
    def registry(self, guard: PathGuard) -> ToolRegistry:
        reg = ToolRegistry()
        register_file_tools(registry=reg, path_guard=guard)
        return reg

    def test_registered_file_tools_metadata(self, registry: ToolRegistry) -> None:
        assert len(registry) == 6
        assert registry.get("files.read_file").permission_level is PermissionLevel.SAFE
        assert registry.get("files.list_directory").permission_level is PermissionLevel.SAFE
        assert registry.get("files.search_files").permission_level is PermissionLevel.SAFE
        assert registry.get("files.file_info").permission_level is PermissionLevel.SAFE
        assert registry.get("files.write_file").permission_level is PermissionLevel.ASK_USER
        assert registry.get("files.delete_file").permission_level is PermissionLevel.ASK_USER

    def test_read_file_success(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        target = sandbox_dir / "note.txt"
        target.write_text("Hello TOM!", encoding="utf-8")

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("files.read_file", {"path": str(target)}))
        assert res.success is True
        assert isinstance(res.data, ReadFileOutput)
        assert res.data.content == "Hello TOM!"
        assert res.data.bytes_read == len(b"Hello TOM!")
        assert res.data.truncated is False

    def test_read_file_truncation(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        target = sandbox_dir / "long.txt"
        target.write_text("0123456789" * 10, encoding="utf-8")  # 100 bytes

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("files.read_file", {"path": str(target), "max_bytes": 20}))
        assert res.success is True
        assert isinstance(res.data, ReadFileOutput)
        assert len(res.data.content) == 20
        assert res.data.truncated is True

    def test_read_file_exceeds_max_limit(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        target = sandbox_dir / "large.bin"
        target.write_bytes(b"X" * 15_000)  # max_read_bytes in fixture is 10_000

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("files.read_file", {"path": str(target)}))
        assert res.success is False
        assert "exceeds maximum read limit" in (res.error or "")

    def test_read_file_traversal_blocked(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("files.read_file", {"path": "../secret.txt"}))
        assert res.success is False
        assert "traversal" in (res.error or "").lower()

    def test_list_directory_flat_and_recursive(
        self, sandbox_dir: Path, registry: ToolRegistry
    ) -> None:
        (sandbox_dir / "f1.txt").write_text("1", encoding="utf-8")
        (sandbox_dir / "f2.txt").write_text("2", encoding="utf-8")
        sub = sandbox_dir / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("3", encoding="utf-8")

        executor = ToolExecutor(registry=registry)

        # Flat listing
        res_flat = run_async(
            executor.execute("files.list_directory", {"path": str(sandbox_dir), "recursive": False})
        )
        assert res_flat.success is True
        assert isinstance(res_flat.data, ListDirectoryOutput)
        names = {e.name for e in res_flat.data.entries}
        assert "f1.txt" in names
        assert "f2.txt" in names
        assert "subdir" in names
        assert "nested.txt" not in names

        # Recursive listing
        res_rec = run_async(
            executor.execute(
                "files.list_directory",
                {"path": str(sandbox_dir), "recursive": True, "max_depth": 2},
            )
        )
        assert res_rec.success is True
        names_rec = {e.name for e in res_rec.data.entries}
        assert "nested.txt" in names_rec

    def test_search_files(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        (sandbox_dir / "alpha.py").write_text("# py", encoding="utf-8")
        (sandbox_dir / "beta.txt").write_text("txt", encoding="utf-8")
        (sandbox_dir / "gamma.py").write_text("# py", encoding="utf-8")

        executor = ToolExecutor(registry=registry)
        res = run_async(
            executor.execute(
                "files.search_files",
                {"pattern": "*.py", "root_path": str(sandbox_dir)},
            )
        )
        assert res.success is True
        assert isinstance(res.data, SearchFilesOutput)
        assert res.data.count == 2
        names = {m.name for m in res.data.matches}
        assert names == {"alpha.py", "gamma.py"}

    def test_file_info(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        target = sandbox_dir / "info.txt"
        target.write_text("some content", encoding="utf-8")

        executor = ToolExecutor(registry=registry)
        res = run_async(executor.execute("files.file_info", {"path": str(target)}))
        assert res.success is True
        assert isinstance(res.data, FileInfo)
        assert res.data.name == "info.txt"
        assert res.data.is_directory is False
        assert res.data.size_bytes == len(b"some content")

    def test_write_file_with_confirmation_and_backup(
        self, sandbox_dir: Path, registry: ToolRegistry
    ) -> None:
        executor = ToolExecutor(registry=registry, confirmation_hook=AutoApproveHook())

        target = sandbox_dir / "doc.txt"
        # 1. First write
        res1 = run_async(
            executor.execute(
                "files.write_file",
                {"path": str(target), "content": "Initial version", "overwrite": False},
            )
        )
        assert res1.success is True
        assert isinstance(res1.data, WriteFileOutput)
        assert target.read_text(encoding="utf-8") == "Initial version"
        assert res1.data.backup_path is None

        # 2. Overwrite with backup
        res2 = run_async(
            executor.execute(
                "files.write_file",
                {
                    "path": str(target),
                    "content": "Updated version",
                    "overwrite": True,
                    "create_backup": True,
                },
            )
        )
        assert res2.success is True
        assert target.read_text(encoding="utf-8") == "Updated version"
        assert res2.data.backup_path is not None
        bak = Path(res2.data.backup_path)
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "Initial version"

    def test_write_file_denied_by_user(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry, confirmation_hook=AutoDenyHook())

        target = sandbox_dir / "denied.txt"
        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(
                executor.execute(
                    "files.write_file",
                    {"path": str(target), "content": "Will be denied"},
                )
            )
        assert "denied" in str(exc_info.value).lower()
        assert not target.exists()

    def test_delete_file_with_backup(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry, confirmation_hook=AutoApproveHook())

        target = sandbox_dir / "temp.txt"
        target.write_text("to be deleted", encoding="utf-8")

        res = run_async(
            executor.execute(
                "files.delete_file",
                {"path": str(target), "create_backup": True},
            )
        )
        assert res.success is True
        assert isinstance(res.data, DeleteFileOutput)
        assert res.data.deleted is True
        assert not target.exists()
        assert res.data.backup_path is not None
        bak = Path(res.data.backup_path)
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "to be deleted"

    def test_delete_file_denied_by_user(self, sandbox_dir: Path, registry: ToolRegistry) -> None:
        executor = ToolExecutor(registry=registry, confirmation_hook=AutoDenyHook())

        target = sandbox_dir / "keep.txt"
        target.write_text("keep me", encoding="utf-8")

        with pytest.raises(PermissionDeniedError) as exc_info:
            run_async(executor.execute("files.delete_file", {"path": str(target)}))
        assert "denied" in str(exc_info.value).lower()
        assert target.exists()
