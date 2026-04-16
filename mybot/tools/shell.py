"""Shell execution tool with whitelist + dangerous-pattern filtering.

Executes shell commands via asyncio.subprocess. Designed to be safe by default:
- Only commands whose first token is in ``allowed_commands`` may run.
- A small set of dangerous substring / regex patterns always blocks execution.
- Every invocation is bounded by a configurable timeout.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from typing import Iterable

from mybot.tools.base import BaseTool, ToolResult

# Default command whitelist — matches config.yaml defaults plus common read-only utils.
DEFAULT_ALLOWED_COMMANDS: tuple[str, ...] = (
    "ls", "cat", "grep", "find", "python", "python3",
    "git", "pip", "uv", "docker", "echo", "pwd", "which",
    "wc", "sort", "head", "tail", "date",
)

# Patterns that immediately veto a command — checked against the full command string.
# Mix of literal fragments and regex. Fork-bomb / recursive-delete / privilege-escalation style.
_DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f"),   # rm -rf, rm -fr, rm -Rf ...
    re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bsu\s+-"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchown\s+"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{"),                      # fork bomb :(){ :|:& };:
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r">\s*/dev/disk"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bhalt\b"),
    re.compile(r"\bkill\s+-9\s+1\b"),
    re.compile(r"\bmv\s+.*\s+/\s*$"),
    re.compile(r"\b/etc/passwd\b"),
    re.compile(r"\bcurl\s+.*\|\s*sh\b"),
    re.compile(r"\bwget\s+.*\|\s*sh\b"),
)


class ShellTool(BaseTool):
    """Execute a shell command with a whitelist and safety filters."""

    name = "shell"
    description = (
        "Execute a shell command on the local machine. Only commands in the "
        "configured whitelist are allowed; dangerous patterns (rm -rf, sudo, "
        "chmod 777, mkfs, dd, fork bombs, piped installers) are blocked. "
        "Returns combined stdout + stderr."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute, e.g. 'git status' or 'ls -la'.",
            }
        },
        "required": ["command"],
    }

    def __init__(
        self,
        allowed_commands: Iterable[str] | None = None,
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> None:
        self.allowed_commands = tuple(allowed_commands) if allowed_commands else DEFAULT_ALLOWED_COMMANDS
        self.timeout = float(timeout)
        self.cwd = cwd

    # ------------------------------------------------------------------ checks

    def _first_token(self, command: str) -> str | None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None
        return tokens[0] if tokens else None

    def _is_dangerous(self, command: str) -> str | None:
        """Return the offending pattern string if the command is unsafe, else None."""
        for pat in _DANGEROUS_PATTERNS:
            if pat.search(command):
                return pat.pattern
        return None

    # --------------------------------------------------------------- execution

    async def execute(self, **params) -> ToolResult:
        command = params.get("command")
        if not command or not isinstance(command, str):
            return ToolResult(success=False, output="", error="Missing required parameter 'command' (string).")

        command = command.strip()
        if not command:
            return ToolResult(success=False, output="", error="Command is empty.")

        # Safety: dangerous-pattern veto.
        dangerous = self._is_dangerous(command)
        if dangerous:
            return ToolResult(
                success=False,
                output="",
                error=f"Command blocked: matches dangerous pattern ({dangerous}).",
            )

        # Safety: whitelist.
        first = self._first_token(command)
        if first is None:
            return ToolResult(success=False, output="", error="Could not parse command (unbalanced quotes?).")

        # If the first token is a path like /usr/bin/git, compare basename.
        first_basename = first.rsplit("/", 1)[-1]
        if first_basename not in self.allowed_commands:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Command '{first_basename}' is not in the allowed list. "
                    f"Allowed: {', '.join(sorted(self.allowed_commands))}."
                ),
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Failed to spawn subprocess: {exc}")

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"Command timed out after {self.timeout:.0f}s.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Subprocess error: {exc}")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        combined_parts: list[str] = []
        if stdout:
            combined_parts.append(stdout.rstrip("\n"))
        if stderr:
            combined_parts.append(f"[stderr]\n{stderr.rstrip(chr(10))}")
        combined = "\n".join(combined_parts)

        rc = proc.returncode if proc.returncode is not None else -1
        if rc != 0:
            return ToolResult(
                success=False,
                output=combined,
                error=f"Command exited with code {rc}.",
            )
        return ToolResult(success=True, output=combined or "(no output)")


tools = [ShellTool()]
