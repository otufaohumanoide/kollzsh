import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from kollzshd_logging import log_debug

PI_REPO_URL: str = "https://github.com/jdf-prog/pi-mono.git"
PI_BRANCH: str = "codex/context-management-ablation"

EventCallback = Callable[..., None]


def _find_node() -> Optional[str]:
    """Try to find a Node.js executable >=20."""
    candidates = []
    nvm_node = os.path.expanduser("~/.nvm/versions/node/*/bin/node")
    if os.path.exists(os.path.expanduser("~/.nvm")):
        import glob
        candidates.extend(sorted(glob.glob(nvm_node), reverse=True))
    which_node = shutil.which("node")
    if which_node:
        candidates.append(which_node)
    for candidate in candidates:
        try:
            version = subprocess.check_output(
                [candidate, "--version"], text=True, stderr=subprocess.DEVNULL
            ).strip()
            major = int(version.lstrip("v").split(".")[0])
            if major >= 20:
                return candidate
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue
    return None


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
            ["git", "clone", "--depth", "1", "--branch", PI_BRANCH, PI_REPO_URL, pi_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")
    else:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=pi_dir, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or result.stdout.strip() != PI_BRANCH:
            current_branch = result.stdout.strip() if result.returncode == 0 else "not-a-git-repo"
            log_debug(f"Pi dir exists but on '{current_branch}', expected '{PI_BRANCH}'. Re-cloning.")
            shutil.rmtree(pi_dir)
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", PI_BRANCH, PI_REPO_URL, pi_dir],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr}")

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


def ensure_pi_ready(
    plugin_dir: str,
    agent_dir: str,
    url: str,
    model: str,
    event_callback: Optional[EventCallback] = None,
) -> str:
    if event_callback:
        event_callback("think", status="start", msg="Checking Node.js...")
    node_path = _ensure_node(plugin_dir)
    if event_callback:
        event_callback("think", status="start", msg="Checking Pi build...")
    _ensure_pi_build(plugin_dir)
    if event_callback:
        event_callback("think", status="start", msg="Setting up models...")
    _ensure_models_json(agent_dir, url, model)
    if event_callback:
        event_callback("think", status="start", msg="Pi ready.")
    return node_path
