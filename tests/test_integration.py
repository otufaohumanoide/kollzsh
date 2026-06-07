import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

import pytest


class TestDaemonSocketProtocol:
    """Test the Unix socket protocol between client and server."""

    @pytest.fixture
    def sock_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield os.path.join(tmpdir, "test.sock")

    @pytest.fixture
    def mock_server(self, sock_path):
        """A minimal server that mimics DaemonServer's socket I/O."""
        from server import DaemonServer

        server = DaemonServer(
            socket_path=sock_path,
            pid_file="/tmp/_test_kollzshd.pid",
            inactivity_timeout=2,
        )
        with patch("server.signal.signal"), \
             patch("agent_router.run_pi_query", return_value=["mock result"]), \
             patch("agent_router.call_llm", return_value=None):
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            # Retry loop: wait up to 3 seconds for socket to be ready
            _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            for _ in range(30):
                try:
                    _sock.connect(sock_path)
                    _sock.close()
                    break
                except (ConnectionRefusedError, FileNotFoundError):
                    _sock.close()
                    _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    time.sleep(0.1)
            else:
                raise RuntimeError("Server did not become ready within 3 seconds")
            yield server
            server.running = False
            thread.join(timeout=2)
            try:
                os.unlink(sock_path)
            except OSError:
                pass
            try:
                os.unlink("/tmp/_test_kollzshd.pid")
            except OSError:
                pass

    def test_connect_and_send_navigation(self, sock_path, mock_server):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        sock.settimeout(5)
        payload = json.dumps({"query": "list files", "mode": "navigation"})
        sock.sendall(payload.encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        response = json.loads(data.decode())
        assert "lines" in response
        assert isinstance(response["lines"], list)

    def test_connect_and_send_deep(self, sock_path, mock_server):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        sock.settimeout(5)
        payload = json.dumps({"query": "search code", "mode": "deep"})
        sock.sendall(payload.encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        response = json.loads(data.decode())
        assert "lines" in response

    def test_send_query_client_no_socket(self):
        from kollzshd_client import _send_query
        with pytest.raises(FileNotFoundError):
            _send_query("/tmp/_nonexistent_test.sock", "test", "navigation")

    def test_render_event_think_start(self):
        from kollzshd_client import _render_event
        event = {"type": "think", "status": "start", "msg": "analyzing"}
        result = _render_event(event)
        assert "analyzing" in result
        assert "THINK" in result

    def test_render_event_error(self):
        from kollzshd_client import _render_event
        event = {"type": "error", "msg": "something failed"}
        result = _render_event(event)
        assert "something failed" in result

    def test_render_event_unknown_type(self):
        from kollzshd_client import _render_event
        event = {"type": "unknown_thing"}
        result = _render_event(event)
        assert result == ""

    def test_parse_lines_basic(self):
        from kollzshd_client import _parse_lines
        import io
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"lines": ["line1", "line2"]}')
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _parse_lines()
        finally:
            sys.stdout = old_stdout
            sys.stdin = old_stdin
        output = captured.getvalue()
        assert "line1" in output
        assert "line2" in output

    def test_parse_lines_empty(self):
        from kollzshd_client import _parse_lines
        import io
        import sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"lines": []}')
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            _parse_lines()
        finally:
            sys.stdout = old_stdout
            sys.stdin = old_stdin
        assert captured.getvalue() == ""

