"""Code/file tool — read, write, and search files within allowed workspaces."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Iterable

from mybot.tools.base import BaseTool, ToolResult

DEFAULT_WORKSPACE_DIRS: tuple[str, ...] = ("/Users/ddn/Developer",)

# Max file bytes to read in a single call to avoid blowing up context.
MAX_READ_BYTES = 1_000_000
# Max total bytes streamed back from a search.
MAX_SEARCH_BYTES = 200_000
# Default glob for search when not specified.
DEFAULT_GLOB = "**/*"

# Directories we never recurse into during search (noise + perf).
_SEARCH_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".venv", "venv", ".tox", "dist", "build", ".idea",
    ".vscode", ".ruff_cache", "target",
})


class CodeTool(BaseTool):
    """Read / write / search files. Restricted to configured workspace directories."""

    name = "code"
    description = (
        "Read, write, or search files within the configured workspace directories. "
        "Supports three operations: 'read_file' (optionally with start_line/end_line), "
        "'write_file' (creates or overwrites), and 'search_files' (grep-like with glob filter)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read_file", "write_file", "search_files"],
                "description": "Which operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "Absolute file path (for read_file / write_file) or directory to search in (for search_files, optional — defaults to first workspace dir).",
            },
            "content": {
                "type": "string",
                "description": "File content (for write_file).",
            },
            "start_line": {
                "type": "integer",
                "description": "1-indexed starting line (for read_file, optional).",
            },
            "end_line": {
                "type": "integer",
                "description": "1-indexed ending line, inclusive (for read_file, optional).",
            },
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for (for search_files).",
            },
            "glob": {
                "type": "string",
                "description": "Glob filter on file paths, e.g. '**/*.py' (for search_files, optional).",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of match lines to return (for search_files, default 100).",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, workspace_dirs: Iterable[str] | None = None) -> None:
        dirs = list(workspace_dirs) if workspace_dirs else list(DEFAULT_WORKSPACE_DIRS)
        self.workspace_dirs: list[Path] = [Path(d).expanduser().resolve() for d in dirs]

    # ----------------------------------------------------------------- safety

    def _resolve_inside_workspace(self, raw_path: str) -> Path | None:
        """Resolve ``raw_path`` and return it only if it lies inside a workspace dir."""
        try:
            p = Path(raw_path).expanduser().resolve()
        except Exception:
            return None
        for ws in self.workspace_dirs:
            try:
                p.relative_to(ws)
                return p
            except ValueError:
                continue
        return None

    # -------------------------------------------------------------- operations

    async def execute(self, **params) -> ToolResult:
        operation = params.get("operation")
        if operation not in ("read_file", "write_file", "search_files"):
            return ToolResult(
                success=False,
                output="",
                error="Parameter 'operation' must be one of: read_file, write_file, search_files.",
            )

        try:
            if operation == "read_file":
                return await asyncio.to_thread(self._read_file, params)
            if operation == "write_file":
                return await asyncio.to_thread(self._write_file, params)
            return await asyncio.to_thread(self._search_files, params)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Unhandled error: {exc}")

    # ---- read ---------------------------------------------------------------

    def _read_file(self, params: dict) -> ToolResult:
        raw_path = params.get("path")
        if not raw_path:
            return ToolResult(success=False, output="", error="read_file requires 'path'.")
        path = self._resolve_inside_workspace(raw_path)
        if path is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Path is outside allowed workspaces: {raw_path}",
            )
        if not path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")
        if not path.is_file():
            return ToolResult(success=False, output="", error=f"Not a regular file: {path}")

        try:
            size = path.stat().st_size
            if size > MAX_READ_BYTES:
                # Still allow a slice via start/end lines, but warn if user wants the whole thing.
                if params.get("start_line") is None and params.get("end_line") is None:
                    return ToolResult(
                        success=False,
                        output="",
                        error=(
                            f"File is {size} bytes (> {MAX_READ_BYTES}). "
                            "Use start_line/end_line to read a slice."
                        ),
                    )
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Read failed: {exc}")

        start = params.get("start_line")
        end = params.get("end_line")
        if start is not None or end is not None:
            lines = raw.splitlines()
            s = max(1, int(start) if start is not None else 1)
            e = min(len(lines), int(end) if end is not None else len(lines))
            if s > e:
                return ToolResult(success=False, output="", error="start_line > end_line.")
            sliced = lines[s - 1:e]
            numbered = "\n".join(f"{i:>6}  {line}" for i, line in enumerate(sliced, start=s))
            return ToolResult(
                success=True,
                output=f"{path} (lines {s}-{e} of {len(lines)}):\n{numbered}",
            )

        return ToolResult(success=True, output=f"{path}:\n{raw}")

    # ---- write --------------------------------------------------------------

    def _write_file(self, params: dict) -> ToolResult:
        raw_path = params.get("path")
        content = params.get("content")
        if not raw_path:
            return ToolResult(success=False, output="", error="write_file requires 'path'.")
        if content is None:
            return ToolResult(success=False, output="", error="write_file requires 'content'.")
        if not isinstance(content, str):
            return ToolResult(success=False, output="", error="'content' must be a string.")

        # We resolve *parent* path to catch non-existent files too.
        p = Path(raw_path).expanduser()
        # If relative, make absolute against the first workspace dir.
        if not p.is_absolute():
            if not self.workspace_dirs:
                return ToolResult(success=False, output="", error="No workspace dirs configured.")
            p = self.workspace_dirs[0] / p
        try:
            p = p.resolve()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Cannot resolve path: {exc}")

        inside = False
        for ws in self.workspace_dirs:
            try:
                p.relative_to(ws)
                inside = True
                break
            except ValueError:
                continue
        if not inside:
            return ToolResult(
                success=False,
                output="",
                error=f"Write path is outside allowed workspaces: {p}",
            )

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Write failed: {exc}")

        return ToolResult(success=True, output=f"Wrote {len(content)} chars to {p}")

    # ---- search -------------------------------------------------------------

    def _search_files(self, params: dict) -> ToolResult:
        pattern = params.get("pattern")
        if not pattern:
            return ToolResult(success=False, output="", error="search_files requires 'pattern'.")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(success=False, output="", error=f"Invalid regex: {exc}")

        glob_pat = params.get("glob") or DEFAULT_GLOB
        max_results = int(params.get("max_results") or 100)

        raw_root = params.get("path")
        if raw_root:
            root = self._resolve_inside_workspace(raw_root)
            if root is None:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Search root outside allowed workspaces: {raw_root}",
                )
            roots: list[Path] = [root]
        else:
            roots = list(self.workspace_dirs)

        results: list[str] = []
        bytes_used = 0
        match_count = 0

        for root in roots:
            if not root.exists():
                continue
            if root.is_file():
                match_count, bytes_used = self._scan_file(
                    root, regex, glob_pat, results, match_count, bytes_used, max_results,
                )
                if match_count >= max_results or bytes_used >= MAX_SEARCH_BYTES:
                    break
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # Prune noisy dirs in place.
                dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
                for fn in filenames:
                    fp = Path(dirpath) / fn
                    # Glob filter against path relative to root.
                    try:
                        rel = fp.relative_to(root).as_posix()
                    except ValueError:
                        rel = fp.as_posix()
                    if not (fnmatch.fnmatch(rel, glob_pat) or fnmatch.fnmatch(fp.name, glob_pat)):
                        continue
                    match_count, bytes_used = self._scan_file(
                        fp, regex, glob_pat, results, match_count, bytes_used, max_results,
                    )
                    if match_count >= max_results or bytes_used >= MAX_SEARCH_BYTES:
                        break
                if match_count >= max_results or bytes_used >= MAX_SEARCH_BYTES:
                    break
            if match_count >= max_results or bytes_used >= MAX_SEARCH_BYTES:
                break

        if not results:
            return ToolResult(
                success=True,
                output=f"No matches for pattern {pattern!r} (glob={glob_pat}).",
            )
        header = f"Found {match_count} match(es) for {pattern!r}:"
        return ToolResult(success=True, output=header + "\n" + "\n".join(results))

    def _scan_file(
        self,
        fp: Path,
        regex: re.Pattern[str],
        glob_pat: str,
        results: list[str],
        match_count: int,
        bytes_used: int,
        max_results: int,
    ) -> tuple[int, int]:
        # Skip binary-ish / huge files quickly.
        try:
            if fp.stat().st_size > 2_000_000:
                return match_count, bytes_used
        except OSError:
            return match_count, bytes_used
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if regex.search(line):
                        entry = f"{fp}:{lineno}: {line.rstrip()}"
                        results.append(entry)
                        match_count += 1
                        bytes_used += len(entry)
                        if match_count >= max_results or bytes_used >= MAX_SEARCH_BYTES:
                            break
        except Exception:  # noqa: BLE001
            # Unreadable file — skip silently.
            return match_count, bytes_used
        return match_count, bytes_used


tools = [CodeTool()]
