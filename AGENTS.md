# AGENTS.md — kollzsh

## Architecture

Oh-my-zsh plugin with a persistent Python daemon. Two ZLE widgets capture the
buffer, send a query to the daemon via Unix socket, pipe results through fzf,
and insert the selection.

**ZSH layer:**
- `kollzsh.plugin.zsh` — oh-my-zsh entry point (sources `koll.zsh`)
- `koll.zsh` — widget definitions, hotkey bindings, daemon lifecycle, validation
- `utils.zsh` — `check_command`, `check_llm_running`, `check_daemon_running`

**Daemon (Python, stdlib only — no pip deps):**
- `kollzshd.py` — socket server (`/tmp/kollzshd.sock`), persistent bash subprocess, CWD tracking, agent loop
- `kollzshd_commands.py` — command whitelist, safety validation, `execute_command` with `__KSEP__`/`__KEND__` marker protocol, `truncate_output`
- `kollzshd_llm.py` — prompt construction (navigation vs deep), HTTP calls to LLM, response parsing
- `kollzshd_pi.py` — Pi RPC client for deep search (Node.js DCI-Agent), auto-setup

**Legacy (unused):**
- `llm_util.py` — old stateless LLM bridge
- `ollama_util.py` — old Ollama client

## Key facts

- **No test framework, no CI, no linter** — test manually by sourcing the plugin in zsh
- Debug log: `/tmp/kollzsh_debug.log` (append-only, readable by all)
- All Python is stdlib only — no venv, no pip
- Daemon auto-starts on first use, auto-dies on ZSH exit (trap EXIT)
- PID file: `/tmp/kollzshd.pid` — daemon refuses to start if another instance is alive

## Hotkeys

| Key | Widget | Mode | Rounds |
|---|---|---|---|
| `Ctrl+O` | `fzf_kollzsh` | Navigation | 1 |
| `Ctrl+F` | `fzf_kollzsh_deep` | Deep search | Multi-turn (Pi) |

## How the daemon works

1. ZSH sends `{"query": "...", "mode": "navigation|deep"}` to the Unix socket
2. Daemon starts a persistent `bash --norc --noprofile` subprocess
3. LLM generates shell commands (grep, find, ls, etc.) — no tool abstractions
4. Daemon executes commands in the persistent shell, captures stdout, syncs CWD via `pwd`
5. Output is truncated (sandwich: top 20 + bottom 20 lines) and returned as JSON
6. Deep mode spawns Pi (Node.js DCI-Agent) for multi-turn research with context management

## Config vars (set in `~/.zshrc` before sourcing oh-my-zsh)

| Var | Default | Notes |
|---|---|---|
| `KOLLZSH_URL` | `http://localhost:8080` | Any OpenAI-compatible `/v1/chat/completions` server |
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL` | Must appear in `GET /v1/models` |
| `KOLLZSH_HOTKEY` | `^o` | ZLE widget binding for navigation |
| `KOLLZSH_DAEMON_SOCK` | `/tmp/kollzshd.sock` | Unix socket for daemon communication |
| `KOLLZSH_PLUGIN_DIR` | auto-detected | Override plugin directory |
| `KOLLZSH_PI_MAX_TURNS` | `20` | Turns máximos por deep search via Pi |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Nível de context management (level0-level5) |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Diretório do models.json do Pi |

## Command whitelist

Read-only commands (auto-execute): `grep rg ag find ls cat head tail wc stat file sort uniq diff tree pwd echo which type du df bat less strings nl od xxd column cut tr fmt fold expand pr printf env dirname basename realpath readlink date cal bc seq shuf tsort comm paste join look split cksum md5sum sha1sum sha256sum`

Destructive commands require user confirmation via fzf.

## Gotchas

- `socat` is NOT used — `send_to_daemon` in `koll.zsh` uses a Python one-liner for socket communication
- `jq` is NOT required — Python handles JSON parsing
- `validate_required` checks: `fzf`, `python3`, LLM server health, model existence
- The daemon's bash subprocess uses `--norc --noprofile` to avoid user configs
- CWD is tracked by appending `; echo "__KSEP__"; pwd; echo "__KEND__"` to every command
