#!/usr/bin/env python3
"""Módulo de interação com LLM para o daemon kollzsh.

Responsável por:
- Construir prompt para o modo navegação
- Realizar chamadas HTTP à API OpenAI-compatible do servidor LLM
- Extrair comandos de respostas da LLM (tool_calls ou parsing de conteúdo)

O daemon usa este módulo para comunicar com a LLM. O módulo NÃO executa
comandos — isso fica a cargo de ``kollzshd_commands.py``.
"""

import ast
import json
import os
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from kollzshd_commands import parse_and_validate_commands
from kollzshd_logging import log_debug

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
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL')
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
        "max_tokens": 8192,
    }


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

    for attempt in range(3):
        log_debug(f"LLM request to: {chat_url} (attempt {attempt + 1}/3)")
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
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
        except urllib.error.URLError as e:
            log_debug(f"Connection error: {e.reason}")
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            log_debug(f"Error calling LLM: {str(e)}")
            return None
