from kollzshd_commands import (
    validate_command_safety,
    truncate_output,
    parse_and_validate_commands,
)


class TestValidateCommandSafety:
    def test_rejects_empty_string(self):
        is_safe, reason = validate_command_safety("")
        assert not is_safe
        assert "Empty" in reason

    def test_rejects_whitespace(self):
        is_safe, reason = validate_command_safety("   ")
        assert not is_safe

    def test_rejects_heredoc(self):
        is_safe, reason = validate_command_safety("cat << EOF")
        assert not is_safe

    def test_rejects_rm_rf_root(self):
        is_safe, reason = validate_command_safety("rm -rf /")
        assert not is_safe

    def test_rejects_rm_pipeline(self):
        is_safe, reason = validate_command_safety("echo x | rm -rf /")
        assert not is_safe

    def test_rejects_destructive_first_token(self):
        for cmd in ["rm", "mv", "cp", "sudo", "kill"]:
            is_safe, _ = validate_command_safety(f"{cmd} some_file")
            assert not is_safe, f"{cmd} should be blocked"

    def test_rejects_pipeline_with_destructive(self):
        is_safe, reason = validate_command_safety("echo hello | rm file")
        assert not is_safe
        assert "pipeline" in reason

    def test_allows_readonly_commands(self):
        for cmd in ["ls", "pwd", "grep", "cat", "find", "echo", "date"]:
            is_safe, _ = validate_command_safety(f"{cmd} --help")
            assert is_safe, f"{cmd} should be allowed"

    def test_rejects_rm_with_conjunction(self):
        is_safe, reason = validate_command_safety("ls && rm -rf /tmp/test")
        assert not is_safe
        assert "pattern" in reason

    def test_rejects_block_device_redirect(self):
        is_safe, reason = validate_command_safety("cat file > /dev/sda")
        assert not is_safe

    def test_allows_path_with_slash(self):
        is_safe, reason = validate_command_safety("/usr/bin/ls -la")
        assert is_safe

    def test_rejects_sudo_in_pipeline(self):
        is_safe, reason = validate_command_safety("echo x | sudo rm -rf /")
        assert not is_safe

    def test_rejects_cp_with_dangerous_flag_none(self):
        is_safe, _ = validate_command_safety("cp file1 file2")
        assert not is_safe  # 'cp' is in DESTRUCTIVE_COMMANDS

    def test_safety_never_blocks_echo(self):
        for variant in [
            "echo hello",
            "echo '__KSEP__'",
            "echo '__KEND__'",
            "echo test > /tmp/out",
        ]:
            is_safe, reason = validate_command_safety(variant)
            assert is_safe, f"echo variant failed: {variant} ({reason})"


class TestTruncateOutput:
    def test_short_list_not_truncated(self):
        lines = ["a", "b", "c"]
        assert truncate_output(lines, max_lines=5) == lines

    def test_long_list_truncated(self):
        lines = [str(i) for i in range(20)]
        result = truncate_output(lines, max_lines=10)
        assert len(result) == 11  # 5 top + 1 omitted + 5 bottom
        assert result[0] == "0"
        assert result[5] == "... (10 lines omitted) ..."
        assert result[-1] == "19"

    def test_empty_list(self):
        assert truncate_output([], max_lines=10) == []

    def test_exact_max_not_truncated(self):
        lines = [str(i) for i in range(5)]
        assert truncate_output(lines, max_lines=5) == lines

    def test_odd_max_lines(self):
        lines = [str(i) for i in range(10)]
        result = truncate_output(lines, max_lines=7)
        assert len(result) == 7  # half=3, 3 top + 1 marker + 3 bottom
        assert "... (4 lines omitted) ..." in result

    def test_max_lines_less_than_2(self):
        lines = [str(i) for i in range(5)]
        result = truncate_output(lines, max_lines=1)
        assert len(result) == 3  # half=1, 1 top + marker + 1 bottom

    def test_single_line_not_truncated(self):
        assert truncate_output(["only line"], max_lines=10) == ["only line"]


class TestParseAndValidateCommands:
    def test_json_array(self):
        results = parse_and_validate_commands('["ls", "pwd"]')
        assert len(results) == 2
        assert all(isinstance(c, str) for c, _, _ in results)

    def test_python_literal(self):
        results = parse_and_validate_commands("['ls', 'pwd']")
        assert len(results) == 2

    def test_line_by_line_fallback(self):
        results = parse_and_validate_commands("ls -la\npwd")
        assert len(results) == 2

    def test_destructive_commands_marked_unsafe_in_parse(self):
        results = parse_and_validate_commands('["ls", "rm -rf /"]')
        assert len(results) == 2
        safe_commands = [c for c, s, _ in results if s]
        assert len(safe_commands) == 1
        assert safe_commands[0] == "ls"

    def test_empty_content(self):
        results = parse_and_validate_commands("")
        assert results == []

    def test_comments_ignored(self):
        results = parse_and_validate_commands("ls -la\n# this is a comment\npwd")
        assert len(results) == 2
