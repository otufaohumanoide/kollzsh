import os
import subprocess
import uuid
from typing import Optional, Tuple

from kollzshd_commands import validate_command_safety, truncate_output
from kollzshd_logging import log_debug


class ShellManager:
    def __init__(self) -> None:
        self.cwd: str = os.getcwd()
        self._shell_proc: subprocess.Popen | None = None

    @property
    def is_alive(self) -> bool:
        return self._shell_proc is not None and self._shell_proc.poll() is None

    def start_shell(self) -> None:
        log_debug("Starting shell subprocess")
        self._shell_proc = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        log_debug(f"Shell started, PID={self._shell_proc.pid}")

    def stop_shell(self) -> None:
        if self._shell_proc and self._shell_proc.poll() is None:
            log_debug("Stopping shell subprocess")
            try:
                self._shell_proc.stdin.close()
                self._shell_proc.wait(timeout=5)
            except Exception as e:
                log_debug(f"Error stopping shell: {e}")
                try:
                    self._shell_proc.kill()
                except Exception:
                    pass
        self._shell_proc = None

    def update_cwd(self, new_cwd: str) -> None:
        if new_cwd and new_cwd != self.cwd:
            self.cwd = new_cwd
            log_debug(f"CWD changed: {self.cwd}")

    def execute_command(self, command: str) -> Tuple[bool, Optional[str], Optional[str]]:
        is_safe, reason = validate_command_safety(command)
        if not is_safe:
            log_debug(f"Command rejected: {reason}", command)
            return False, f"Command rejected: {reason}", None

        log_debug(f"Executing command: {command}")

        uid = uuid.uuid4().hex[:8]
        ksep_marker = f'__KSEP_{uid}__'
        kend_marker = f'__KEND_{uid}__'
        sentinel = f"{command}; echo '{ksep_marker}'; pwd; echo '{kend_marker}'"

        try:
            self._shell_proc.stdin.write(sentinel + '\n')
            self._shell_proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            log_debug(f"Error writing to shell stdin: {e}")
            return False, f"[LLM GEROU] {command}\n[ERRO] Shell pipe error: {e}", None

        output_lines: list[str] = []
        new_cwd: Optional[str] = None
        found_sep = False

        try:
            while True:
                line = self._shell_proc.stdout.readline()
                if not line:
                    log_debug("Shell stdout closed unexpectedly")
                    return False, f"[LLM GEROU] {command}\n[ERRO] Shell process exited", None

                line_stripped = line.rstrip('\n')

                if line_stripped == kend_marker:
                    break

                if line_stripped == ksep_marker:
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
