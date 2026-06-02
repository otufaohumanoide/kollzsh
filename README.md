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

An [`oh-my-zsh`](https://ohmyz.sh) plugin that integrates LLMs (OpenAI-compatible API) via [fzf](https://github.com/junegunn/fzf) to provide intelligent shell command suggestions.

<img src="demo.svg" alt="Kollzsh Demo" width="600">

## Features

* **Engine-agnostic**: Works with any OpenAI-compatible API — llama.cpp, vLLM, Ollama, etc.
* **Intelligent Command Suggestions**: Generate shell commands based on your natural language query.
* **FZF Integration**: Interactively select suggested commands using FZF's fuzzy finder.
* **Customizable**: Configure shortcut, model, server URL, and response count.

## Requirements

* `jq` for parsing JSON responses
* `fzf` for interactive selection
* `curl` for API requests
* `python3` for the LLM backend (stdlib only, no pip dependencies)
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

## Configuration

| Variable | Default | Description |
|---|---|---|
| `KOLLZSH_MODEL` | `unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL` | Model name on the LLM server |
| `KOLLZSH_HOTKEY` | `^o` | Hotkey binding (Ctrl+o) |
| `KOLLZSH_COMMAND_COUNT` | `5` | Number of command suggestions |
| `KOLLZSH_URL` | `http://localhost:8080` | LLM server URL |

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
