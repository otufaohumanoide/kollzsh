# koll.zsh

```
  :###:
  :   :
  :   :
.'     '.
:       :
|_______|
|kollzsh|
|‐‐‐‐‐‐‐|
|       |
:_______:
```

An [`oh-my-zsh`](https://ohmyz.sh) plugin that integrates LLMs (OpenAI-compatible API) via [fzf](https://github.com/junegunn/fzf) to provide intelligent shell command suggestions and deep filesystem search.

<img src="demo.svg" alt="Kollzsh Demo" width="600">

## Features

* **Engine-agnostic**: Works with any OpenAI-compatible API — llama.cpp, vLLM, Ollama, etc.
* **Intelligent Command Suggestions**: Generate shell commands based on your natural language query.
* **Deep Search**: LLM executes grep, find, rg, cat, etc. directly via a persistent daemon — explores your filesystem for you.
* **FZF Integration**: Interactively select suggested commands using FZF's fuzzy finder.
* **Command Safety**: Whitelist of read-only commands executes automatically; destructive commands require user confirmation.
* **Customizable**: Configure shortcut, model, server URL, and response count.

## Requirements

* `fzf` for interactive selection
* `python3` for the daemon backend (stdlib only, no pip dependencies)
* An LLM server with OpenAI-compatible `/v1/chat/completions` endpoint

## Installation

1. Clone the repository to oh-my-zsh custom plugin folder:
    ```bash
    git clone https://github.com/otufaohumanoide/kollzsh.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/kollzsh
    ```

2. Enable the plugin in `~/.zshrc`:
    ```bash
    plugins=(
      [plugins...]
      kollzsh
    )
    ```

3. Type a task description in your terminal and press the shortcut (default Ctrl+`o`) to get command suggestions. Select one with fzf.

## Hotkeys

| Key | Widget | Mode | Description |
|---|---|---|---|
| `Ctrl+O` | `fzf_kollzsh` | Navigation | LLM generates commands, daemon executes, fzf selects |
| `Ctrl+G` | `fzf_kollzsh_deep` | Deep Search | LLM explores with grep/find/rg, refines results, fzf selects |

## Architecture

```
ZSH widget (Ctrl+O / Ctrl+G)
       │
       ▼
Daemon Python (kollzshd.py)
  ├── Persistent bash subprocess (--norc --noprofile)
  ├── CWD synced via pwd after every command
  ├── Command safety filter (whitelist read-only)
  ├── Output truncation (top 20 + bottom 20 lines)
  └── Max 2 rounds per query
       │
       ├─── Round 1: LLM writes commands → daemon executes → stdout truncated
       ├─── Round 2 (deep only): LLM evaluates output → refines or finalizes
       └─── Final: output → fzf → user selects
```

The daemon starts automatically on first use and shuts down when ZSH exits. A PID file (`/tmp/kollzshd.pid`) prevents duplicate instances.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-GGUF:UD-Q6_K_XL` | Model name on the LLM server |
| `KOLLZSH_HOTKEY` | `^o` | Hotkey binding for navigation mode |
| `KOLLZSH_URL` | `http://localhost:8080` | LLM server URL |
| `KOLLZSH_DAEMON_SOCK` | `/tmp/kollzshd.sock` | Unix socket for daemon communication |

### Server URLs for different engines

| Engine | KOLLZSH_URL |
|---|---|
| Ollama | `http://localhost:11434` |
| llama.cpp server | `http://localhost:8080` (default) |
| vLLM | `http://localhost:8000` |

## Debugging

Logs are written to `/tmp/kollzsh_debug.log`. Enable with:
```bash
tail -f /tmp/kollzsh_debug.log
```
