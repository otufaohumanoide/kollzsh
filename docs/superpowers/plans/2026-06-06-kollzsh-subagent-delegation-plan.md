# kollzsh Subagent Delegation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy 6 independent improvement missions across 2 waves using parallel subagents.

**Architecture:** Wave 1 runs Integration Tests, ZSH UX Polish, and README/docs in parallel (zero file overlap). Wave 2 runs Type Hints first (all .py files, additive only), then Pi Robustness and Error Handling in parallel on separate files.

**Tech Stack:** Python 3.10+ stdlib, pytest, ZSH, Markdown

---

## Wave 1 — Parallel (Task 1, 2, 3 run concurrently)

### Task 1: Integration Tests

**Files:**
- Create: `tests/test_integration.py`
- Modify: none

- [ ] **Step 1: Create `tests/test_integration.py` with socket-level tests**

```python
import json
import os
import socket
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

import pytest


class TestDaemonSocketProtocol:
    """Test the Unix socket protocol between client and server."""

    @pytest.fixture
    def sock_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield os.path.join(tmpdir, "test.sock")

    @pytest.fixture
    def mock_server(self, sock_path):
        """A minimal server that mimics DaemonServer's socket I/O."""
        from server import DaemonServer

        server = DaemonServer(
            socket_path=sock_path,
            pid_file="/tmp/_test_kollzshd.pid",
            inactivity_timeout=2,
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        time.sleep(0.2)
        yield server
        server.running = False
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        try:
            os.unlink("/tmp/_test_kollzshd.pid")
        except OSError:
            pass

    def test_connect_and_send_navigation(self, sock_path, mock_server):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        payload = json.dumps({"query": "list files", "mode": "navigation"})
        sock.sendall(payload.encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        response = json.loads(data.decode())
        assert "lines" in response
        assert isinstance(response["lines"], list)

    def test_connect_and_send_deep(self, sock_path, mock_server):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        payload = json.dumps({"query": "search code", "mode": "deep"})
        sock.sendall(payload.encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        response = json.loads(data.decode())
        assert "lines" in response

    def test_double_pid_guard(self, sock_path):
        from kollzshd import main as daemon_main
        pid_file = "/tmp/_test_kollzshd_pid2.pid"
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
        try:
            with patch.object(sys, "argv", ["kollzshd.py"]):
                with pytest.raises(SystemExit):
                    daemon_main()
        finally:
            os.unlink(pid_file)

    def test_send_query_client_connection_refused(self):
        from kollzshd_client import _send_query
        result = _send_query("/tmp/_nonexistent_test.sock", "test", "navigation")
        assert "Error:" in result or "daemon is not running" in result

    def test_render_event_think_start(self):
        from kollzshd_client import _render_event
        event = {"type": "think", "status": "start", "msg": "analyzing"}
        result = _render_event(event)
        assert "analyzing" in result
        assert "THINK" in result

    def test_render_event_error(self):
        from kollzshd_client import _render_event
        event = {"type": "error", "msg": "something failed"}
        result = _render_event(event)
        assert "something failed" in result

    def test_render_event_unknown_type(self):
        from kollzshd_client import _render_event
        event = {"type": "unknown_thing"}
        result = _render_event(event)
        assert result == ""

    def test_parse_lines_basic(self):
        from kollzshd_client import _parse_lines
        import io
        import sys
        sys.stdin = io.StringIO('{"lines": ["line1", "line2"]}')
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _parse_lines()
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "line1" in output
        assert "line2" in output

    def test_parse_lines_empty(self):
        from kollzshd_client import _parse_lines
        import io
        import sys
        sys.stdin = io.StringIO('{"lines": []}')
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _parse_lines()
        finally:
            sys.stdout = old_stdout
        assert captured.getvalue() == ""

    def test_truncate_output_basic(self):
        from kollzshd_commands import truncate_output
        lines = [f"line {i}" for i in range(10)]
        result = truncate_output(lines, max_lines=4)
        assert len(result) == 4
        assert "omitted" in result[2]

    def test_truncate_output_no_truncation_needed(self):
        from kollzshd_commands import truncate_output
        lines = [f"line {i}" for i in range(3)]
        result = truncate_output(lines, max_lines=10)
        assert result == lines

    def test_validate_command_safety_safe(self):
        from kollzshd_commands import validate_command_safety
        ok, reason = validate_command_safety("ls -la")
        assert ok is True

    def test_validate_command_safety_destructive(self):
        from kollzshd_commands import validate_command_safety
        ok, reason = validate_command_safety("rm -rf /tmp/foo")
        assert ok is False
        assert "Blocked" in reason
```

- [ ] **Step 2: Run integration tests**

Run: `python3 -m pytest tests/test_integration.py -v`
Expected: Tests pass (may skip some that rely on server import chain)

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for daemon socket protocol"
```

---

### Task 2: ZSH UX Polish

**Files:**
- Modify: `kollzsh-validate.zsh`
- Modify: `kollzsh-daemon.zsh`
- Modify: `koll.zsh`

- [ ] **Step 1: Add distinct error messages to `kollzsh-validate.zsh`**

Replace generic error with specific messages:

```zsh
check_llm_running() {
  local url="${KOLLZSH_URL:-http://localhost:8080}"
  local response
  response=$(command curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$url/v1/models" 2>/dev/null)
  if [[ "$response" != "200" ]]; then
    case "$response" in
      000)  print -P "  %%F{red}[kollzsh]%%f LLM server unreachable at $url" >&2 ;;
      401|403) print -P "  %%F{red}[kollzsh]%%f LLM server at $url returned HTTP $response" >&2 ;;
      *)    print -P "  %%F{red}[kollzsh]%%f LLM server at $url returned HTTP $response" >&2 ;;
    esac
    return 1
  fi
  return 0
}

check_daemon_running() {
  if [[ ! -S "${KOLLZSH_DAEMON_SOCK:-/tmp/kollzshd.sock}" ]]; then
    print -P "  %%F{red}[kollzsh]%%f Daemon socket not found. Start daemon with: kollzshd.py &" >&2
    return 1
  fi
  return 0
}

check_fzf_installed() {
  if ! command -v fzf &>/dev/null; then
    print -P "  %%F{red}[kollzsh]%%f fzf not found. Install with: sudo apt install fzf (brew install fzf)" >&2
    return 1
  fi
  return 0
}
```

- [ ] **Step 2: Add progress indicator to `kollzsh-daemon.zsh`**

```zsh
ensure_daemon_running() {
  local pid_file="/tmp/kollzshd.pid"
  local daemon_bin="${KOLLZSH_PLUGIN_DIR}/kollzshd.py"

  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    return 0
  fi

  print -Pn "  %%F{blue}[kollzsh]%%f Starting daemon... " >&2

  if [[ ! -x "$daemon_bin" ]]; then
    print -P "%%F{red}FAILED%%f (kollzshd.py not found)" >&2
    return 1
  fi

  python3 "$daemon_bin" &
  local pid=$!
  echo "$pid" > "$pid_file" 2>/dev/null

  # Wait for socket to appear (up to 3 seconds)
  local sock="${KOLLZSH_DAEMON_SOCK:-/tmp/kollzshd.sock}"
  local waited=0
  while [[ ! -S "$sock" && $waited -lt 30 ]]; do
    sleep 0.1
    waited=$((waited + 1))
  done

  if [[ -S "$sock" ]]; then
    print -P "%%F{green}OK%%f" >&2
  else
    print -P "%%F{red}TIMEOUT%%f (daemon did not create socket)" >&2
    return 1
  fi
}
```

- [ ] **Step 3: Add friendly timeout message to `koll.zsh`**

Add after the fzf invocation:

```zsh
# After fzf timeout or empty selection
if [[ $? -eq 130 ]]; then
  print -P "%%F{yellow}[kollzsh]%%f Search took too long — try a more specific query" >&2
fi
```

- [ ] **Step 4: Run zsh -n verification**

Run: `zsh -n kollzsh-validate.zsh && zsh -n kollzsh-daemon.zsh && zsh -n koll.zsh`
Expected: All parse cleanly

- [ ] **Step 5: Commit**

```bash
git add kollzsh-validate.zsh kollzsh-daemon.zsh koll.zsh
git commit -m "feat: improve ZSH UX with specific error messages and progress indicator"
```

---

### Task 3: README / Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write detailed `README.md`**

```markdown
# kollzsh

Oh-my-zsh plugin pairing a persistent Python daemon with an LLM (OpenAI-compatible API) to suggest shell commands.

## Prerequisites

- **Python 3.10+** (stdlib only — no pip, no venv)
- **fzf** — fuzzy finder (`sudo apt install fzf` / `brew install fzf`)
- **LLM server** — OpenAI-compatible API at `KOLLZSH_URL` (default: `http://localhost:8080`) with model `KOLLZSH_MODEL`
- **Node.js >=20** — required only for deep/librarian mode (Ctrl+F)

## Installation

```bash
# Clone into oh-my-zsh custom plugins
git clone https://github.com/your-org/kollzsh.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/kollzsh

# Add to .zshrc before sourcing oh-my-zsh
export KOLLZSH_URL="http://localhost:8080"
export KOLLZSH_MODEL="unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL"

# Then add kollzsh to plugins array
plugins=(... kollzsh)

# Reload
source ~/.zshrc
```

## Usage

| Key | Mode | Description |
|---|---|---|
| `Ctrl+O` | Navigation | LLM generates commands → executes → fzf selection |
| `Ctrl+F` | Deep librarian | Pi DCI-Agent searches content semantically |

**Navigation mode:** Type a partial command or describe what you want in the terminal, press Ctrl+O, and the LLM generates relevant commands. Results pipe to fzf for selection.

**Deep mode:** Press Ctrl+F to search project files semantically using Pi DCI-Agent. Results stream to terminal (command line stays clean).

## Configuration

| Variable | Default | Description |
|---|---|---|
| `KOLLZSH_URL` | `http://localhost:8080` | LLM server URL |
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL` | LLM model name |
| `KOLLZSH_HOTKEY` | `^o` | ZLE widget binding for navigation |
| `KOLLZSH_DAEMON_SOCK` | `/tmp/kollzshd.sock` | Unix socket path |
| `KOLLZSH_PLUGIN_DIR` | auto-detected | Override plugin directory |
| `KOLLZSH_SYSTEM_CONTEXT` | (empty) | Extra text injected into LLM system prompt |
| `KOLLZSH_PI_MAX_TURNS` | `20` | Max Pi turns per deep search |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Pi context management level |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Pi agent config directory |

## Troubleshooting

**Daemon won't start:**
- Check `/tmp/kollzsh_debug.log` for errors
- Ensure another daemon instance isn't running: `kill $(cat /tmp/kollzshd.pid)`
- Verify `python3 --version` is 3.10+

**LLM not responding (Ctrl+O fails):**
- Run: `curl -s http://localhost:8080/v1/models`
- Check KOLLZSH_URL is correct and LLM server is running
- Verify KOLLZSH_MODEL exists in the server's model list

**Pi/Librarian not working (Ctrl+F fails):**
- Check Node.js version: `node --version` (needs >=20)
- Run: `python3 pi_setup.py` for auto-setup
- Check `/tmp/kollzsh_debug.log` for Pi errors

**Connection refused:**
- Daemon is not running. Press Ctrl+O/Ctrl+F to auto-start, or run manually: `python3 kollzshd.py &`

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Verify Python syntax
python3 -m py_compile *.py

# Verify ZSH syntax
zsh -n koll.zsh utils.zsh kollzsh-validate.zsh kollzsh-daemon.zsh

# Debug log
tail -f /tmp/kollzsh_debug.log

# Changes to .py files auto-restart the daemon
# Changes to .zsh files need: source ~/.zshrc
```

## Architecture

```
ZSH widget -> Unix socket -> Python daemon
  navigation mode -> LLM generates commands -> bash executes -> fzf
  deep mode -> Pi DCI-Agent -> searches content -> streamed events
```

Split into 10 Python modules and 5 ZSH files — see `AGENTS.md` for details.
```

- [ ] **Step 2: Verify README renders correctly**

Run: No verification needed beyond visual review.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: comprehensive README with install, config, troubleshooting"
```

---

## Wave 2 — Staged (Task 4 completes before 5 and 6)

### Task 4: Type Hints

**Files:**
- Modify: `kollzshd_commands.py`
- Modify: `kollzshd_llm.py`
- Modify: `kollzshd_logging.py`
- Modify: `kollzshd_client.py`
- Modify: `kollzshd.py`
- Modify: `server.py`
- Modify: `shell_manager.py`
- Modify: `agent_router.py`
- Modify: `pi_setup.py`
- Modify: `pi_client.py`

- [ ] **Step 1: Add type hints to `kollzshd.py`**

```python
"""Entry point for the kollzsh daemon."""
import os
import sys
from typing import NoReturn

from kollzshd_logging import setup_logging, log_debug
from server import DaemonServer

PID_FILE: str = "/tmp/kollzshd.pid"


def main() -> None:
    # ... existing code unchanged ...
```

(For all files, the pattern is: add `-> ReturnType` to every function, add `: type` to parameters, add variable annotations for complex types. No logic changes.)

- [ ] **Step 2: Add type hints to `server.py`**

Key annotations to add:
```python
def __init__(self, socket_path: str = ..., pid_file: str = ..., inactivity_timeout: int = ...) -> None:
def run(self) -> None:
def _accept_loop(self) -> None:
def _handle_client(self, conn: socket.socket, addr: str) -> None:
def _send_event(self, conn: socket.socket | None, event: Dict[str, object]) -> None:
def _check_inactivity(self) -> None:
def _cleanup(self) -> None:
def _signal_handler(self, signum: int, frame: object | None) -> None:
```

- [ ] **Step 3: Add type hints to `shell_manager.py`**

```python
def __init__(self) -> None:
@property
def is_alive(self) -> bool:
def start_shell(self) -> None:
def execute_command(self, command: str) -> Tuple[bool, str, Optional[str]]:
def update_cwd(self, new_cwd: str) -> None:
def close(self) -> None:
```

- [ ] **Step 4: Add type hints to `agent_router.py`**

```python
def __init__(self, shell: ShellManager) -> None:
def run_navigation(self, query: str, event_sender: Optional[EventSender] = ...) -> List[str]:
def run_deep_pi(self, query: str, event_sender: Optional[EventSender] = ...) -> List[str]:
def run_agent_loop(self, query: str, mode: str = ..., event_sender: Optional[EventSender] = ...) -> List[str]:
```

- [ ] **Step 5: Add type hints to `kollzshd_commands.py`**

```python
def validate_command_safety(command: str) -> Tuple[bool, str]:
def truncate_output(lines: List[str], max_lines: int = ...) -> List[str]:
def parse_and_validate_commands(content: str) -> List[Tuple[str, bool, str]]:
```

- [ ] **Step 6: Add type hints to `kollzshd_llm.py`**

```python
def _get_system_context() -> str:
def build_navigation_prompt(cwd: str, query: str) -> Dict[str, Any]:
def extract_commands(response_data: Dict[str, Any]) -> List[str]:
def _parse_content_commands(content: str) -> List[str]:
def call_llm(payload: Dict[str, Any], timeout: int = ...) -> Optional[Dict[str, Any]]:
```

- [ ] **Step 7: Add type hints to `kollzshd_client.py`**

```python
def _send_query(sock_path: str, query: str, mode: str) -> str:
def _render_event(event: dict) -> str:
def _stream_query(sock_path: str, query: str) -> None:
def _parse_lines() -> None:
def main() -> None:
```

- [ ] **Step 8: Add type hints to `kollzshd_logging.py`**

```python
def setup_logging(log_file: str = ...) -> None:
def log_debug(message: str, data: str | None = ...) -> None:
```

- [ ] **Step 9: Add type hints to `pi_setup.py`**

```python
def _find_node() -> Optional[str]:
def ensure_pi_ready(agent_dir: str, plugin_dir: str, event_callback: Optional[EventCallback] = ...) -> bool:
def _ensure_node(agent_dir: str, event_callback: Optional[EventCallback] = ...) -> str:
def _ensure_nvm(agent_dir: str, event_callback: Optional[EventCallback] = ...) -> bool:
def _ensure_pi_repo(agent_dir: str, plugin_dir: str, event_callback: Optional[EventCallback] = ...) -> bool:
def _ensure_pi_build(agent_dir: str, event_callback: Optional[EventCallback] = ...) -> bool:
def _ensure_models_json(agent_dir: str, url: str, model: str, event_callback: Optional[EventCallback] = ...) -> bool:
```

- [ ] **Step 10: Add type hints to `pi_client.py`**

```python
def run_pi_query(cwd: str, query: str, plugin_dir: str, agent_dir: str, url: str, model: str, max_turns: int, context_level: str, event_callback: Optional[EventCallback] = ...) -> List[str]:
```

- [ ] **Step 11: Run all verifications**

Run: `python3 -m py_compile *.py && python3 -m pytest tests/ -q`
Expected: All 10 .py files compile, all tests pass

- [ ] **Step 12: Commit**

```bash
git add -p  # review each type annotation change
git commit -m "refactor: add complete type hints to all Python modules"
```

---

### Task 5: Pi Subprocess Robustness

**Files:**
- Modify: `pi_client.py`
- Modify: `pi_setup.py`

(Depends on Task 4 being committed first.)

- [ ] **Step 1: Add orphan cleanup to `pi_client.py`**

```python
import atexit
import signal
from typing import Callable, List, Optional

_pi_proc: "subprocess.Popen[str] | None" = None


def _cleanup_pi() -> None:
    global _pi_proc
    if _pi_proc is not None and _pi_proc.poll() is None:
        _pi_proc.terminate()
        try:
            _pi_proc.wait(timeout=2)
        except Exception:
            _pi_proc.kill()
        _pi_proc = None


atexit.register(_cleanup_pi)
```

- [ ] **Step 2: Add health check and restart in `run_pi_query`**

Replace the current subprocess launch with:

```python
global _pi_proc

def _ensure_pi_running(
    agent_dir: str, plugin_dir: str,
    url: str, model: str,
    context_level: str,
    event_callback: Optional[EventCallback] = None,
) -> "subprocess.Popen[str]":
    global _pi_proc
    if _pi_proc is not None and _pi_proc.poll() is None:
        return _pi_proc

    agent_config = os.path.join(agent_dir, "agent.json")
    if not os.path.exists(agent_config):
        if event_callback:
            event_callback("think", status="start", msg="Setting up Pi agent...")
        ok = ensure_pi_ready(agent_dir, plugin_dir, event_callback)
        if not ok:
            raise RuntimeError("Pi agent setup failed")

    models_path = os.path.join(agent_dir, "models.json")
    _pi_proc = subprocess.Popen(
        [
            "node", "dist/index.js",
            "--mode", "rpc",
            "--tools", "read,bash",
            "--no-session",
            "--config", agent_dir,
            "--models", models_path,
        ],
        cwd=os.path.join(plugin_dir, "pi-mono"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    return _pi_proc
```

- [ ] **Step 3: Add timeout recovery in the streaming loop**

```python
def run_pi_query(
    cwd: str, query: str,
    plugin_dir: str, agent_dir: str,
    url: str, model: str,
    max_turns: int, context_level: str,
    event_callback: Optional[EventCallback] = None,
) -> List[str]:
    global _pi_proc
    lines: List[str] = []

    try:
        proc = _ensure_pi_running(agent_dir, plugin_dir, url, model, context_level, event_callback)
    except RuntimeError as exc:
        if event_callback:
            event_callback("error", msg=str(exc))
        return [f"Pi setup error: {exc}"]

    # ... existing query code ...

    while True:
        try:
            ready, _, _ = select.select([proc.stdout], [], [], PI_QUERY_TIMEOUT)
            if not ready:
                # Timeout: discard partial output, return error
                proc.kill()
                _pi_proc = None
                if event_callback:
                    event_callback("error", msg=f"Pi query timed out after {PI_QUERY_TIMEOUT}s")
                return [f"Pi query timed out after {PI_QUERY_TIMEOUT}s"]
            line = proc.stdout.readline()
            if not line:
                break
            # ... existing event handling ...
        except (BrokenPipeError, OSError) as exc:
            if event_callback:
                event_callback("error", msg=f"Pi connection lost: {exc}")
            _pi_proc = None
            return [f"Pi connection lost: {exc}"]

    return lines
```

- [ ] **Step 4: Add Node.js fallback detection to `pi_setup.py`**

```python
def _find_node() -> Optional[str]:
    """Try to find a Node.js executable >=20."""
    candidates = []
    nvm_node = os.path.expanduser("~/.nvm/versions/node/*/bin/node")
    if os.path.exists(os.path.expanduser("~/.nvm")):
        import glob
        candidates.extend(sorted(glob.glob(nvm_node), reverse=True))
    which_node = shutil.which("node")
    if which_node:
        candidates.append(which_node)
    for candidate in candidates:
        try:
            version = subprocess.check_output(
                [candidate, "--version"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            major = int(version.lstrip("v").split(".")[0])
            if major >= 20:
                return candidate
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue
    return None
```

- [ ] **Step 5: Run verification**

Run: `python3 -m py_compile pi_client.py pi_setup.py && python3 -m pytest tests/ -q`
Expected: Both compile, all 56 tests pass

- [ ] **Step 6: Commit**

```bash
git add pi_client.py pi_setup.py
git commit -m "fix: Pi subprocess robustness (orphan cleanup, health check, timeout, Node fallback)"
```

---

### Task 6: Error Handling Hardening

**Files:**
- Modify: `server.py`
- Modify: `shell_manager.py`
- Modify: `agent_router.py`

(Depends on Task 4 being committed first. Runs in parallel with Task 5.)

- [ ] **Step 1: Harden `server.py` accept loop**

```python
def _accept_loop(self) -> None:
    while self.running:
        try:
            conn, addr = self.server.accept()
        except socket.timeout:
            self._check_inactivity()
            continue
        except OSError as exc:
            log_debug(f"Accept error: {exc}")
            continue
        self._handle_client(conn, str(addr))

def _handle_client(self, conn: socket.socket, addr: str) -> None:
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        raw = data.decode().strip()
        if not raw:
            return
        request = json.loads(raw)
        query = request.get("query", "")
        mode = request.get("mode", "navigation")
        log_debug(f"Request from {addr}: mode={mode}, query={query}")
        result = self.router.run_agent_loop(query, mode)
        response = json.dumps({"lines": result, "cwd": self.shell.cwd})
        try:
            conn.sendall(response.encode())
        except (BrokenPipeError, OSError) as exc:
            log_debug(f"Send error to {addr}: {exc}")
    except (json.JSONDecodeError, Exception) as exc:
        log_debug(f"Error handling client {addr}: {exc}")
        try:
            conn.sendall(json.dumps({"error": str(exc)}).encode())
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
```

- [ ] **Step 2: Harden `shell_manager.py` with dead process detection and command timeout**

Add `import time` to the imports at the top of the file. Then replace the class with:

```python
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
```

- [ ] **Step 3: Harden `agent_router.py` with graceful LLM error handling**

```python
class AgentRouter:
    def run_navigation(
        self, query: str, event_sender: Optional[EventSender] = None
    ) -> List[str]:
        try:
            payload = build_navigation_prompt(self.shell.cwd, query)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Prompt building failed: {exc}")
            return [f"Error building prompt: {exc}"]

        try:
            response_data = call_llm(payload)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"LLM call failed: {exc}")
            return [f"LLM call error: {exc}"]

        if not response_data:
            if event_sender:
                event_sender("error", msg="LLM returned empty response")
            return ["Error: LLM returned no response"]

        try:
            commands = extract_commands(response_data)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Failed to parse LLM response: {exc}")
            return [f"Parse error: {exc}"]

        if not commands:
            return ["No relevant commands found"]

        output: list[str] = []
        for cmd in commands:
            if event_sender:
                event_sender("cmd", cmd=cmd)
            try:
                success, cmd_output, new_cwd = self.shell.execute_command(cmd)
            except Exception as exc:
                if event_sender:
                    event_sender("error", msg=f"Command failed: {cmd} - {exc}")
                continue
            if not success and not self.shell.is_alive:
                self.shell.start_shell()
            if new_cwd:
                self.shell.update_cwd(new_cwd)
            if cmd_output:
                output.extend(cmd_output.strip().split("\n"))

        return truncate_output(output)

    def run_deep_pi(
        self, query: str, event_sender: Optional[EventSender] = None
    ) -> List[str]:
        try:
            lines = run_pi_query(
                self.shell.cwd, query, plugin_dir, agent_dir,
                url, model, max_turns, context_level,
                event_callback=event_sender,
            )
            return truncate_output(lines)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Pi query failed: {exc}")
            return [f"Deep search error: {exc}"]
```

- [ ] **Step 4: Run verification**

Run: `python3 -m py_compile server.py shell_manager.py agent_router.py && python3 -m pytest tests/ -q`
Expected: All compile, all 56 tests pass

- [ ] **Step 5: Commit**

```bash
git add server.py shell_manager.py agent_router.py
git commit -m "fix: error handling hardening (accept loop, shell dead detection, command timeout, graceful LLM errors)"
```

---

## Verification Suite

After all 6 tasks complete:

```bash
python3 -m py_compile *.py                    # all 10 compile
python3 -m pytest tests/ -v                   # all tests pass
zsh -n koll.zsh utils.zsh kollzsh-validate.zsh kollzsh-daemon.zsh  # all parse
```
