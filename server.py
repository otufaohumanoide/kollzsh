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

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        server_sock.bind(self.socket_path)
        server_sock.listen(5)
        server_sock.settimeout(1.0)
        log_debug(f"Listening on {self.socket_path}")

        try:
            while self.running:
                elapsed = time.time() - self.last_activity
                if elapsed > self.inactivity_timeout:
                    log_debug(f"Inactivity timeout ({self.inactivity_timeout}s), shutting down")
                    break

                try:
                    conn, _ = server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                try:
                    data = b""
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                        if b'\n' in data:
                            break

                    if data:
                        request_data = data.decode('utf-8').strip()
                        try:
                            request = json.loads(request_data)
                        except json.JSONDecodeError:
                            conn.close()
                            continue

                        if request.get("mode") == "deep":
                            self.handle_request_streaming(request, conn)
                        else:
                            response = self.handle_request(request)
                            conn.sendall((response + "\n").encode("utf-8"))
                except Exception as e:
                    log_debug(f"Error handling connection: {e}")
                finally:
                    conn.close()
        finally:
            server_sock.close()
            self.cleanup()
