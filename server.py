import json
import os
import signal
import socket
import time
from typing import Any, Dict

from kollzshd_logging import log_debug
from shell_manager import ShellManager
from agent_router import AgentRouter


class DaemonServer:
    def __init__(
        self,
        socket_path: str = "/tmp/kollzshd.sock",
        pid_file: str = "/tmp/kollzshd.pid",
        inactivity_timeout: int = 1800,
    ) -> None:
        self.socket_path = socket_path
        self.pid_file = pid_file
        self.inactivity_timeout = inactivity_timeout
        self.last_activity: float = time.time()
        self.running: bool = False
        self.server: socket.socket | None = None
        self.shell = ShellManager()
        self.router = AgentRouter(self.shell)

    def _send_event(self, conn: socket.socket, type_name: str, **kwargs: Any) -> None:
        event: Dict[str, object] = {"type": type_name}
        event.update(kwargs)
        try:
            conn.sendall((json.dumps(event) + "\n").encode())
        except Exception:
            pass

    def handle_request_streaming(self, request: dict, conn: socket.socket) -> None:
        self.last_activity = time.time()
        log_debug("Received request (streaming):", str(request))
        query = request.get("query", "")

        if not query:
            self._send_event(conn, "error", msg="Empty query")
            self._send_event(conn, "done", lines=["Error: empty query"], cwd=self.shell.cwd)
            return

        if not self.shell.is_alive:
            self.shell.start_shell()

        def send_event(type_name: str, **kwargs: Any) -> None:
            self._send_event(conn, type_name, **kwargs)

        lines = self.router.run_agent_loop(query, "deep", event_sender=send_event)
        self._send_event(conn, "done", lines=lines, cwd=self.shell.cwd)

    def handle_request(self, request: dict) -> str:
        self.last_activity = time.time()
        log_debug("Received request:", str(request))
        query = request.get("query", "")

        if not query:
            return json.dumps({"lines": ["Error: empty query"], "cwd": self.shell.cwd})

        if not self.shell.is_alive:
            self.shell.start_shell()

        lines = self.router.run_agent_loop(query, "navigation")
        result = {"lines": lines, "cwd": self.shell.cwd}
        log_debug("Response:", result)
        return json.dumps(result)

    def _check_inactivity(self) -> None:
        elapsed = time.time() - self.last_activity
        if elapsed > self.inactivity_timeout:
            log_debug(f"Inactivity timeout ({self.inactivity_timeout}s), shutting down")
            self.running = False

    def _accept_loop(self) -> None:
        while self.running:
            try:
                conn, addr = self.server.accept()
            except socket.timeout:
                self._check_inactivity()
                continue
            except OSError as exc:
                log_debug(f"Accept error: {exc}")
                continue
            self._handle_client(conn, str(addr))

    def _handle_client(self, conn: socket.socket, addr: str) -> None:
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            raw = data.decode().strip()
            if not raw:
                return
            request = json.loads(raw)
            query = request.get("query", "")
            mode = request.get("mode", "navigation")
            log_debug(f"Request from {addr}: mode={mode}, query={query}")
            if mode == "deep":
                self.handle_request_streaming(request, conn)
                return
            result = self.router.run_agent_loop(query, mode)
            response = json.dumps({"lines": result, "cwd": self.shell.cwd})
            try:
                conn.sendall(response.encode())
            except (BrokenPipeError, OSError) as exc:
                log_debug(f"Send error to {addr}: {exc}")
        except (json.JSONDecodeError, Exception) as exc:
            log_debug(f"Error handling client {addr}: {exc}")
            try:
                conn.sendall(json.dumps({"error": str(exc)}).encode())
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def cleanup(self) -> None:
        log_debug("Cleaning up daemon")
        self.running = False
        self.shell.stop_shell()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        try:
            os.unlink(self.pid_file)
        except OSError:
            pass

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda s, f: self.cleanup())
        signal.signal(signal.SIGINT, lambda s, f: self.cleanup())

        self.shell.start_shell()
        self.running = True

        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        self.server.bind(self.socket_path)
        self.server.listen(5)
        self.server.settimeout(1.0)
        log_debug(f"Listening on {self.socket_path}")

        try:
            self._accept_loop()
        finally:
            self.server.close()
            self.cleanup()
