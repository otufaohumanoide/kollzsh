#!/usr/bin/env python3
"""Módulo de interação com LLM para o daemon kollzsh.

Responsável por:
- Construir prompts para os dois modos de operação (navegação e busca profunda)
- Realizar chamadas HTTP à API OpenAI-compatible do servidor LLM
- Extrair comandos de respostas da LLM (tool_calls ou parsing de conteúdo)

O daemon usa este módulo para comunicar com a LLM. O módulo NÃO executa
comandos — isso fica a cargo de ``kollzshd_commands.py``.
"""

import ast
import json
import logging
import os
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from kollzshd_commands import log_debug, parse_and_validate_commands

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Definição da tool/function que a LLM pode usar para retornar comandos estruturados
TOOL_DEFINITION: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_shell_commands",
        "description": "Generate multiple relevant shell commands for a given task",
        "parameters": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of shell commands to execute"
                }
            },
            "required": ["commands"]
        }
    }
}


def _get_system_context() -> str:
    """Retorna conteudo extra da env KOLLZSH_SYSTEM_CONTEXT para in-context learning.
    
    O usuario pode definir exemplos ou instrucoes que serao injetadas
    no system prompt da LLM. Exemplo no ~/.zshrc:
      export KOLLZSH_SYSTEM_CONTEXT="Sempre use aspas simples nos comandos.
    Exemplo correto: grep -r 'termo' .
    Exemplo ERRADO: grep -r \\"termo\\" ."
    """
    return os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()


def build_navigation_prompt(cwd: str, query: str) -> Dict[str, Any]:
    """Constrói o payload da API para o modo navegação (Ctrl+O).

    Modo simples de round único: a LLM gera comandos, o daemon executa,
    e o resultado vai direto para o fzf. Usa tool_calling para garantir
    que a LLM retorne JSON estruturado.

    Args:
        cwd: Diretório de trabalho atual do daemon.
        query: Consulta do usuário (buffer do terminal).

    Returns:
        Dict com payload completo para POST /v1/chat/completions.
    """
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-GGUF:UD-Q6_K_XL')
    extra = _get_system_context()
    system_msg = (
        "You are a shell command generator. "
        "Return ONLY a JSON list of strings. No explanation."
        + (f"\n\nUser examples/instructions:\n{extra}" if extra else "")
    )
    user_msg = (
        f"You are in: {cwd}\n"
        f'User query: "{query}"\n'
        "Generate 1-3 shell commands to explore, navigate, or answer this.\n"
        "Return ONLY a JSON list of strings. No explanation."
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "tools": [TOOL_DEFINITION],
        "tool_choice": "auto",
        "stream": False,
        "max_tokens": 60000,
    }


def build_deep_search_prompt(
    cwd: str,
    query: str,
    round_num: int,
    previous_output: Optional[str] = None
) -> Dict[str, Any]:
    """Constrói o payload da API para o modo busca profunda (Ctrl+G).

    Round 1: LLM gera comandos de busca (com tool_calling).
    Round 2: LLM recebe o output executado e decide se precisa refinar.

    No round 2, NÃO usamos tool_calling — a LLM retorna JSON puro
    com ``done`` (True/False) e ``answer``/``refine``.

    Args:
        cwd: Diretório de trabalho atual do daemon.
        query: Consulta do usuário.
        round_num: Número do round (1 ou 2).
        previous_output: Output do round 1 (necessário apenas no round 2).

    Returns:
        Dict com payload completo para POST /v1/chat/completions.
    """
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-GGUF:UD-Q6_K_XL')

    extra = _get_system_context()
    if round_num == 1:
        system_msg = (
            "You search LOCAL FILES to answer user questions. Rules:\n"
            "1. Use grep -ril or rg -il to find relevant files\n"
            "2. Search inside .txt AND .md files\n"
            "3. The daemon will auto-read any .txt/.md files found\n"
            "4. Return simple, correct commands — no double escaping\n"
            "EXAMPLES:\n"
            "  grep -ril 'principio da alternatividade' . --include='*.txt' --include='*.md'\n"
            "  rg -il 'trafico' . -g '*.txt' -g '*.md'\n"
            + (f"\nUser instructions:\n{extra}" if extra else "")
        )
        user_msg = (
            f"Directory: {cwd}\n"
            f'User question: "{query}"\n\n'
            "Find files relevant to this question.\n"
            "Return a JSON list of 1-3 search commands:\n"
            '{"commands": ["search command 1", "search command 2"]}'
        )
    else:
        system_msg = (
            "You received file contents to answer a user question.\n"
            "Analyze the content and answer clearly.\n"
            "For each file, briefly note what it contains.\n"
            "If content is insufficient, ask for more specific search.\n"
            "Return JSON with done=true and answer, or done=false and refine."
            + (f"\nUser instructions:\n{extra}" if extra else "")
        )
        user_msg = (
            f'File contents found:\n{previous_output}\n\n'
            f'Answer this question: "{query}"\n\n'
            "If you can answer: {\"done\": true, \"answer\": [\"line1\", \"line2\", ...]}\n"
            "If you need more info: {\"done\": false, \"refine\": [\"more commands\"]}"
        )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "max_tokens": 60000,
    }

    # Round 1 usa tool_calling para extrair comandos estruturados
    if round_num == 1:
        payload["tools"] = [TOOL_DEFINITION]
        payload["tool_choice"] = "auto"

    return payload


def extract_commands(response_data: Dict[str, Any]) -> List[str]:
    """Extrai comandos da resposta da LLM.

    Tenta extrair via tool_calls primeiro (formato estruturado preferido).
    Se não houver tool_calls, faz parsing do campo content como fallback.

    Args:
        response_data: Resposta JSON da API /v1/chat/completions.

    Returns:
        Lista de strings de comandos extraídos (vazia se nenhum encontrado).
    """
    if not response_data:
        return []

    choices = response_data.get("choices", [])
    if not choices:
        return []

    message = choices[0].get("message", {})

    # Tenta extrair de tool_calls (formato preferido)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            if tc.get("type") == "function" and tc["function"]["name"] == "get_shell_commands":
                try:
                    args = json.loads(tc["function"]["arguments"])
                    commands = args.get("commands", [])
                    if commands:
                        log_debug("Extracted commands via tool call:", commands)
                        return [str(c) for c in commands]
                except (json.JSONDecodeError, KeyError) as e:
                    log_debug(f"Error parsing tool call arguments: {e}")

    # Fallback: parse do campo content (resposta em texto livre)
    content = message.get("content", "")
    if content:
        log_debug("No tool calls, falling back to content parsing")
        return _parse_content_commands(content)

    return []


def _parse_content_commands(content: str) -> List[str]:
    """Parseia comandos de uma string de conteúdo cru.

    Tenta, em ordem:
    1. Extrair de code fence markdown (````json ... ``` ```)
    2. Parsear como JSON (lista ou dict com chave "commands")
    3. Parsear como Python literal (ast.literal_eval)
    4. Parse linha a linha via ``parse_and_validate_commands``

    Args:
        content: String contendo comandos em formato variado.

    Returns:
        Lista de strings de comandos validados como seguros.
    """
    # Tenta extrair de code fence markdown
    markdown_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if markdown_match:
        content = markdown_match.group(1)

    # Tenta JSON direto
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
        if isinstance(parsed, dict) and "commands" in parsed:
            return [str(c) for c in parsed["commands"] if isinstance(c, str)]
    except json.JSONDecodeError:
        pass

    # Tenta Python literal
    try:
        parsed = ast.literal_eval(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
    except (ValueError, SyntaxError):
        pass

    # Último recurso: parse linha a linha
    validated = parse_and_validate_commands(content)
    return [cmd for cmd, is_safe, _reason in validated if is_safe]


def call_llm(payload: Dict[str, Any], timeout: int = 120) -> Optional[Dict[str, Any]]:
    """Realiza chamada HTTP POST ao endpoint /v1/chat/completions.

    Args:
        payload: Corpo da requisição JSON (model, messages, tools, etc).
        timeout: Timeout em segundos para a requisição HTTP.

    Returns:
        Dict com a resposta da API, ou None em caso de erro.
    """
    llm_url = os.getenv('KOLLZSH_URL', 'http://localhost:8080').rstrip('/')
    chat_url = f"{llm_url}/v1/chat/completions"

    log_debug("LLM request to:", chat_url)
    log_debug("Payload:", payload)

    try:
        req = urllib.request.Request(
            chat_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_data = json.loads(resp.read().decode('utf-8'))

        log_debug("LLM response:", response_data)
        return response_data

    except urllib.error.HTTPError as e:
        log_debug(f"HTTP error: {e.code} {e.reason}", e.read().decode('utf-8', errors='replace'))
        return None
    except urllib.error.URLError as e:
        log_debug(f"Connection error: {e.reason}")
        return None
    except Exception as e:
        log_debug(f"Error calling LLM: {str(e)}")
        return None
