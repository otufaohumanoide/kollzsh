# AGENTS.md — kollzsh

## Architecture

This is an oh-my-zsh plugin. A ZSH widget (`fzf_kollzsh`) captures the current buffer,
calls `llm_util.py` with it, pipes results through fzf, and inserts the selection.

**Files:**
- `kollzsh.plugin.zsh` — oh-my-zsh entry point (sources `koll.zsh`)
- `koll.zsh` — widget definition, hotkey binding, validation flow
- `utils.zsh` — `check_command`, `check_llm_running`, OS detection
- `llm_util.py` — calls `POST {KOLLZSH_URL}/v1/chat/completions` with OpenAI function calling; **stdlib only** (urllib, json, re, ast — no pip deps)
- `ollama_util.py` — legacy, unused

## Key facts

- **No test framework, no CI, no linter** — just source the plugin in zsh and test manually
- Debug log: `/tmp/kollzsh_debug.log` (append-only, readable by all)
- The Python script is invoked directly via `python3` (no venv)
- Health check: `GET {KOLLZSH_URL}/v1/models` must return 200

## Bugs / dead code

- `KOLLZSH_RESPONSE` at `koll.zsh:62` is never set — log line is always empty
- `KOLLZSH_COMMAND_COUNT` is defined but never read
- `jq` is checked in `validate_required` but never used in the current code path

## Config vars (set in `~/.zshrc` before sourcing oh-my-zsh)

| Var | Default | Notes |
|---|---|---|
| `KOLLZSH_URL` | `http://localhost:8080` | Any OpenAI-compatible `/v1/chat/completions` server |
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL` | Must appear in `GET /v1/models` |
| `KOLLZSH_HOTKEY` | `^o` | ZLE widget binding |
| `KOLLZSH_COMMAND_COUNT` | `5` | Dead — not consumed anywhere |
