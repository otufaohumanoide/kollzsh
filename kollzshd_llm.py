#!/usr/bin/env python3
"""kollzsh daemon LLM interaction module.

Handles prompt construction, API calls, and response parsing
for navigation mode (Ctrl+O) and deep search mode (Ctrl+G).
"""

import ast
import json
import logging
import os
import re
import urllib.request
import urllib.error

from kollzshd_commands import log_debug, parse_and_validate_commands

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

TOOL_DEFINITION = {
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


def build_navigation_prompt(cwd, query):
    """Build the prompt for navigation mode (Ctrl+O).

    Simple, one-shot: LLM generates commands, daemon executes, done.
    Returns the payload dict for the API call.
    """
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL')
    system_msg = (
        "You are a shell command generator. "
        "Return ONLY a JSON list of strings. No explanation."
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
    }


def build_deep_search_prompt(cwd, query, round_num, previous_output=None):
    """Build the prompt for deep search mode (Ctrl+G).

    Round 1: LLM generates commands
    Round 2 (if needed): LLM receives output and refines

    Returns the payload dict for the API call.
    """
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL')

    if round_num == 1:
        system_msg = (
            "You are a precise shell command generator for deep file search. "
            "Write concise, focused commands using grep, rg, find, cat, head, etc. "
            "Use pipes to limit output: head -30, tail -20."
        )
        user_msg = (
            f"You are in: {cwd}\n"
            f'User query: "{query}"\n\n'
            "You can run shell commands to explore and find information.\n"
            "Write precise commands using grep, rg, find, cat, head, etc.\n"
            "Use pipes to limit output: head -30, tail -20.\n\n"
            "If the output is insufficient, I will ask you to refine ONCE MORE.\n"
            "After that, the results go to the user.\n\n"
            "Return a JSON list of 1-2 commands:\n"
            '{"commands": ["...", "..."]}'
        )
    else:
        system_msg = (
            "You are analyzing command output to refine search results. "
            "Decide if the output is sufficient or if more commands are needed."
        )
        user_msg = (
            f'Command output (truncated):\n{previous_output}\n\n'
            f'Is this enough to answer "{query}"?\n'
            "If yes, return {\"done\": true, \"answer\": [\"relevant\", \"lines\"]}\n"
            "If no, return {\"done\": false, \"refine\": [\"more precise command\"]}"
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
    }

    if round_num == 1:
        payload["tools"] = [TOOL_DEFINITION]
        payload["tool_choice"] = "auto"

    return payload


def extract_commands(response_data):
    """Extract commands from LLM response.

    Tries tool_calls first, then falls back to content parsing.
    Returns list of command strings.
    """
    if not response_data:
        return []

    choices = response_data.get("choices", [])
    if not choices:
        return []

    message = choices[0].get("message", {})

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

    content = message.get("content", "")
    if content:
        log_debug("No tool calls, falling back to content parsing")
        return _parse_content_commands(content)

    return []


def _parse_content_commands(content):
    """Parse commands from raw content string."""
    markdown_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if markdown_match:
        content = markdown_match.group(1)

    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
        if isinstance(parsed, dict) and "commands" in parsed:
            return [str(c) for c in parsed["commands"] if isinstance(c, str)]
    except json.JSONDecodeError:
        pass

    try:
        parsed = ast.literal_eval(content)
        if isinstance(parsed, list):
            return [str(c) for c in parsed if isinstance(c, str)]
    except (ValueError, SyntaxError):
        pass

    validated = parse_and_validate_commands(content)
    return [cmd for cmd, is_safe, _reason in validated if is_safe]


def call_llm(payload, timeout=120):
    """Make HTTP request to LLM API endpoint.

    Returns response data dict, or None on error.
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
