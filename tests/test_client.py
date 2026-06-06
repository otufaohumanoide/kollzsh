import json

from kollzshd_client import _render_event


class TestRenderEvent:
    def test_think_start(self):
        event = {"type": "think", "status": "start", "msg": "Searching...", "round": 1}
        result = _render_event(event)
        assert "Round" in result
        assert "Searching" in result

    def test_think_start_no_round(self):
        event = {"type": "think", "status": "start", "msg": "Thinking..."}
        result = _render_event(event)
        assert "Thinking" in result

    def test_error_event(self):
        event = {"type": "error", "msg": "LLM call failed"}
        result = _render_event(event)
        assert "LLM call failed" in result

    def test_unknown_event_type(self):
        event = {"type": "unknown_type"}
        result = _render_event(event)
        assert result == ""

    def test_empty_event(self):
        assert _render_event({}) == ""


class TestParseLines:
    def test_valid_json(self, monkeypatch, capsys):
        import sys
        data = json.dumps({"lines": ["line1", "line2", "line3"]})
        monkeypatch.setattr(sys, "stdin", type("StdinMock", (), {"read": lambda self: data})())
        from kollzshd_client import _parse_lines
        _parse_lines()
        captured = capsys.readouterr()
        assert captured.out.strip() == "line1\nline2\nline3"

    def test_empty_lines(self, monkeypatch, capsys):
        import sys
        data = json.dumps({"lines": []})
        monkeypatch.setattr(sys, "stdin", type("StdinMock", (), {"read": lambda self: data})())
        from kollzshd_client import _parse_lines
        _parse_lines()
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_invalid_json(self, monkeypatch, capsys):
        import sys
        monkeypatch.setattr(sys, "stdin", type("StdinMock", (), {"read": lambda self: "invalid json"})())
        from kollzshd_client import _parse_lines
        _parse_lines()
        captured = capsys.readouterr()
        assert captured.out == ""
