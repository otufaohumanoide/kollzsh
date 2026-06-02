#!/usr/bin/env python3
import logging
import sys
import os
import json
import urllib.request
import urllib.error

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def log_debug(message, data=None):
    if data:
        logging.debug(f"{message}\nData: {data}\n----------------------------------------")
    else:
        logging.debug(message)

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

def interact_with_llm(user_query):
    llm_url = os.getenv('KOLLZSH_URL', 'http://localhost:8080').rstrip('/')
    model = os.getenv('KOLLZSH_MODEL', 'unsloth/Qwen3.5-4B-GGUF:UD-Q8_K_XL')
    chat_url = f"{llm_url}/v1/chat/completions"

    formatted_query = f"Generate shell commands for the following task: {user_query}. Provide multiple relevant commands if available."

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": formatted_query}],
        "tools": [TOOL_DEFINITION],
        "tool_choice": "auto",
        "stream": False
    }

    log_debug("Sending request to:", chat_url)
    log_debug("Payload:", payload)

    try:
        req = urllib.request.Request(
            chat_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_data = json.loads(resp.read().decode('utf-8'))

        log_debug("Received response:", response_data)

        message = response_data.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls")

        if tool_calls:
            for tc in tool_calls:
                if tc.get("type") == "function" and tc["function"]["name"] == "get_shell_commands":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        commands = args.get("commands", [])
                        if commands:
                            log_debug("Successfully extracted commands via tool call:", commands)
                            return commands
                    except (json.JSONDecodeError, KeyError) as e:
                        log_debug(f"Error parsing tool call arguments: {e}")

        content = message.get("content", "")
        if content:
            log_debug("No tool calls found, falling back to content parsing")
            return parse_commands(content)

        log_debug("No valid commands found in response")
        return []

    except urllib.error.HTTPError as e:
        log_debug(f"HTTP error: {e.code} {e.reason}", e.read().decode('utf-8', errors='replace'))
        return []
    except urllib.error.URLError as e:
        log_debug(f"Connection error: {e.reason}")
        return []
    except Exception as e:
        log_debug(f"Error interacting with LLM: {str(e)}")
        return []

def parse_commands(content):
    try:
        import re
        markdown_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if markdown_match:
            content = markdown_match.group(1)

        content = normalize_json_string(content)
        log_debug("Normalized content:", content)

        try:
            commands = json.loads(content)
        except json.JSONDecodeError:
            import ast
            commands = ast.literal_eval(content)

        if isinstance(commands, list):
            cleaned_commands = []
            for cmd in commands:
                if isinstance(cmd, str):
                    cmd = cmd.replace('\\"', '"')
                    cmd = cmd.replace('\\\\', '\\')
                    cmd = cmd.replace('"', '\\"')
                    cleaned_commands.append(cmd)

            log_debug("Successfully parsed commands:", cleaned_commands)
            return cleaned_commands

        log_debug("Parsed content is not a list:", commands)
        return []

    except Exception as e:
        log_debug(f"Error parsing commands: {str(e)}", content)
        return []

def normalize_json_string(content):
    content = content.replace('\n', ' ')
    content = content.replace('\r', ' ')
    content = content.replace('\t', ' ')

    content = content.replace('\\"', '"')
    content = content.replace('\\\\', '\\')
    content = content.replace('"', '\\"')

    content = ' '.join(content.split())

    log_debug("Normalized JSON string:", content)
    return content

if __name__ == '__main__':
    if len(sys.argv) != 2:
        log_debug("Usage: llm_util.py <user_query>")
        sys.exit(1)

    user_query = sys.argv[1]
    commands = interact_with_llm(user_query)

    if not commands:
        log_debug("No valid commands found")
        sys.exit(1)

    for cmd in commands:
        print(cmd)

    log_debug("Successfully output commands")
