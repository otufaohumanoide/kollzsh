#!/usr/bin/env python3
import json
import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

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
    _ensure_pi_build(plugin_dir)
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
        result = "[Deep search error] Check /tmp/kollzsh_debug.log for details"

    return result.split("\n")
