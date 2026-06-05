# AGENTS.md — kollzsh

Oh-my-zsh plugin pairing a persistent Python daemon with an LLM (OpenAI-compatible API) to suggest shell commands. Two ZLE widgets — navigation (Ctrl+O) and deep search (Ctrl+F).

## Architecture

```
ZSH widget → Unix socket → kollzshd.py (Python daemon)
  navigation mode → LLM generates commands → bash subprocess executes → fzf
  deep mode → Pi RPC (Node.js DCI-Agent) → streamed events → buffer
```

**ZSH layer:**
- `kollzsh.plugin.zsh` — oh-my-zsh entry point (sources `koll.zsh`)
- `koll.zsh` — widget definitions, hotkeys, daemon lifecycle, socket I/O
- `utils.zsh` — `check_command`, `check_llm_running`, `check_daemon_running`

**Daemon (Python, stdlib-only — no pip, no venv):**
- `kollzshd.py` — socket server (`/tmp/kollzshd.sock`), persistent bash subprocess, CWD tracking, agent loop, streaming event protocol
- `kollzshd_commands.py` — command whitelist, safety validation, `execute_command` with `__KSEP__`/`__KEND__` marker protocol, `truncate_output` (sandwich: top 60 + bottom 60 lines)
- `kollzshd_llm.py` — prompt construction (navigation vs deep), HTTP calls to LLM (`urllib`, no `requests`), response parsing
- `kollzshd_pi.py` — Pi RPC client for deep search (Node.js DCI-Agent), auto-setup (nvm, git clone, npm install, build)

**Legacy (unused, candidate for removal):**
- `llm_util.py` — old stateless LLM bridge
- `ollama_util.py` — old Ollama client

## Widgets and hotkeys

| Key | Widget | Mode | Output route |
|---|---|---|---|
| `Ctrl+O` (configurable via `KOLLZSH_HOTKEY`) | `fzf_kollzsh` | Navigation | fzf selection → BUFFER |
| `Ctrl+F` | `fzf_kollzsh_deep` | Deep search | Direct to BUFFER (no fzf) |

## Daemon lifecycle

- Starts on first widget invocation (`ensure_daemon_running` in `koll.zsh`)
- Shuts down on ZSH exit (trap EXIT runs `_kollzsh_cleanup`, SIGTERMs daemon)
- **Auto-restart on code change**: `ensure_daemon_running` compares `.py` timestamps against `/tmp/kollzshd.pid` — if daemon code is newer, kills old daemon and starts fresh
- PID file: `/tmp/kollzshd.pid` — daemon refuses to start if another instance is alive
- Inactivity timeout: 1800 seconds with no requests → daemon shuts down
- Single daemon serves all ZSH sessions sharing the same socket

## Socket protocol

All daemon I/O is JSON over a Unix socket (`/tmp/kollzshd.sock`).

**Request** (ZSH → daemon):
```json
{"query": "...", "mode": "navigation|deep"}
```

**Navigation response** (daemon → ZSH, single JSON object):
```json
{"lines": ["..."], "cwd": "/path"}
```

**Deep mode** uses **streaming**: daemon sends multiple JSON lines, one per event. ZSH reads line-by-line via `s.makefile("r")`:

| Event type | Fields | When |
|---|---|---|
| `think` | `status`, `msg`, `round` | LLM thinking / Pi turn start/end |
| `cmd` | `cmd`, `round` | Command being executed |
| `out` | `lines`, `round` | Command output lines |
| `read` | `file`, `round` | Auto-reading a file |
| `result` | `lines` | Full Pi result (before truncation) |
| `error` | `msg` | LLM or Pi error |
| `done` | `lines`, `cwd` | Final event; ZSH captures this as stdout |

ZSH renders progress events to stderr (visible during search) and only the `done` event is captured to stdout for buffer insertion. Socket has a 300-second timeout.

## Deep mode (Pi DCI-Agent)

- Custom fork of Pi: `https://github.com/jdf-prog/pi-mono.git`, branch `codex/context-management-ablation`
- Auto-setup: finds Node.js >=20 (tries NVM, falls back to install), clones and `npm install && npm run build` in `pi-mono/`, generates `models.json` in `KOLLZSH_PI_AGENT_DIR`
- Runs as a Node.js subprocess (`--mode rpc --tools read,bash --no-session`)
- `start_new_session=True` in subprocess to prevent tty leak
- 300-second timeout for Pi queries
- Max turns configurable via `KOLLZSH_PI_MAX_TURNS` (default 20)
- Context management profiles: `level0`-`level5` (`KOLLZSH_PI_CONTEXT_LEVEL`, default `level3`)

## Config variables (set in `~/.zshrc` before sourcing oh-my-zsh)

| Var | Default | Notes |
|---|---|---|
| `KOLLZSH_URL` | `http://localhost:8080` | OpenAI-compatible `/v1/chat/completions` server |
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL` | Must appear in `GET /v1/models` |
| `KOLLZSH_HOTKEY` | `^o` | ZLE widget binding for navigation |
| `KOLLZSH_DAEMON_SOCK` | `/tmp/kollzshd.sock` | Unix socket path |
| `KOLLZSH_PLUGIN_DIR` | auto-detected from script location | Override plugin directory |
| `KOLLZSH_PI_MAX_TURNS` | `20` | Max Pi turns per deep search |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Pi context management (level0-level5) |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Pi agent config directory |
| `KOLLZSH_SYSTEM_CONTEXT` | (empty) | Extra text injected into LLM system prompt |
| `KOLLZSH_COMMAND_COUNT` | `5` | Used in koll.zsh but not wired to daemon |

## Command whitelist (`kollzshd_commands.py`)

Read-only commands (auto-execute):
`grep rg ag find ls cat head tail wc stat file sort uniq diff tree pwd echo which type du df bat less strings nl od xxd column cut tr fmt fold expand pr printf env dirname basename realpath readlink date cal bc seq shuf tsort comm paste join look split cksum md5sum sha1sum sha256sum`

Destructive commands require user confirmation via fzf:
`rm mv cp chmod chown sudo kill apt pacman brew dnf yum pip npm docker systemctl mkfs dd shutdown reboot halt poweroff init`

Pipelines with destructive commands are blocked. Redirects to block devices are blocked. Dangerous patterns (`rm -rf /`) are regex-checked.

## Development workflow

- **No test framework, no CI, no linter** — test manually by sourcing the plugin in ZSH
- Debug log: `tail -f /tmp/kollzsh_debug.log`
- After changing any `.py` file, the daemon auto-restarts on next widget invocation (timestamp check)
- After changing `koll.zsh` or `utils.zsh`, re-source: `source ~/.zshrc` or open a new terminal

## Gotchas

- Socket communication uses inline Python (`python3 -c '...'`), not `socat` or `jq`
- `send_to_daemon` uses double-quoted Python (safe for single quotes in queries)
- `stream_from_daemon` uses single-quoted Python (all Python strings must use double quotes)
- Daemon's bash subprocess uses `--norc --noprofile` — no user aliases or configs
- CWD is synced by appending `; echo '__KSEP__'; pwd; echo '__KEND__'` to every command
- `validate_required` checks: `fzf`, `python3`, LLM server health (`/v1/models`), model existence
- LLM communication uses `urllib.request` (stdlib), not `requests` library
- `pi-mono/` and `DCI-Agent-Lite/` are gitignored (auto-managed by daemon)
