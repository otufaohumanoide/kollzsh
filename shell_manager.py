import os
import subprocess
import time
import uuid
from typing import Optional, Tuple

from kollzshd_commands import validate_command_safety
from kollzshd_logging import log_debug


class ShellManager:
    def __init__(self) -> None:
        self.cwd: str = os.getcwd()
        self._shell_proc: subprocess.Popen | None = None

    @property
    def is_alive(self) -> bool:
        if self._shell_proc is None:
            return False
        return self._shell_proc.poll() is None

    def start_shell(self) -> None:
        try:
            self._shell_proc = subprocess.Popen(
                ["bash", "--norc", "--noprofile"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.cwd,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            log_debug(f"Failed to start shell: {exc}")
            raise

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

    def execute_command(
        self, command: str, timeout: float = 60.0
    ) -> Tuple[bool, str, Optional[str]]:
        marker = uuid.uuid4().hex[:8]
        safe, reason = validate_command_safety(command)
        if not safe:
            return True, f"[Blocked: {reason}]", None

        if self._shell_proc is None or self._shell_proc.poll() is not None:
            log_debug("Shell process dead, restarting")
            self.start_shell()
            time.sleep(0.1)

        wrapped = f"{command} 2>&1; echo '__KSEP_{marker}__'; pwd; echo '__KEND_{marker}__'"
        try:
            self._shell_proc.stdin.write(wrapped + "\n")
            self._shell_proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            log_debug(f"Write to shell failed: {exc}, restarting shell")
            self.start_shell()
            self._shell_proc.stdin.write(wrapped + "\n")
            self._shell_proc.stdin.flush()

        output: list[str] = []
        start = time.time()
        while True:
            if time.time() - start > timeout:
                self._shell_proc.kill()
                self._shell_proc = None
                return False, f"Command timed out after {timeout}s", None
            line = self._shell_proc.stdout.readline()
            if not line:
                self._shell_proc = None
                return False, "Shell process died during command", None
            output.append(line.rstrip("\n"))
            if line.strip() == f"__KEND_{marker}__":
                break

        if len(output) < 3:
            return False, "Incomplete command output", None

        cmd_output = "\n".join(output[:-3])
        cwd_line = output[-2].strip()
        new_cwd = cwd_line if cwd_line and cwd_line != self.cwd else None
        return True, cmd_output, new_cwd
