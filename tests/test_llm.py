from kollzshd_llm import (
    build_navigation_prompt,
    extract_commands,
    _parse_content_commands,
)


class TestBuildNavigationPrompt:
    def test_includes_cwd(self):
        prompt = build_navigation_prompt("/home/user", "list files")
        msg = prompt["messages"][1]["content"]
        assert "/home/user" in msg

    def test_includes_query(self):
        prompt = build_navigation_prompt("/tmp", "find logs")
        msg = prompt["messages"][1]["content"]
        assert "find logs" in msg

    def test_has_tool_definition(self):
        prompt = build_navigation_prompt("/tmp", "test")
        assert "tools" in prompt
        assert len(prompt["tools"]) == 1
        assert prompt["tools"][0]["type"] == "function"

    def test_has_model(self):
        prompt = build_navigation_prompt("/tmp", "test")
        assert "model" in prompt
        assert isinstance(prompt["model"], str)

    def test_not_streaming(self):
        prompt = build_navigation_prompt("/tmp", "test")
        assert prompt.get("stream") is False

    def test_uses_env_model(self, monkeypatch):
        monkeypatch.setenv("KOLLZSH_MODEL", "test-model")
        prompt = build_navigation_prompt("/tmp", "test")
        assert prompt["model"] == "test-model"

    def test_injects_system_context(self, monkeypatch):
        monkeypatch.setenv("KOLLZSH_SYSTEM_CONTEXT", "Always use -la")
        prompt = build_navigation_prompt("/tmp", "test")
        sys_msg = prompt["messages"][0]["content"]
        assert "Always use -la" in sys_msg

    def test_empty_system_context_omitted(self):
        prompt = build_navigation_prompt("/tmp", "test")
        sys_msg = prompt["messages"][0]["content"]
        assert "User examples" not in sys_msg


class TestParseContentCommands:
    def test_json_list(self):
        commands = _parse_content_commands('["ls", "pwd"]')
        assert commands == ["ls", "pwd"]

    def test_json_dict_with_commands_key(self):
        commands = _parse_content_commands('{"commands": ["ls", "pwd"]}')
        assert commands == ["ls", "pwd"]

    def test_markdown_json_fence(self):
        content = "```json\n[\"ls\", \"pwd\"]\n```"
        commands = _parse_content_commands(content)
        assert commands == ["ls", "pwd"]

    def test_python_literal_list(self):
        commands = _parse_content_commands("['ls', 'pwd']")
        assert commands == ["ls", "pwd"]

    def test_line_by_line(self):
        commands = _parse_content_commands("ls -la\npwd")
        assert commands == ["ls -la", "pwd"]

    def test_empty_string(self):
        assert _parse_content_commands("") == []

    def test_returns_all_commands_from_json(self):
        commands = _parse_content_commands('["ls", "rm -rf /"]')
        assert commands == ["ls", "rm -rf /"]

    def test_invalid_json_returns_empty(self):
        commands = _parse_content_commands("not valid at all")
        assert isinstance(commands, list)


class TestExtractCommands:
    def test_returns_empty_for_none_response(self):
        assert extract_commands(None) == []

    def test_returns_empty_for_empty_response(self):
        assert extract_commands({}) == []

    def test_returns_empty_for_no_choices(self):
        assert extract_commands({"choices": []}) == []

    def test_extracts_from_tool_calls(self, llm_response_data):
        commands = extract_commands(llm_response_data)
        assert commands == ["ls -la", "pwd"]

    def test_extracts_from_content_fallback(self):
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '["ls", "pwd"]',
                    }
                }
            ]
        }
        commands = extract_commands(response)
        assert commands == ["ls", "pwd"]
