import atexit
import json
import os
import select
import signal
import subprocess
import time
from typing import Callable, List, Optional

from kollzshd_logging import log_debug
from pi_setup import ensure_pi_ready

PI_QUERY_TIMEOUT: int = 300
EventCallback = Callable[..., None]

_pi_proc: "subprocess.Popen[str] | None" = None


def _cleanup_pi() -> None:
    global _pi_proc
    if _pi_proc is not None and _pi_proc.poll() is None:
        _pi_proc.terminate()
        try:
            _pi_proc.wait(timeout=2)
        except Exception:
            _pi_proc.kill()
        _pi_proc = None


atexit.register(_cleanup_pi)


def _ensure_pi_running(
    agent_dir: str, plugin_dir: str,
    url: str, model: str,
    context_level: str,
    event_callback: Optional[EventCallback] = None,
) -> "subprocess.Popen[str]":
    global _pi_proc
    if _pi_proc is not None and _pi_proc.poll() is None:
        return _pi_proc

    agent_config = os.path.join(agent_dir, "agent.json")
    if not os.path.exists(agent_config):
        if event_callback:
            event_callback("think", status="start", msg="Setting up Pi agent...")
    node_path = ensure_pi_ready(plugin_dir, agent_dir, url, model, event_callback)

    _pi_proc = subprocess.Popen(
        [
            node_path, "packages/coding-agent/dist/cli.js",
            "--mode", "rpc",
            "--provider", "local",
            "--model", model,
            "--tools", "read,bash,grep,find,ls",
            "--no-session",
            "--context-management-level", context_level,
        ],
        cwd=os.path.join(plugin_dir, "pi-mono"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    return _pi_proc


def _build_librarian_prompt(query: str, extra: str) -> str:
    kb_path = os.path.join(os.path.expanduser("~/.pi/agent/extensions/estagiario-data"), "atomos")
    prompt = (
        f"You are a legal research librarian. Your task is to find relevant legal knowledge atoms.\n\n"
        f"KNOWLEDGE BASE STRUCTURE:\n"
        f"The knowledge base is at {kb_path}\n"
        f"Each subdirectory is a law code: cp/ (Codigo Penal), cc/ (Codigo Civil), cdc/ (Consumidor), etc.\n"
        f"Each law code has topic subdirectories with atom .md files.\n"
        f"Read _manifest.json in each directory for an index of atoms.\n"
        f"Read _meta.json for deontic classification and relations between atoms.\n\n"
        f"QUERY DECOMPOSITION:\n"
        f"Before searching, decompose complex queries into separate search terms.\n"
        f"Example: 'roubo onde a vitima foi agredida' → search for: (1) 'roubo', (2) 'lesao corporal' or 'agressao', (3) 'agravantes' or 'circunstancias'\n"
        f"Example: 'homicidio culposo com reincidencia' → search for: (1) 'homicidio culposo', (2) 'reincidencia'\n"
        f"Example: 'furto com violencia' → search for: (1) 'furto', (2) 'violencia', (3) 'roubo' (furto com violencia pode ser roubo)\n\n"
        f"SEARCH STRATEGY:\n"
        f"1. First, list the top-level directories to understand available law codes\n"
        f"2. Use grep to search for your decomposed terms across the knowledge base\n"
        f"3. Read _manifest.json files to find relevant atoms by topic\n"
        f"4. Read _meta.json to understand relations between atoms (requisito_de, causa_de, etc.)\n"
        f"5. Read the actual atom .md files to get the legal content\n"
        f"6. If searching for agravantes, aggravating circumstances, or special conditions, search for those terms explicitly\n"
        f"7. If the query involves multiple crimes or concurrence (concurso), search for each crime separately\n\n"
        f"OUTPUT:\n"
        f"Return the FULL content of ALL matching atoms found.\n"
        f"Include the atom file path for reference.\n"
        f"STOP as soon as you find clear answers for ALL decomposed terms.\n"
        f"AVOID: raw file listings without reading, directory dumps, or extra explanation."
    )
    if query.strip():
        prompt += f"\n\nUSER QUERY:\n{query}"
    if extra:
        prompt += f"\n\nUser context: {extra}"
    return prompt


def run_pi_query(
    cwd: str,
    query: str,
    plugin_dir: str,
    agent_dir: str,
    url: str,
    model: str,
    max_turns: int = 6,
    context_level: str = "level3",
    event_callback: Optional[EventCallback] = None,
) -> List[str]:
    global _pi_proc
    try:
        proc = _ensure_pi_running(agent_dir, plugin_dir, url, model, context_level, event_callback)
    except RuntimeError as exc:
        if event_callback:
            event_callback("error", msg=str(exc))
        return [f"Pi setup error: {exc}"]

    extra = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
    librarian_query = _build_librarian_prompt(query, extra)
    prompt = json.dumps({"id": "1", "type": "prompt", "message": librarian_query}) + "\n"
    proc.stdin.write(prompt)
    proc.stdin.flush()

    text_parts: List[str] = []
    tool_outputs: List[str] = []
    seen_turns = 0
    sent_abort = False

    try:
        while True:
            try:
                ready, _, _ = select.select([proc.stdout], [], [], PI_QUERY_TIMEOUT)
                if not ready:
                    proc.kill()
                    _pi_proc = None
                    if event_callback:
                        event_callback("error", msg=f"Pi query timed out after {PI_QUERY_TIMEOUT}s")
                    return [f"Pi query timed out after {PI_QUERY_TIMEOUT}s"]

                line = proc.stdout.readline()
                if not line:
                    break

                raw = line.strip()
                if not raw:
                    continue
            except (BrokenPipeError, OSError) as exc:
                if event_callback:
                    event_callback("error", msg=f"Pi connection lost: {exc}")
                _pi_proc = None
                return [f"Pi connection lost: {exc}"]

            try:
                event = json.loads(raw)
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
                    proc.stdin.write(abort)
                    proc.stdin.flush()
                    sent_abort = True
                continue

            if event_type == "message_update":
                assistant = event.get("assistantMessageEvent", {})
                delta = assistant.get("delta", "")
                if delta:
                    text_parts.append(delta)
                continue

            if event_type == "message_end":
                assistant = event.get("assistantMessageEvent", {})
                msg = assistant.get("message", {})
                raw = msg.get("content", "") or assistant.get("content", "") or event.get("content", "")
                if isinstance(raw, list):
                    texts = []
                    for item in raw:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                    raw = " ".join(texts)
                if raw and raw != "None":
                    text_parts.append(str(raw))
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
        _pi_proc = None

    result = "".join(text_parts).strip()
    if not result and tool_outputs:
        result = "\n".join(tool_outputs).strip()
    stderr_text = proc.stderr.read() if proc.stderr else ""
    if stderr_text:
        log_debug("Pi stderr:", stderr_text[:500])

    if not result:
        log_debug("Pi returned empty result, checking stderr")
        result = "[Deep search error] Check /tmp/kollzsh_debug.log for details"
    elif event_callback:
        log_debug(f"Pi completed: {len(result)} chars, {len(result.splitlines())} lines")

    return result.split("\n")
