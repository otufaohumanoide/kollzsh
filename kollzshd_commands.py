#!/usr/bin/env python3
"""Command validation and parsing for the kollzsh daemon.

Provides functions for:
- Shell command safety validation (read-only whitelist vs destructive)
- Long output truncation (sandwich: top N + bottom N lines)
- Parsing and validating command lists from LLM responses

Command execution is handled by ``shell_manager.ShellManager``.
"""

import ast
import json
import re
import shlex
import sys
from typing import List, Tuple

from kollzshd_logging import log_debug


# Read-only commands — execute automatically without user confirmation
READONLY_COMMANDS: set[str] = {
    "grep", "rg", "ag", "find", "ls", "cat", "head", "tail", "wc", "stat",
    "file", "sort", "uniq", "diff", "tree", "pwd", "echo", "which", "type",
    "du", "df", "bat", "less", "strings", "nl", "od", "xxd", "column", "cut",
    "tr", "fmt", "fold", "expand", "pr", "printf", "env", "dirname", "basename",
    "realpath", "readlink", "date", "cal", "bc", "seq", "shuf", "tsort",
    "comm", "paste", "join", "look", "split", "cksum", "md5sum", "sha1sum",
    "sha256sum",
}

# Destructive commands — blocked at daemon level (not sent to fzf)
DESTRUCTIVE_COMMANDS: set[str] = {
    "rm", "mv", "cp", "chmod", "chown", "sudo", "kill", "apt", "pacman",
    "brew", "dnf", "yum", "pip", "npm", "docker", "systemctl", "mkfs", "dd",
    "shutdown", "reboot", "halt", "poweroff", "init",
}





def validate_command_safety(command: str) -> Tuple[bool, str]:
    """Deep safety validation of a shell command.

    Verification layers:
    1. Empty command or heredoc
    2. First token checked against DESTRUCTIVE_COMMANDS
    3. Pipeline segments (``ls | rm -rf /`` is blocked)
    4. Dangerous patterns via regex (``rm -rf /``, redirects to ``/dev/sd``)
    5. Command-specific dangerous flags

    Args:
        command: Shell command to validate.

    Returns:
        Tuple ``(is_safe, reason)`` — True if safe, False with rejection reason.
    """
    if not command or not command.strip():
        return False, "Empty command"

    command = command.strip()

    if '<<' in command:
        return False, "Contains heredoc syntax"

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if not tokens:
        return False, "No command tokens found"

    # Extract command name, stripping absolute path if present
    first_token = tokens[0].lower()
    if '/' in first_token:
        first_token = first_token.split('/')[-1]

    if first_token in DESTRUCTIVE_COMMANDS:
        return False, f"Blocked command: {first_token}"

    # Check each pipeline segment individually
    if '|' in command:
        segments = command.split('|')
        for segment in segments:
            segment = segment.strip()
            if segment:
                segment_first = segment.split()[0].lower() if segment.split() else ''
                if segment_first in DESTRUCTIVE_COMMANDS:
                    return False, f"Blocked command in pipeline: {segment_first}"

    # Regex patterns for dangerous operations (rm -rf /, device redirects)
    dangerous_patterns = [
        r'rm\s+.*-rf\s+/',
        r'rm\s+.*-r\s+/',
        r'>\s*/dev/sd',
        r'\|\s*rm',
        r'\|\s*sudo',
        r'&&\s*rm',
        r';\s*rm',
        r'`rm',
        r'\$\{.*rm',
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Matches dangerous pattern: {pattern}"

    # Block device redirect patterns
    redirect_patterns = [
        r'>\s*/dev/sd',
        r'>>\s*/dev/sd',
        r'2>&1\s*\|\s*rm',
    ]
    for pattern in redirect_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Matches dangerous redirect: {pattern}"

    # Command-specific dangerous flags
    dangerous_flags = {
        'rm': ['-rf', '-r', '-f', '-fr'],
        'dd': ['of=', 'if='],
        'mkfs': [],
    }
    if first_token in dangerous_flags:
        for flag in dangerous_flags[first_token]:
            if flag in command:
                return False, f"Dangerous flag: {flag}"

    return True, "Command is safe"


def truncate_output(lines: list[str], max_lines: int = 120) -> list[str]:
    """Truncate output using sandwich method: top N + bottom N.

    When output exceeds ``max_lines``, keeps the first and last
    ``max_lines // 2`` lines, inserting a marker showing how many
    were omitted. Prevents giant outputs from blowing the LLM context.

    Args:
        lines: List of output lines.
        max_lines: Maximum lines in the final output.
                   Half goes to the top, half to the bottom.
                   Minimum 2 (to show at least 1 top + 1 bottom).

    Returns:
        Truncated list with omission marker in the middle.
    """
    if max_lines < 2:
        max_lines = 2
    if len(lines) <= max_lines:
        return lines
    half = max_lines // 2
    omitted = len(lines) - 2 * half
    return (lines[:half]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-half:])


def parse_and_validate_commands(content: str) -> List[Tuple[str, bool, str]]:
    """Parse commands from a string and validate each one.

    Tries JSON first, then Python literal (ast.literal_eval),
    and falls back to line-by-line parsing. Each found command
    is validated with ``validate_command_safety``.

    Args:
        content: String containing commands (JSON, Python literal, or plain text).

    Returns:
        List of ``(command, is_safe, reason)`` tuples.
    """
    results: List[Tuple[str, bool, str]] = []

    try:
        # Try JSON first (LLM's preferred format)
        try:
            commands = json.loads(content)
        except json.JSONDecodeError:
            # Fallback to Python literal (lists formatted without valid JSON)
            commands = ast.literal_eval(content)

        if not isinstance(commands, list):
            commands = [str(commands)]

        for cmd in commands:
            if isinstance(cmd, str):
                is_safe, reason = validate_command_safety(cmd)
                results.append((cmd, is_safe, reason))

    except Exception as e:
        log_debug(f"Error parsing commands: {str(e)}", content)
        # Last resort: line-by-line parse
        for line in content.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                is_safe, reason = validate_command_safety(line)
                results.append((line, is_safe, reason))

    return results


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: kollzshd_commands.py <user_query>", file=sys.stderr)
        sys.exit(1)

    user_query = sys.argv[1]
    valid, reason = validate_command_safety(user_query)
    print(f"Safety check: {'SAFE' if valid else 'BLOCKED'} — {reason}")
