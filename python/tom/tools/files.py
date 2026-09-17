"""TOM Sandboxed File Tools & Path-Traversal Guards.

Provides safe, sandboxed filesystem tools with strict boundary enforcement:
- PathGuard: path resolution, relative_to whitelist validation, traversal rejection,
  Windows reserved device name checking (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
- Tools:
  - files.read_file (SAFE)
  - files.list_directory (SAFE)
  - files.search_files (SAFE)
  - files.file_info (SAFE)
  - files.write_file (ASK_USER)
  - files.delete_file (ASK_USER)
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tom.schemas.config import ToolsConfig
from tom.security.permissions import PermissionLevel, SecurityError
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

# ---------------------------------------------------------------------------
# Windows Reserved Names
# ---------------------------------------------------------------------------

WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
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


# ---------------------------------------------------------------------------
# PathGuard Security Barrier
# ---------------------------------------------------------------------------


class PathGuard:
    """Security barrier enforcing directory sandboxing, path traversal guards,
    Windows device name rejection, and file size quotas.
    """

    def __init__(
        self,
        allowed_directories: list[str | Path] | None = None,
        max_read_bytes: int = 5_000_000,
    ) -> None:
        """Initialize PathGuard.

        Args:
            allowed_directories: List of directories within which operations are permitted.
                                 Defaults to current working directory.
            max_read_bytes: Maximum number of bytes allowed to be read in read_file.
        """
        raw_dirs = allowed_directories if allowed_directories is not None else ["."]
        self._allowed_dirs: list[Path] = [Path(d).resolve() for d in raw_dirs]
        self._max_read_bytes = max_read_bytes

    @property
    def allowed_directories(self) -> list[Path]:
        """Return resolved allowed base directories."""
        return list(self._allowed_dirs)

    @property
    def max_read_bytes(self) -> int:
        """Return maximum read bytes limit."""
        return self._max_read_bytes

    def _check_windows_reserved(self, p: Path) -> None:
        """Reject paths containing Windows reserved device names."""
        for part in p.parts:
            # Check full component and root stem (e.g. CON or CON.txt)
            clean_part = part.strip()
            stem = clean_part.split(".")[0].upper()
            if stem in WINDOWS_RESERVED_NAMES or clean_part.upper() in WINDOWS_RESERVED_NAMES:
                raise SecurityError(f"Access to Windows reserved device name '{part}' is forbidden")

    def validate_path(
        self,
        raw_path: str | Path,
        must_exist: bool = False,
        check_is_file: bool = False,
        check_is_dir: bool = False,
    ) -> Path:
        """Validate and resolve a path against sandbox constraints.

        Args:
            raw_path: Input path string or Path object.
            must_exist: Whether the path must already exist.
            check_is_file: If existing, verify it is a regular file.
            check_is_dir: If existing, verify it is a directory.

        Returns:
            Resolved, canonical Path.

        Raises:
            SecurityError: If traversal, boundary escape, or reserved names detected.
            FileNotFoundError: If must_exist is True and path does not exist.
            ValueError: If file/dir type constraints are violated.
        """
        path_str = str(raw_path)

        # 1. Traversal syntax check on raw string
        if ".." in Path(path_str).parts or "../" in path_str or "..\\" in path_str:
            raise SecurityError(f"Path traversal sequence '..' detected in '{raw_path}'")

        # 2. Windows reserved name check on raw components
        self._check_windows_reserved(Path(path_str))

        # 3. Canonical resolution
        try:
            p = Path(raw_path)
            if not p.is_absolute() and self._allowed_dirs:
                resolved = (self._allowed_dirs[0] / p).resolve()
            else:
                resolved = p.resolve()
        except Exception as exc:
            raise SecurityError(f"Failed to resolve path '{raw_path}': {exc}") from exc

        # 4. Check Windows reserved name on resolved path
        self._check_windows_reserved(resolved)

        # 5. Sandbox boundary containment check using is_relative_to
        contained = False
        for base in self._allowed_dirs:
            try:
                if resolved.is_relative_to(base):
                    contained = True
                    break
            except AttributeError:
                # Python < 3.9 compatibility fallback
                try:
                    resolved.relative_to(base)
                    contained = True
                    break
                except ValueError:
                    pass

        if not contained:
            allowed_repr = [str(b) for b in self._allowed_dirs]
            raise SecurityError(
                f"Access to path '{raw_path}' is outside allowed directories: {allowed_repr}"
            )

        # 6. Existence checks
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Path does not exist: {resolved}")

        if resolved.exists():
            if check_is_file and not resolved.is_file():
                raise ValueError(f"Path is not a regular file: {resolved}")
            if check_is_dir and not resolved.is_dir():
                raise ValueError(f"Path is not a directory: {resolved}")

        return resolved


# ---------------------------------------------------------------------------
# Pydantic Schemas for File Tools
# ---------------------------------------------------------------------------


class ReadFileInput(BaseModel):
    """Input parameters for files.read_file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Relative or absolute path to the file within sandbox")
    encoding: str = Field(default="utf-8", description="Text encoding (default: utf-8)")
    max_bytes: int | None = Field(
        default=None,
        description="Optional byte limit for reading (cannot exceed global max_read_bytes)",
    )


class ReadFileOutput(BaseModel):
    """Output results for files.read_file."""

    model_config = ConfigDict(extra="ignore")

    path: str
    content: str
    bytes_read: int
    truncated: bool


class FileInfo(BaseModel):
    """Metadata details for a single filesystem entry."""

    model_config = ConfigDict(extra="ignore")

    path: str
    name: str
    is_directory: bool
    size_bytes: int
    modified_timestamp: float
    is_readonly: bool


class ListDirectoryInput(BaseModel):
    """Input parameters for files.list_directory."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", description="Directory path to inspect within sandbox")
    recursive: bool = Field(
        default=False, description="Whether to recursively inspect subdirectories"
    )
    max_depth: int = Field(
        default=1, ge=1, le=10, description="Max recursion depth if recursive is True"
    )
    include_hidden: bool = Field(
        default=False, description="Whether to include hidden files (starting with .)"
    )


class ListDirectoryOutput(BaseModel):
    """Output results for files.list_directory."""

    model_config = ConfigDict(extra="ignore")

    directory: str
    entries: list[FileInfo]
    total_count: int


class SearchFilesInput(BaseModel):
    """Input parameters for files.search_files."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        ..., description="Glob pattern or substring to search, e.g. '*.py' or 'test'"
    )
    root_path: str = Field(default=".", description="Root search directory within sandbox")
    max_results: int = Field(default=100, ge=1, le=1000, description="Maximum matches to return")
    case_sensitive: bool = Field(default=False, description="Whether search is case-sensitive")


class SearchFilesOutput(BaseModel):
    """Output results for files.search_files."""

    model_config = ConfigDict(extra="ignore")

    pattern: str
    matches: list[FileInfo]
    count: int


class GetFileInfoInput(BaseModel):
    """Input parameters for files.file_info."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File or directory path to inspect within sandbox")


class WriteFileInput(BaseModel):
    """Input parameters for files.write_file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Destination file path within sandbox")
    content: str = Field(..., description="Text content to write")
    overwrite: bool = Field(default=False, description="Allow overwriting existing file")
    create_backup: bool = Field(
        default=True, description="Create a .bak copy if destination already exists"
    )
    encoding: str = Field(default="utf-8", description="File encoding (default: utf-8)")


class WriteFileOutput(BaseModel):
    """Output results for files.write_file."""

    model_config = ConfigDict(extra="ignore")

    path: str
    bytes_written: int
    backup_path: str | None = None


class DeleteFileInput(BaseModel):
    """Input parameters for files.delete_file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="File path to delete within sandbox")
    create_backup: bool = Field(default=True, description="Create a .bak copy before deleting")


class DeleteFileOutput(BaseModel):
    """Output results for files.delete_file."""

    model_config = ConfigDict(extra="ignore")

    path: str
    deleted: bool
    backup_path: str | None = None


# ---------------------------------------------------------------------------
# File Tool Implementations
# ---------------------------------------------------------------------------


def create_file_tools(
    path_guard: PathGuard | None = None,
    config: ToolsConfig | None = None,
) -> list[ToolDefinition]:
    """Create all sandboxed file tools bound to a PathGuard.

    Args:
        path_guard: Custom PathGuard instance. If None, created using config or defaults.
        config: Optional ToolsConfig.

    Returns:
        List of ToolDefinition instances.
    """
    if path_guard is None:
        if config is not None:
            path_guard = PathGuard(
                allowed_directories=config.allowed_directories,
                max_read_bytes=config.max_file_read_bytes,
            )
        else:
            path_guard = PathGuard()

    guard = path_guard

    async def read_file(params: ReadFileInput) -> ReadFileOutput:
        target = guard.validate_path(params.path, must_exist=True, check_is_file=True)

        file_size = target.stat().st_size
        max_bytes = guard.max_read_bytes
        if params.max_bytes is not None:
            max_bytes = min(max_bytes, params.max_bytes)

        if file_size > guard.max_read_bytes and params.max_bytes is None:
            raise ValueError(
                f"File size ({file_size} bytes) exceeds maximum read limit ({guard.max_read_bytes} bytes)"
            )

        with open(target, "rb") as f:
            raw_data = f.read(max_bytes + 1)

        truncated = len(raw_data) > max_bytes
        actual_bytes = raw_data[:max_bytes]
        text_content = actual_bytes.decode(params.encoding, errors="replace")

        return ReadFileOutput(
            path=str(target),
            content=text_content,
            bytes_read=len(actual_bytes),
            truncated=truncated,
        )

    async def list_directory(params: ListDirectoryInput) -> ListDirectoryOutput:
        target_dir = guard.validate_path(params.path, must_exist=True, check_is_dir=True)

        entries: list[FileInfo] = []

        def _collect(current: Path, depth: int) -> None:
            try:
                for item in current.iterdir():
                    if not params.include_hidden and item.name.startswith("."):
                        continue
                    try:
                        st = item.stat()
                        is_d = item.is_dir()
                        entries.append(
                            FileInfo(
                                path=str(item),
                                name=item.name,
                                is_directory=is_d,
                                size_bytes=st.st_size if not is_d else 0,
                                modified_timestamp=st.st_mtime,
                                is_readonly=not os.access(item, os.W_OK),
                            )
                        )
                        if is_d and params.recursive and depth < params.max_depth:
                            _collect(item, depth + 1)
                    except (OSError, PermissionError):
                        continue
            except (OSError, PermissionError):
                pass

        _collect(target_dir, depth=1)
        entries.sort(key=lambda x: (not x.is_directory, x.name.lower()))

        return ListDirectoryOutput(
            directory=str(target_dir),
            entries=entries,
            total_count=len(entries),
        )

    async def search_files(params: SearchFilesInput) -> SearchFilesOutput:
        root_dir = guard.validate_path(params.root_path, must_exist=True, check_is_dir=True)

        matches: list[FileInfo] = []
        pattern = params.pattern if params.case_sensitive else params.pattern.lower()

        for current_root, _dirs, files in os.walk(root_dir):
            for filename in files:
                check_name = filename if params.case_sensitive else filename.lower()
                is_match = fnmatch.fnmatch(check_name, pattern) or (pattern in check_name)
                if is_match:
                    file_path = Path(current_root) / filename
                    try:
                        st = file_path.stat()
                        matches.append(
                            FileInfo(
                                path=str(file_path),
                                name=filename,
                                is_directory=False,
                                size_bytes=st.st_size,
                                modified_timestamp=st.st_mtime,
                                is_readonly=not os.access(file_path, os.W_OK),
                            )
                        )
                    except (OSError, PermissionError):
                        continue

                    if len(matches) >= params.max_results:
                        break
            if len(matches) >= params.max_results:
                break

        return SearchFilesOutput(
            pattern=params.pattern,
            matches=matches,
            count=len(matches),
        )

    async def file_info(params: GetFileInfoInput) -> FileInfo:
        target = guard.validate_path(params.path, must_exist=True)
        st = target.stat()
        is_d = target.is_dir()
        return FileInfo(
            path=str(target),
            name=target.name,
            is_directory=is_d,
            size_bytes=st.st_size if not is_d else 0,
            modified_timestamp=st.st_mtime,
            is_readonly=not os.access(target, os.W_OK),
        )

    async def write_file(params: WriteFileInput) -> WriteFileOutput:
        target = guard.validate_path(params.path, must_exist=False)
        if target.exists() and target.is_dir():
            raise ValueError(f"Target path is a directory, cannot overwrite: {target}")

        backup_path_str: str | None = None
        if target.exists():
            if not params.overwrite:
                raise FileExistsError(f"File '{params.path}' already exists and overwrite is False")
            if params.create_backup:
                bak_candidate = target.with_suffix(target.suffix + ".bak")
                guard.validate_path(bak_candidate, must_exist=False)
                shutil.copy2(target, bak_candidate)
                backup_path_str = str(bak_candidate)

        target.parent.mkdir(parents=True, exist_ok=True)

        raw_bytes = params.content.encode(params.encoding)
        with open(target, "wb") as f:
            f.write(raw_bytes)

        return WriteFileOutput(
            path=str(target),
            bytes_written=len(raw_bytes),
            backup_path=backup_path_str,
        )

    async def delete_file(params: DeleteFileInput) -> DeleteFileOutput:
        target = guard.validate_path(params.path, must_exist=True, check_is_file=True)

        backup_path_str: str | None = None
        if params.create_backup:
            bak_candidate = target.with_suffix(target.suffix + ".bak")
            guard.validate_path(bak_candidate, must_exist=False)
            shutil.copy2(target, bak_candidate)
            backup_path_str = str(bak_candidate)

        os.remove(target)

        return DeleteFileOutput(
            path=str(target),
            deleted=True,
            backup_path=backup_path_str,
        )

    return [
        ToolDefinition(
            name="files.read_file",
            description="Read file contents safely from within the sandboxed filesystem boundary.",
            category="files",
            input_schema=ReadFileInput,
            output_schema=ReadFileOutput,
            permission_level=PermissionLevel.SAFE,
            handler=read_file,
        ),
        ToolDefinition(
            name="files.list_directory",
            description="List contents of a directory within the sandboxed filesystem boundary.",
            category="files",
            input_schema=ListDirectoryInput,
            output_schema=ListDirectoryOutput,
            permission_level=PermissionLevel.SAFE,
            handler=list_directory,
        ),
        ToolDefinition(
            name="files.search_files",
            description="Search files by glob pattern or substring within the sandboxed directory.",
            category="files",
            input_schema=SearchFilesInput,
            output_schema=SearchFilesOutput,
            permission_level=PermissionLevel.SAFE,
            handler=search_files,
        ),
        ToolDefinition(
            name="files.file_info",
            description="Get metadata attributes for a specific file or directory within the sandbox.",
            category="files",
            input_schema=GetFileInfoInput,
            output_schema=FileInfo,
            permission_level=PermissionLevel.SAFE,
            handler=file_info,
        ),
        ToolDefinition(
            name="files.write_file",
            description="Write or overwrite a file within the sandbox (requires user confirmation).",
            category="files",
            input_schema=WriteFileInput,
            output_schema=WriteFileOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=write_file,
        ),
        ToolDefinition(
            name="files.delete_file",
            description="Delete a file within the sandbox (requires user confirmation).",
            category="files",
            input_schema=DeleteFileInput,
            output_schema=DeleteFileOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=delete_file,
        ),
    ]


def register_file_tools(
    registry: ToolRegistry | None = None,
    path_guard: PathGuard | None = None,
    config: ToolsConfig | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register all sandboxed file tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry. Defaults to default_registry.
        path_guard: Optional PathGuard instance.
        config: Optional ToolsConfig.
        replace: Whether to replace existing registrations.

    Returns:
        List of registered ToolDefinitions.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_file_tools(path_guard=path_guard, config=config)
    for t in tools:
        target_registry.register(t, replace=replace)
    return tools
