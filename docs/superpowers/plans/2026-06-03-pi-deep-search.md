# Pi Deep Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Ctrl+F deep search in kollzsh with Pi (DCI-Agent-Lite RPC), using a local llama.cpp model, with zero-touch setup.

**Architecture:** A new `kollzshd_pi.py` module spawns Pi (Node.js) in RPC mode per query, communicates via JSON-RPC over stdin/stdout, and returns the final answer. `kollzshd.py` routes mode=="deep" to this module. Pi is auto-cloned and built on first use.

**Tech Stack:** Python stdlib (no new deps), Node.js 20+ (installed via NVM if missing), Pi CLI (forked pi-mono).

---

### Task 1: Create `kollzshd_pi.py` — Pi RPC module

**Files:**
- Create: `kollzshd_pi.py`

- [ ] **Step 1: Write `kollzshd_pi.py`**

```python
#!/usr/bin/env python3
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

PI_REPO_URL = "https://github.com/jdf-prog/pi-mono.git"
PI_BRANCH = "codex/context-management-ablation"


def log_debug(message: str, data: Optional[str] = None) -> None:
    if data:
        logging.debug(f"{message}\nData: {data}\n----------------------------------------")
    else:
        logging.debug(message)


def _find_node() -> str:
    nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
    versions_dir = nvm_dir / "versions" / "node"
    if versions_dir.is_dir():
        candidates = sorted(
            (d for d in versions_dir.iterdir() if d.name.startswith("v")),
            key=lambda d: tuple(int(x) for x in d.name.lstrip("v").split(".")),
            reverse=True,
        )
        for candidate in candidates:
            major = int(candidate.name.lstrip("v").split(".")[0])
            node = candidate / "bin" / "node"
            if major >= 20 and node.exists():
                return str(node)
    node = shutil.which("node")
    if node:
        try:
            ver = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
            if ver.returncode == 0 and ver.stdout.strip():
                major = int(ver.stdout.strip().lstrip("v").split(".")[0])
                if major >= 20:
                    return node
        except Exception:
            pass
    return ""


def _ensure_node(plugin_dir: str) -> str:
    node = _find_node()
    if node:
        return node
    log_debug("Node.js >=20 not found, installing via NVM")
    nvm_install = subprocess.run(
        ["curl", "-o-", "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh"],
        capture_output=True, text=True, timeout=30,
    )
    if nvm_install.returncode != 0:
        raise RuntimeError("Failed to download NVM installer")
    result = subprocess.run(
        ["bash", "-c", nvm_install.stdout],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"NVM install failed: {result.stderr}")
    nvm_dir = os.path.expanduser("~/.nvm")
    nvm_sh = os.path.join(nvm_dir, "nvm.sh")
    if not os.path.exists(nvm_sh):
        raise RuntimeError("NVM installed but nvm.sh not found")
    install_node = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(nvm_sh)} && nvm install 20 && nvm use 20 && which node"],
        capture_output=True, text=True, timeout=120,
    )
    if install_node.returncode != 0:
        raise RuntimeError(f"Node install failed: {install_node.stderr}")
    node_path = install_node.stdout.strip()
    if not node_path:
        raise RuntimeError("Node installed but path not found")
    log_debug(f"Node installed at: {node_path}")
    return node_path


def _ensure_pi_build(plugin_dir: str) -> str:
    pi_dir = os.path.join(plugin_dir, "pi-mono")
    package_dir = os.path.join(pi_dir, "packages", "coding-agent")
    cli_path = os.path.join(package_dir, "dist", "cli.js")

    if os.path.exists(cli_path):
        return package_dir

    log_debug("Pi CLI not found, cloning and building pi-mono")
    if not os.path.exists(pi_dir):
        result = subprocess.run(
            ["git", "clone", "--depth", "1", PI_REPO_URL, pi_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")
        result = subprocess.run(
            ["git", "checkout", PI_BRANCH],
            cwd=pi_dir, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git checkout failed: {result.stderr}")

    log_debug("Running npm install in pi-mono")
    result = subprocess.run(
        ["npm", "install"],
        cwd=pi_dir, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm install failed: {result.stderr}")

    log_debug("Running npm run build in pi-mono")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=pi_dir, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"npm run build failed: {result.stderr}")

    if not os.path.exists(cli_path):
        raise RuntimeError(f"Build completed but CLI not found at {cli_path}")

    log_debug(f"Pi built successfully at {package_dir}")
    return package_dir


def _ensure_models_json(agent_dir: str, url: str, model: str) -> str:
    os.makedirs(agent_dir, exist_ok=True)
    models_path = os.path.join(agent_dir, "models.json")

    base_url = url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    provider = {
        "providers": {
            "local": {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": "dummy",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": model}],
            }
        }
    }

    new_content = json.dumps(provider, indent=2, ensure_ascii=False)

    if os.path.exists(models_path):
        try:
            existing = open(models_path).read()
            if existing.strip() == new_content.strip():
                return models_path
        except Exception:
            pass

    with open(models_path, "w") as f:
        f.write(new_content)

    log_debug(f"Generated models.json at {models_path}")
    return models_path


def ensure_pi_ready(plugin_dir: str, agent_dir: str, url: str, model: str) -> str:
    node_path = _ensure_node(plugin_dir)
    package_dir = _ensure_pi_build(plugin_dir)
    _ensure_models_json(agent_dir, url, model)
    return node_path


def run_pi_query(
    cwd: str,
    query: str,
    plugin_dir: str,
    agent_dir: str,
    url: str,
    model: str,
    max_turns: int = 20,
    context_level: str = "level3",
) -> List[str]:
    node_path = ensure_pi_ready(plugin_dir, agent_dir, url, model)
    package_dir = os.path.join(plugin_dir, "pi-mono", "packages", "coding-agent")
    cli_path = os.path.join(package_dir, "dist", "cli.js")

    cmd = [
        node_path,
        cli_path,
        "--mode", "rpc",
        "--provider", "local",
        "--model", model,
        "--tools", "read,bash",
        "--no-session",
        "--context-management-level", context_level,
    ]

    log_debug(f"Spawning Pi RPC: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PI_CODING_AGENT_DIR"] = agent_dir

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    prompt = json.dumps({"id": "1", "type": "prompt", "message": query}) + "\n"
    proc.stdin.write(prompt.encode())
    proc.stdin.flush()

    text_parts: List[str] = []
    seen_turns = 0
    sent_abort = False

    try:
        while True:
            raw = proc.stdout.readline()
            if not raw:
                break

            raw = raw.strip()
            if not raw:
                continue

            try:
                event = json.loads(raw.decode())
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            if event_type == "turn_start":
                seen_turns += 1
                if max_turns and seen_turns > max_turns and not sent_abort:
                    abort = json.dumps({"id": "2", "type": "abort"}) + "\n"
                    proc.stdin.write(abort.encode())
                    proc.stdin.flush()
                    sent_abort = True
                continue

            if event_type == "message_update":
                assistant = event.get("assistantMessageEvent", {})
                delta = assistant.get("delta", "")
                if delta:
                    text_parts.append(delta)
                continue

            if event_type == "agent_end":
                break
    finally:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass

    result = "".join(text_parts).strip()
    stderr_text = proc.stderr.read().decode() if proc.stderr else ""
    if stderr_text:
        log_debug("Pi stderr:", stderr_text[:500])

    if not result:
        log_debug("Pi returned empty result, checking stderr")
        result = f"[Deep search error] Check /tmp/kollzsh_debug.log for details"

    return result.split("\n")
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('kollzshd_pi.py').read()); print('OK')"`
Expected: `OK`

---

### Task 2: Modify `kollzshd.py` — route deep mode to Pi

**Files:**
- Modify: `kollzshd.py`

- [ ] **Step 1: Add import for kollzshd_pi at top of `kollzshd.py`**

```python
from kollzshd_pi import run_pi_query, log_debug as pi_log_debug
```

- [ ] **Step 2: Replace the deep mode logic in `run_agent_loop`**

Insert this at the top of `run_agent_loop`, right after `log_debug(...)` and before the loop:

```python
if mode == "deep":
    plugin_dir = os.environ.get(
        "KOLLZSH_PLUGIN_DIR",
        os.path.dirname(os.path.abspath(__file__)),
    )
    agent_dir = os.environ.get(
        "KOLLZSH_PI_AGENT_DIR",
        os.path.expanduser("~/.pi/agent"),
    )
    url = os.environ.get("KOLLZSH_URL", "http://localhost:8080")
    model = os.environ.get("KOLLZSH_MODEL", "unsloth/Qwen3.5-4B-GGUF:UD-Q6_K_XL")
    max_turns = int(os.environ.get("KOLLZSH_PI_MAX_TURNS", "20"))
    context_level = os.environ.get("KOLLZSH_PI_CONTEXT_LEVEL", "level3")

    log_debug(f"Deep mode via Pi: url={url}, model={model}, cwd={self.cwd}")
    try:
        lines = run_pi_query(
            self.cwd, query, plugin_dir, agent_dir,
            url, model, max_turns, context_level,
        )
        return truncate_output(lines)
    except Exception as e:
        log_debug(f"Pi query failed: {e}")
        return [f"Deep search error: {e}"]
```

- [ ] **Step 3: Add `os` to imports if not already there**

Verify `os` is imported in kollzshd.py (it already is at line 24).

- [ ] **Step 4: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('kollzshd.py').read()); print('OK')"`
Expected: `OK`

---

### Task 3: Update `koll.zsh` — status message

**Files:**
- Modify: `koll.zsh`

- [ ] **Step 1: Replace the status message in `fzf_kollzsh_deep`**

Change:
```zsh
echo "🔍 Buscando e analisando..."
```
To:
```zsh
echo "🔍 Deep search (DCI-Agent)..."
```

---

### Task 4: Update `AGENTS.md` — document new config vars

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add Pi-specific config vars to the table**

Add after the existing `KOLLZSH_PLUGIN_DIR` row:

```markdown
| `KOLLZSH_PI_MAX_TURNS` | `20` | Turns máximos por deep search via Pi |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Nível de context management (level0-level4) |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Diretório do models.json do Pi |
```

- [ ] **Step 2: Update the deep mode description**

Change line 45 from:
```
6. Deep mode runs up to 2 rounds: generate → execute → LLM evaluates → maybe refine
```
To:
```
6. Deep mode spawns Pi (Node.js DCI-Agent) for multi-turn research with context management
```

---

### Task 5: Build pi-mono and verify

**Files:**
- Run commands only

- [ ] **Step 1: Build pi-mono**

```bash
cd /home/marcos/kollzsh
git clone https://github.com/jdf-prog/pi-mono.git pi-mono
cd pi-mono
git checkout codex/context-management-ablation
npm install
npm run build
```

- [ ] **Step 2: Verify CLI exists**

```bash
ls -la pi-mono/packages/coding-agent/dist/cli.js
```

- [ ] **Step 3: Test Pi RPC mode works**

```bash
echo '{"id":"1","type":"prompt","message":"hello"}' | node pi-mono/packages/coding-agent/dist/cli.js --mode rpc --provider local --model test
```
Expected: Connects and returns an event (will likely fail since no provider is running, but should not crash with script errors).

- [ ] **Step 4: Run Python syntax check on all files**

```bash
python3 -c "
import ast
for f in ['kollzshd.py', 'kollzshd_pi.py', 'kollzshd_commands.py', 'kollzshd_llm.py']:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"
```
Expected: `OK` for all files.
