# Design: kollzsh Subagent Delegation — Wave 1 & 2

## Goal

Deploy 6 independent improvement missions across 2 waves, using subagents that never edit the same file concurrently.

## Wave 1 — Zero Conflict (3 subagents, parallel)

### A: Integration Tests

**Scope:** `tests/test_integration.py` (new file only)

**What:**
- Test DaemonServer socket accept with Unix socket
- Test navigation query → response roundtrip (mock LLM)
- Test deep/streaming query → event stream (mock Pi)
- Test daemon lifecycle: start, inactivity timeout, PID file double-start guard
- Test ShellManager bash subprocess: execute_command, CWD tracking, UUID marker protocol
- Test AgentRouter dispatch: navigation vs deep mode routing

**Not:** Does not test actual LLM or Pi — mocks both. Does not touch production code.

**Verification:** `python3 -m pytest tests/test_integration.py -v` passes.

---

### B: ZSH UX Polish

**Scope:** `koll.zsh`, `kollzsh-validate.zsh`, `kollzsh-daemon.zsh` (`.zsh` only)

**What:**
- `kollzsh-validate.zsh`: distinct error messages for "LLM not running" vs "daemon not running" vs "fzf not found"
- `kollzsh-daemon.zsh`: spinner/progress indicator during `ensure_daemon_running` (daemon startup can take seconds)
- `koll.zsh`: friendly timeout message ("Search took too long — try a more specific query")

**Not:** Does not change widget logic or key bindings.

**Verification:** `zsh -n` passes on all `.zsh` files.

---

### C: README / Docs

**Scope:** `README.md`

**What:**
- Installation (clone to oh-my-zsh custom plugins, source .zshrc)
- Configuration table (all `KOLLZSH_*` env vars with defaults and notes)
- Usage: Ctrl+O for navigation, Ctrl+F for deep librarian search
- Prerequisites: Python 3.10+, Node.js >=20 (for Pi/libarian), fzf, LLM server at KOLLZSH_URL
- Troubleshooting: daemon won't start (check `/tmp/kollzsh_debug.log`), Pi not working (check Node.js version), connection refused
- Development: pytest, py_compile, zsh -n, debug log

**Verification:** Markdown renders correctly (review by human).

---

## Wave 2 — Type Hints Gate (3 subagents, staged)

### D: Type Hints (must complete before E and F)

**Scope:** ALL `.py` files (additive only, zero logic changes)

**What:**
- All functions without return type → add `-> ReturnType`
- All parameters without type → add `: type`
- Complex local variables → add inline type annotation
- Use `typing` module types: `Optional[X]`, `Union[X, Y]`, `Callable[[A], B]`, `Dict[str, Any]`, `List[str]`

**Not:** Does not change any logic. Does not add new imports beyond `typing`. Does not change docstrings.

**Verification:** `python3 -m py_compile *.py` passes. `python3 -m pytest tests/` (56/56) passes. `git diff` shows only type annotation additions.

---

### E: Pi Subprocess Robustness (after D committed, parallel with F)

**Scope:** `pi_client.py`, `pi_setup.py` only

**What:**
- `pi_client.py`: register Pi subprocess PID in daemon atexit handler (kill orphans on shutdown). Health check: if subprocess.poll() is not None, restart transparently. Timeout recovery: if select() times out, discard partial output buffer, return error event.
- `pi_setup.py`: fallback Node.js detection to `which node` (currently only tries NVM). Clear error message with install link when Node.js < 20.

**Not:** Does not touch server.py, shell_manager.py, agent_router.py, or any other module.

**Verification:** `python3 -m py_compile pi_client.py pi_setup.py` passes. `python3 -m pytest tests/` still passes.

---

### F: Error Handling Hardening (after D committed, parallel with E)

**Scope:** `server.py`, `shell_manager.py`, `agent_router.py` only

**What:**
- `server.py`: wrap accept() in try/except (don't let bad client kill server). Handle BrokenPipeError on socket send. Log connection errors before closing.
- `shell_manager.py`: detect dead bash subprocess (`.poll() is not None`) → auto-restart. Add command timeout (configurable, default 60s) — if execute_command hangs, kill and return error. Catch OSError on Popen.
- `agent_router.py`: try/except around `extract_commands` and `call_llm` — if LLM returns garbage, return error event instead of crashing. Structured error events with `event_sender("error", msg=...)`.

**Not:** Does not touch pi_client.py, pi_setup.py, or any .zsh file.

**Verification:** `python3 -m py_compile server.py shell_manager.py agent_router.py` passes. `python3 -m pytest tests/` still passes.

---

## Verification Suite (after all agents complete)

```bash
python3 -m py_compile *.py
python3 -m pytest tests/ -v
zsh -n koll.zsh utils.zsh kollzsh-validate.zsh kollzsh-daemon.zsh
```

## Conflict Matrix

```
        A   B   C   D   E   F
tests   -   -   -   -   -   -
zsh     -   -   -   -   -   -
readme  -   -   -   -   -   -
types   -   -   -   -   SEQ SEQ
pi      -   -   -   SEQ -   -
errors  -   -   -   SEQ -   -
```
