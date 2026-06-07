#!/usr/bin/env python3
"""LLM interaction module for the kollzsh daemon.

Responsible for:
- Building prompts for navigation mode
- Making HTTP calls to the OpenAI-compatible LLM API
- Extracting commands from LLM responses (tool_calls or content parsing)

The daemon uses this module to communicate with the LLM. It does NOT execute
commands — that is handled by ``kollzshd_commands.py``.
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

# Tool/function definition the LLM can use to return structured commands
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
    """Return extra context from KOLLZSH_SYSTEM_CONTEXT env for in-context learning.

    The user can define examples or instructions that will be injected
    into the LLM system prompt. Example in ~/.zshrc:
      export KOLLZSH_SYSTEM_CONTEXT="Always use single quotes in commands.
    Correct example: grep -r 'term' .
    WRONG example: grep -r \"term\" ."
    """
    return os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()


def build_navigation_prompt(cwd: str, query: str) -> Dict[str, Any]:
    """Build the API payload for navigation mode (Ctrl+O).

    Simple single-round mode: the LLM generates commands, the daemon executes them,
    and the result goes straight to fzf. Uses tool_calling to ensure
    the LLM returns structured JSON.

    Args:
        cwd: Current working directory of the daemon.
        query: User query (terminal buffer).

    Returns:
        Dict with full payload for POST /v1/chat/completions.
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
    """Extract commands from an LLM response.

    Tries tool_calls first (preferred structured format).
    If no tool_calls, parses the content field as fallback.

    Args:
        response_data: JSON response from the /v1/chat/completions API.

    Returns:
        List of extracted command strings (empty if none found).
    """
    if not response_data:
        return []

    choices = response_data.get("choices", [])
    if not choices:
        return []

    message = choices[0].get("message", {})

    # Try tool_calls first (preferred format)
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

    # Fallback: parse content field (free-text response)
    content = message.get("content", "")
    if content:
        log_debug("No tool calls, falling back to content parsing")
        return _parse_content_commands(content)

    return []


def _parse_content_commands(content: str) -> List[str]:
    """Parse commands from a raw content string.

    Tries, in order:
    1. Extract from markdown code fence (````json ... ``` ```)
    2. Parse as JSON (list or dict with "commands" key)
    3. Parse as Python literal (ast.literal_eval)
    4. Line-by-line parse via ``parse_and_validate_commands``

    Args:
        content: String containing commands in various formats.

    Returns:
        List of command strings validated as safe.
    """
    # Try extracting from markdown code fence
    markdown_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if markdown_match:
        content = markdown_match.group(1)

    # Try direct JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
        if isinstance(parsed, dict) and "commands" in parsed:
            return [str(c) for c in parsed["commands"] if isinstance(c, str)]
    except json.JSONDecodeError:
        pass

    # Try Python literal
    try:
        parsed = ast.literal_eval(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
    except (ValueError, SyntaxError):
        pass

    # Last resort: line-by-line parse
    validated = parse_and_validate_commands(content)
    return [cmd for cmd, is_safe, _reason in validated if is_safe]


def call_llm(payload: Dict[str, Any], timeout: int = 120) -> Optional[Dict[str, Any]]:
    """Make HTTP POST request to the /v1/chat/completions endpoint.

    Args:
        payload: JSON request body (model, messages, tools, etc).
        timeout: Timeout in seconds for the HTTP request.

    Returns:
        Dict with the API response, or None on error.
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
