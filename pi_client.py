import json
import os
import select
import subprocess
import time
from typing import Callable, List, Optional

from kollzshd_logging import log_debug
from pi_setup import ensure_pi_ready

PI_QUERY_TIMEOUT: int = 300
EventCallback = Callable[..., None]


def run_pi_query(
    cwd: str,
    query: str,
    plugin_dir: str,
    agent_dir: str,
    url: str,
    model: str,
    max_turns: int = 20,
    context_level: str = "level3",
    event_callback: Optional[EventCallback] = None,
) -> List[str]:
    node_path = ensure_pi_ready(plugin_dir, agent_dir, url, model, event_callback)
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
        start_new_session=True,
    )

    extra = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
    librarian_query = (
        f"Search topic: {query}\n\n"
        f"Your job: find relevant files in this filesystem and return "
        f"their paths + full content. NEVER answer questions or explain "
        f"anything. Only search and return files."
    )
    if extra:
        librarian_query += f"\n\nUser context: {extra}"
    prompt = json.dumps({"id": "1", "type": "prompt", "message": librarian_query}) + "\n"
    proc.stdin.write(prompt.encode())
    proc.stdin.flush()

    text_parts: List[str] = []
    tool_outputs: List[str] = []
    seen_turns = 0
    sent_abort = False

    try:
        deadline = time.time() + PI_QUERY_TIMEOUT
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                log_debug(f"Pi query timed out after {PI_QUERY_TIMEOUT}s")
                break

            ready, _, _ = select.select([proc.stdout], [], [], min(1.0, remaining))
            if not ready:
                continue

            raw = proc.stdout.readline()
            if not raw:
                break

            raw = raw.strip()
            if not raw:
                continue

            try:
                event = json.loads(raw.decode())
            except json.JSONDecodeError:
                log_debug(f"Pi JSON decode error for line: {raw[:200]}")
                continue

            event_type = event.get("type")

            if event_type == "turn_start":
                seen_turns += 1
                if event_callback:
                    event_callback("think", status="start", msg=f"Pi turn {seen_turns}/{max_turns}")
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

            if event_type == "tool_execution_end":
                tool_name = event.get("toolName", "?")
                result = event.get("result", "")
                result_str = ""
                if isinstance(result, dict):
                    content = result.get("content", [])
                    if isinstance(content, list) and content:
                        first = content[0]
                        if isinstance(first, dict):
                            result_str = first.get("text", "") or first.get("output", "") or ""
                    if not result_str:
                        result_str = result.get("stdout", "") or result.get("output", "") or result.get("text", "") or ""
                    if not result_str:
                        result_str = json.dumps(result, indent=2)
                elif result:
                    result_str = str(result).strip()
                if result_str:
                    tool_outputs.append(f"--- [{tool_name}] ---")
                    tool_outputs.append(result_str)
                    tool_outputs.append("")
                continue

            if event_type == "tool_execution_update":
                continue

            if event_type == "agent_end":
                log_debug("Pi agent_end received")
                if event_callback:
                    event_callback("think", status="end")
                break

            if event_type not in ("tool_use", "tool_result", "tool_execution_start", "message_start", "message_end", "turn_end", "provider_request_context"):
                log_debug(f"Pi unknown event: {event_type}")
    finally:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass

    result = "".join(text_parts).strip()
    if tool_outputs:
        tool_block = "\n".join(tool_outputs).strip()
        if result:
            result = result + "\n\n" + tool_block
        else:
            result = tool_block
    stderr_text = proc.stderr.read().decode() if proc.stderr else ""
    if stderr_text:
        log_debug("Pi stderr:", stderr_text[:500])

    if not result:
        log_debug("Pi returned empty result, checking stderr")
        result = "[Deep search error] Check /tmp/kollzsh_debug.log for details"
    elif event_callback:
        log_debug(f"Pi completed: {len(result)} chars, {len(result.splitlines())} lines")

    return result.split("\n")
