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
