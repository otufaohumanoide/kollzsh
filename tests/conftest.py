import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def sample_commands():
    return ["ls -la", "pwd", "grep -r 'foo' .", "find . -name '*.py'"]


@pytest.fixture
def llm_response_data():
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "get_shell_commands",
                                "arguments": '{"commands": ["ls -la", "pwd"]}',
                            },
                        }
                    ],
                }
            }
        ]
    }
