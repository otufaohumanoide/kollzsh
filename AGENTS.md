# AGENTS.md — kollzsh

Oh-my-zsh plugin pairing a persistent Python daemon with an LLM (OpenAI-compatible API) to suggest shell commands. Two ZLE widgets — navigation (Ctrl+O) and librarian (Ctrl+F).

## Architecture

```
ZSH widget → Unix socket → kollzshd.py (Python daemon)
  navigation mode → LLM generates commands → bash subprocess executes → fzf
  deep mode → Pi "librarian" → searches content → streamed events → stderr
```

**ZSH layer:**
- `kollzsh.plugin.zsh` — oh-my-zsh entry point (sources `koll.zsh`)
- `koll.zsh` — widget definitions, hotkeys, daemon lifecycle, socket I/O
- `kollzsh-validate.zsh` — health checks (daemon, LLM running)
- `kollzsh-daemon.zsh` — daemon lifecycle (start, EXIT trap, code-change detection)
- `utils.zsh` — `check_command`, `check_llm_running`, `check_daemon_running`

**Daemon (Python 3.10+, stdlib-only — no pip, no venv):**
- `kollzshd.py` — entry point (PID file check, starts ``DaemonServer``)
- `server.py` — socket server, accept loop, signal handling, inactivity timeout
- `shell_manager.py` — persistent bash subprocess, CWD tracking, `execute_command` with UUID marker protocol
- `agent_router.py` — dispatches navigation vs deep queries, coordinates LLM + Pi
- `kollzshd_commands.py` — pure functions: command whitelist, safety validation, `truncate_output` sandwich, `parse_and_validate_commands`
- `kollzshd_llm.py` — `build_navigation_prompt`, `call_llm` via `urllib` (retry with backoff), `extract_commands` from tool_calls or content fallback
- `kollzshd_logging.py` — shared `setup_logging()` / `log_debug()` (guarded against double init)
- `kollzshd_client.py` — CLI socket client used by `koll.zsh` (subcommands: `send`, `stream`, `parse-lines`)
- `pi_setup.py` — Pi DCI-Agent auto-setup (nvm, git clone, npm install, build, models.json)
- `pi_client.py` — Pi RPC subprocess, streaming event loop, query timeout

## Widgets & Hotkeys

| Key | Widget | Mode | Output |
|---|---|---|---|
| `Ctrl+O` (`KOLLZSH_HOTKEY`) | `fzf_kollzsh` | Navigation | fzf selection → BUFFER |
| `Ctrl+F` | `fzf_kollzsh_deep` | Librarian search | stderr (no BUFFER) |

Navigation mode generates commands via LLM tool_calling, executes them, pipes output to fzf. Deep mode delegates to Pi DCI-Agent (Node.js subprocess) operating as a librarian — searches content, never answers questions.

## Daemon Lifecycle

- Starts on first widget invocation (`ensure_daemon_running` in `koll.zsh`)
- Shuts down on ZSH exit (trap EXIT sends SIGTERM to daemon)
- **Auto-restart on code change**: compares timestamp of ALL `.py` files against `/tmp/kollzshd.pid` — if any is newer, kills old daemon and starts fresh
- PID file: `/tmp/kollzshd.pid` — refuses to start if another instance is alive
- Inactivity timeout: 1800s with no requests → daemon shuts down
- Single daemon serves all ZSH sessions sharing the same socket

## Socket Protocol

All daemon I/O is JSON over Unix socket (`/tmp/kollzshd.sock`). Client has 300s socket read timeout (`kollzshd_client.py`); server-side Pi subprocess has independent 300s `PI_QUERY_TIMEOUT`.

**Request:** `{"query": "...", "mode": "navigation|deep"}`
**Navigation response:** `{"lines": ["..."], "cwd": "/path"}`
**Deep mode** streams events as JSON lines (one per line), ZSH reads via `makefile("r")`:

| Event | Fields | When |
|---|---|---|
| `think` | `status`, `msg`, `round` | Pi turn start/end |
| `cmd` | `cmd`, `round` | Command being executed |
| `out` | `lines`, `round` | Command output lines |
| `read` | `file`, `round` | Auto-reading a file |
| `result` | `lines` | Full Pi result |
| `error` | `msg` | LLM or Pi error |
| `done` | `lines`, `cwd` | Final — ZSH captures as stdout |

ZSH renders progress events to stderr; only `done` event goes to stdout (discarded by widget, `>/dev/null`).

## Deep Mode — Librarian (Ctrl+F)

Pi DCI-Agent operates as a librarian: searches the filesystem for semantically
relevant content using grep/rg/find, returns file paths + full content via stderr
(reading only, the command line stays clean). It NEVER answers questions directly.

Pi setup and config unchanged:
- Custom fork: `https://github.com/jdf-prog/pi-mono.git`, branch `codex/context-management-ablation`
- Auto-setup: finds Node.js >=20 (tries NVM, falls back to install), `npm install && npm run build` in `pi-mono/`
- Runs as Node.js subprocess (`--mode rpc --tools read,bash --no-session`), `start_new_session=True` to prevent tty leak
- 300s Pi query timeout, max turns configurable (`KOLLZSH_PI_MAX_TURNS`, default 20)
- Context levels: `level0`-`level5` (`KOLLZSH_PI_CONTEXT_LEVEL`, default `level3`)

## Config Variables (set in `~/.zshrc` before sourcing oh-my-zsh)

| Var | Default | Notes |
|---|---|---|
| `KOLLZSH_URL` | `http://localhost:8080` | LLM server, `/v1/chat/completions` API |
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL` | Must exist in `GET /v1/models` |
| `KOLLZSH_HOTKEY` | `^o` | ZLE widget binding for navigation |
| `KOLLZSH_DAEMON_SOCK` | `/tmp/kollzshd.sock` | Unix socket path |
| `KOLLZSH_PLUGIN_DIR` | auto-detected | Override plugin directory |
| `KOLLZSH_PI_MAX_TURNS` | `20` | Max Pi turns per deep search |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Pi context management level |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Pi agent config directory |
| `KOLLZSH_SYSTEM_CONTEXT` | (empty) | Extra text injected into LLM system prompt |
| `KOLLZSH_COMMAND_COUNT` | `5` | Defined but unused by current daemon |

## Command Safety (`kollzshd_commands.py`)

Read-only commands (auto-execute): `grep`, `rg`, `ag`, `find`, `ls`, `cat`, `head`, `tail`, `wc`, `stat`, `file`, `sort`, `uniq`, `diff`, `tree`, `pwd`, `echo`, `which`, `type`, `du`, `df`, `bat`, `less`, `strings`, `nl`, `od`, `xxd`, `column`, `cut`, `tr`, `fmt`, `fold`, `expand`, `pr`, `printf`, `env`, `dirname`, `basename`, `realpath`, `readlink`, `date`, `cal`, `bc`, `seq`, `shuf`, `tsort`, `comm`, `paste`, `join`, `look`, `split`, `cksum`, `md5sum`, `sha1sum`, `sha256sum`

Destructive commands are **blocked at the daemon level** (not sent to fzf for confirmation): `rm`, `mv`, `cp`, `chmod`, `chown`, `sudo`, `kill`, `apt`, `pacman`, `brew`, `dnf`, `yum`, `pip`, `npm`, `docker`, `systemctl`, `mkfs`, `dd`, `shutdown`, `reboot`, `halt`, `poweroff`, `init`

Pipelines with destructive commands, redirects to block devices, and dangerous patterns (`rm -rf /`) are regex-blocked.

## Development Workflow

- **56 pytest unit tests** in `tests/` — run with `python3 -m pytest tests/`
- Debug log: `tail -f /tmp/kollzsh_debug.log` (log file is `chmod 666` in `koll.zsh`)
- After changing any `.py` file, the daemon auto-restarts on next widget invocation (timestamp check)
- After changing `koll.zsh` or `utils.zsh`, re-source: `source ~/.zshrc` or open a new terminal
- Verify Python changes with: `python3 -m py_compile <file>.py && python3 -c "from <file> import ..."`

## Gotchas

- Daemon's bash subprocess uses `--norc --noprofile` — no user aliases or configs
- CWD is synced by appending `; echo '__KSEP__'; pwd; echo '__KEND__'` to every command — parse for these markers in stdout
- LLM communication uses `urllib.request` (stdlib), NOT `requests` — no venv/pip dependencies allowed
- `truncate_output` uses sandwich (top N + bottom N lines proportional to `max_lines`). Outputs exceeding threshold get truncated with an omitted-line marker
- `pi-mono/` and `DCI-Agent-Lite/` are gitignored (auto-managed by daemon)
- `chmod 666` on `/tmp/kollzsh_debug.log` — intentionally world-writable for multi-user debug access
