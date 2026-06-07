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


def _build_librarian_prompt(query: str, extra: str, fontes_dir: str = "") -> str:
    atomos_path = os.path.join(os.path.expanduser("~/.pi/agent/extensions/estagiario-data"), "atomos")
    kb_sections = []

    kb_sections.append(
        f"1. Full law texts: {fontes_dir}/*.md (if available)\n"
        f"   Complete Brazilian laws in markdown files.\n"
        f"   Use grep directly to find relevant paragraphs by keyword.\n"
    )

    kb_sections.append(
        f"2. Structured atoms: {atomos_path}/\n"
        f"   Legal articles decomposed into individual .md atoms, organized by law code\n"
        f"   (cp/Codigo Penal, cc/Codigo Civil, cdc/Consumidor, ctn/Tributario, eca/ECA).\n"
        f"   Each topic directory has _manifest.json (article index) and _meta.json (cross-references).\n"
    )

    prompt = (
        f"You are a legal concept interpreter and research librarian.\n"
        f"Your mission: translate ANY user input into legal concepts, then search and return relevant legal content.\n\n"
        f"————— PHASE 1 — CONCEPT EXTRACTION —————\n"
        f"The user's message may arrive in any form: a question, a statement, a case description,\n"
        f"informal language, comma-separated tags, or a mix of all of these.\n\n"
        f"Your first task is to extract LEGAL CONCEPTS from the message:\n"
        f"- Identify names of crimes, legal institutes, penalties, procedural stages, principles.\n"
        f"- Translate informal/descriptive language into formal legal concepts:\n"
        f"  * \"subtração indevida de itens alheios\" → furto, roubo, apropriação indébita\n"
        f"  * \"invadir casa dos outros\" → violação de domicílio, invasão\n"
        f"  * \"bater em alguém\" → lesão corporal, agressão\n"
        f"  * \"mexer nas coisas alheias\" → furto\n"
        f"  * \"matar alguém\" → homicídio\n"
        f"  * \"pegar emprestado e não devolver\" → apropriação indébita\n"
        f"  * \"vender produto estragado\" → vício do produto, CDC, responsabilidade\n"
        f"  * \"contrato abusivo\" → cláusulas abusivas, CDC, nulidade\n"
        f"  * \"condenado, quer reduzir pena com estudo\" → remição, execução penal, LEP\n"
        f"- For questions about penalties: extract the crime AND the penalty concept.\n"
        f"- For case descriptions: extract ALL legal institutes mentioned or implied.\n"
        f"- For tags: treat each tag as a concept to search.\n\n"
        f"————— PHASE 2 — SEARCH —————\n"
        f"KNOWLEDGE BASES (search BOTH):\n"
        f"{''.join(kb_sections)}\n"
        f"SEARCH STRATEGY:\n"
        f"1. Search full law texts FIRST with grep — fastest and most comprehensive.\n"
        f"   For each extracted concept, grep in {fontes_dir}/*.md\n"
        f"2. When grep finds relevant paragraphs, read the surrounding context.\n"
        f"3. Search atomos second for granular detail using _manifest.json.\n"
        f"4. Use synonyms and related terms:\n"
        f"   * remição → remir, redução de pena, Lei 7.210\n"
        f"   * falta grave → infração disciplinar, indisciplina\n"
        f"   * furto → subtração, coisa alheia, art. 155\n"
        f"5. If a concept is not found anywhere, note it in the report.\n\n"
        f"————— OUTPUT FORMAT —————\n"
        f"Your ENTIRE response must follow this EXACT structure with NO other text:\n\n"
        f"CONCEITOS:\n"
        f"- conceito1\n"
        f"- conceito2\n\n"
        f"MATERIAL:\n"
        f"Fonte: nome_da_lei, Art. XX\n"
        f"<texto completo do dispositivo legal>\n\n"
        f"Fonte: nome_da_lei, Art. YY, § ZZ\n"
        f"<texto completo>\n\n"
        f"RELATORIO:\n"
        f"- Busquei em: [arquivos/diretórios]\n"
        f"- Encontrei X resultados para [conceito]\n"
        f"- Não encontrei para [conceito]: [motivo]\n\n"
        f"————— RULES —————\n"
        f"- NEVER answer the user's question or give an opinion.\n"
        f"- NEVER describe, summarize, or paraphrase legal content.\n"
        f"- ALWAYS reproduce the FULL TEXT of every legal provision found.\n"
        f"- NEVER include grep line numbers, bash commands, tool headers, or JSON.\n"
        f"- NEVER include your reasoning, thinking process, or narrative like \"Vou analisar...\".\n"
        f"- Your output must contain ONLY the CONCEITOS, MATERIAL, and RELATORIO sections.\n"
        f"- If nothing is found, output only the RELATORIO section explaining what was searched.\n"
        f"- STOP searching once you have covered all extracted concepts."
    )
    if query.strip():
        prompt += f"\n\n===== USER MESSAGE =====\n{query}\n===== END MESSAGE ====="
    if extra:
        prompt += f"\n\nAdditional context: {extra}"
    return prompt


def _assemble_librarian_result(text_parts, tool_outputs):
    """Extract structured output from Pi agent response.

    Prefers the structured markers (CONCEITOS:/MATERIAL:/RELATORIO:)
    from the LLM's text output. Falls back to cleaning tool outputs
    if the LLM didn't produce structured output.
    """
    joined = "".join(tp for tp in text_parts if isinstance(tp, str)).strip()

    if joined:
        sections = _parse_sections(joined)
        if sections["material"] or sections["relatorio"]:
            lines = []
            if sections["conceitos"]:
                lines.append("=== CONCEITOS EXTRAÍDOS ===")
                lines.extend(sections["conceitos"])
                lines.append("")
            if sections["material"]:
                lines.append("=== MATERIAL ENCONTRADO ===")
                lines.extend(sections["material"])
                lines.append("")
            if sections["relatorio"]:
                lines.append("=== RELATÓRIO DE BUSCA ===")
                lines.extend(sections["relatorio"])
            return lines

    clean = _clean_tool_outputs(tool_outputs)
    if clean:
        result = ["=== MATERIAL ENCONTRADO ==="]
        result.extend(clean)
        return result

    return ["[Deep search] No structured content found. Check /tmp/kollzsh_debug.log for details."]


def _parse_sections(text):
    """Parse CONCEITOS:/MATERIAL:/RELATORIO: sections from text."""
    sections = {"conceitos": [], "material": [], "relatorio": []}
    current = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper().rstrip(":")
        if upper == "CONCEITOS":
            current = "conceitos"
            continue
        if upper == "MATERIAL":
            current = "material"
            continue
        if upper.startswith("RELATORIO"):
            current = "relatorio"
            continue

        if current:
            sections[current].append(stripped)

    for key in sections:
        while sections[key] and sections[key][-1] == "":
            sections[key].pop()
    return sections


def _clean_tool_outputs(tool_outputs):
    """Remove grep line numbers, tool headers, bash commands, TTL syntax.

    Keeps only readable text content from tool outputs.
    """
    cleaned = []
    for line in tool_outputs:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if stripped.startswith("--- [") or stripped.startswith("{") or stripped.startswith("}"):
            continue
        if stripped.startswith("total ") or stripped.startswith("("):
            continue
        if stripped.endswith(":"):
            continue
        no_prefix = stripped.split("- ", 1)[-1] if " - " in stripped else stripped
        no_prefix = no_prefix.split(":", 1)[-1] if ":" in no_prefix.split(" ", 1)[0] else no_prefix
        no_prefix = no_prefix.split(" - ", 1)[-1] if " - " in no_prefix else no_prefix
        if no_prefix.startswith("rb:") or no_prefix.startswith("rdfs:") or no_prefix.startswith("owl:"):
            continue
        if no_prefix.strip():
            cleaned.append(no_prefix.strip())
    return cleaned


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
    fontes_dir: str = "",
) -> List[str]:
    global _pi_proc
    try:
        proc = _ensure_pi_running(agent_dir, plugin_dir, url, model, context_level, event_callback)
    except RuntimeError as exc:
        if event_callback:
            event_callback("error", msg=str(exc))
        return [f"Pi setup error: {exc}"]

    extra = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
    librarian_query = _build_librarian_prompt(query, extra, fontes_dir)
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

    stderr_text = proc.stderr.read() if proc.stderr else ""
    if stderr_text:
        log_debug("Pi stderr:", stderr_text[:500])

    lines = _assemble_librarian_result(text_parts, tool_outputs)
    if event_callback:
        log_debug(f"Pi completed: {len(''.join(lines))} chars, {len(lines)} lines")

    return lines
