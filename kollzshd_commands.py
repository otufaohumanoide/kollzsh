#!/usr/bin/env python3
"""kollzsh daemon command validation and execution."""

import ast
import json
import logging
import os
import re
import shlex
import subprocess
import sys

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

READONLY_COMMANDS = {
    "grep", "rg", "ag", "find", "ls", "cat", "head", "tail", "wc", "stat",
    "file", "sort", "uniq", "diff", "tree", "pwd", "echo", "which", "type",
    "du", "df", "bat", "less", "strings", "nl", "od", "xxd", "column", "cut",
    "tr", "fmt", "fold", "expand", "pr", "printf", "env", "dirname", "basename",
    "realpath", "readlink", "date", "cal", "bc", "seq", "shuf", "tsort",
    "comm", "paste", "join", "look", "split", "cksum", "md5sum", "sha1sum",
    "sha256sum",
}

DESTRUCTIVE_COMMANDS = {
    "rm", "mv", "cp", "chmod", "chown", "sudo", "kill", "apt", "pacman",
    "brew", "dnf", "yum", "pip", "npm", "docker", "systemctl", "mkfs", "dd",
    "shutdown", "reboot", "halt", "poweroff", "init",
}


def log_debug(message, data=None):
    if data:
        logging.debug(f"{message}\nData: {data}\n----------------------------------------")
    else:
        logging.debug(message)


def is_readonly(command):
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    has_readonly = False
    for t in tokens:
        if t in DESTRUCTIVE_COMMANDS or t in (">", ">>"):
            return False
        if t in READONLY_COMMANDS:
            has_readonly = True
    return has_readonly


def validate_command_safety(command):
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

    first_token = tokens[0].lower()
    if '/' in first_token:
        first_token = first_token.split('/')[-1]

    if first_token in DESTRUCTIVE_COMMANDS:
        return False, f"Blocked command: {first_token}"

    if '|' in command:
        segments = command.split('|')
        for segment in segments:
            segment = segment.strip()
            if segment:
                segment_first = segment.split()[0].lower() if segment.split() else ''
                if segment_first in DESTRUCTIVE_COMMANDS:
                    return False, f"Blocked command in pipeline: {segment_first}"

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

    redirect_patterns = [
        r'>\s*/dev/sd',
        r'>>\s*/dev/sd',
        r'2>&1\s*\|\s*rm',
    ]
    for pattern in redirect_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Matches dangerous redirect: {pattern}"

    dangerous_flags = {
        'rm': ['-rf', '-r', '-f', '-fr'],
        'dd': ['of=', 'if='],
        'mkfs': [],
        'chmod': ['-R', '777', '755'],
        'chown': ['-R'],
    }
    if first_token in dangerous_flags:
        for flag in dangerous_flags[first_token]:
            if flag in command:
                return False, f"Dangerous flag: {flag}"

    return True, "Command is safe"


def truncate_output(lines, max_lines=40):
    if len(lines) <= max_lines:
        return lines
    top = 20
    bottom = 20
    omitted = len(lines) - top - bottom
    return (lines[:top]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-bottom:])


def execute_command(command, shell_proc, timeout=30):
    is_safe, reason = validate_command_safety(command)
    if not is_safe:
        log_debug(f"Command rejected: {reason}", command)
        return False, f"Command rejected: {reason}", None

    log_debug(f"Executing command: {command}")

    sentinel = f"{command}; echo '__KSEP__'; pwd; echo '__KEND__'"

    try:
        shell_proc.stdin.write(sentinel + '\n')
        shell_proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        log_debug(f"Error writing to shell stdin: {e}")
        return False, f"Shell pipe error: {e}", None

    output_lines = []
    new_cwd = None
    found_sep = False

    try:
        while True:
            line = shell_proc.stdout.readline()
            if not line:
                log_debug("Shell stdout closed unexpectedly")
                return False, "Shell process exited", None

            line_stripped = line.rstrip('\n')

            if line_stripped == '__KEND__':
                break

            if line_stripped == '__KSEP__':
                found_sep = True
                continue

            if not found_sep:
                output_lines.append(line_stripped)
            else:
                new_cwd = line_stripped
    except Exception as e:
        log_debug(f"Error reading shell output: {e}")
        return False, f"Shell read error: {e}", None

    if new_cwd:
        log_debug(f"CWD updated to: {new_cwd}")

    truncated = truncate_output(output_lines)
    return True, '\n'.join(truncated), new_cwd


def parse_and_validate_commands(content):
    results = []

    try:
        try:
            commands = json.loads(content)
        except json.JSONDecodeError:
            commands = ast.literal_eval(content)

        if not isinstance(commands, list):
            commands = [str(commands)]

        for cmd in commands:
            if isinstance(cmd, str):
                is_safe, reason = validate_command_safety(cmd)
                results.append((cmd, is_safe, reason))

    except Exception as e:
        log_debug(f"Error parsing commands: {str(e)}", content)
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
    print(f"is_readonly test: {is_readonly(user_query)}")
