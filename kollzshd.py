#!/usr/bin/env python3
"""kollzsh daemon — persistent shell + LLM agent for filesystem search."""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time

from kollzshd_commands import (
    execute_command, truncate_output, log_debug
)
from kollzshd_llm import (
    build_navigation_prompt, build_deep_search_prompt,
    extract_commands, call_llm
)

SOCKET_PATH = "/tmp/kollzshd.sock"
PID_FILE = "/tmp/kollzshd.pid"
MAX_ROUNDS = 2
INACTIVITY_TIMEOUT = 1800  # 30 minutes

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class KollzshDaemon:
    def __init__(self):
        self.cwd = os.getcwd()
        self.history = []
        self.shell_proc = None
        self.last_activity = time.time()
        self.running = False

    def start_shell(self):
        """Start persistent bash subprocess."""
        log_debug("Starting shell subprocess")
        self.shell_proc = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        log_debug(f"Shell started, PID={self.shell_proc.pid}")

    def stop_shell(self):
        """Stop persistent bash subprocess."""
        if self.shell_proc and self.shell_proc.poll() is None:
            log_debug("Stopping shell subprocess")
            try:
                self.shell_proc.stdin.close()
                self.shell_proc.wait(timeout=5)
            except Exception as e:
                log_debug(f"Error stopping shell: {e}")
                try:
                    self.shell_proc.kill()
                except Exception:
                    pass
        self.shell_proc = None

    def update_cwd(self, new_cwd):
        """Update CWD and maintain history."""
        if new_cwd and new_cwd != self.cwd:
            self.history.append(self.cwd)
            if len(self.history) > 10:
                self.history = self.history[-10:]
            self.cwd = new_cwd
            log_debug(f"CWD changed: {self.cwd}")

    def run_agent_loop(self, query, mode="navigation"):
        """Run the LLM agent loop.

        For navigation mode: 1 round (generate commands -> execute -> return output)
        For deep mode: up to 2 rounds (generate -> execute -> evaluate -> maybe refine)
        """
        log_debug(f"Agent loop: mode={mode}, query={query}")
        all_output = []
        cwd = self.cwd

        max_rounds = 1 if mode == "navigation" else MAX_ROUNDS

        for round_num in range(1, max_rounds + 1):
            log_debug(f"Round {round_num}")

            if mode == "navigation" or round_num == 1:
                payload = build_navigation_prompt(cwd, query)
            else:
                payload = build_deep_search_prompt(cwd, query, round_num, '\n'.join(all_output))

            response_data = call_llm(payload)
            if not response_data:
                log_debug("LLM call failed")
                all_output.append("Error: LLM call failed")
                break

            if mode == "deep" and round_num == 2:
                content = ""
                choices = response_data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")

                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        if parsed.get("done") is True:
                            answer = parsed.get("answer", [])
                            if isinstance(answer, list):
                                all_output.extend([str(a) for a in answer])
                            else:
                                all_output.append(str(answer))
                            break
                        elif parsed.get("done") is False:
                            refine = parsed.get("refine", [])
                            if refine and isinstance(refine, list):
                                commands = [str(c) for c in refine]
                            else:
                                break
                        else:
                            break
                    else:
                        break
                except (json.JSONDecodeError, ValueError):
                    break
            else:
                commands = extract_commands(response_data)

            if not commands:
                log_debug("No commands extracted")
                if round_num == 1:
                    all_output.append("No relevant commands found")
                break

            for cmd in commands:
                success, output, new_cwd = execute_command(cmd, self.shell_proc)
                if new_cwd:
                    self.update_cwd(new_cwd)
                    cwd = self.cwd
                if output:
                    lines = output.strip().split('\n')
                    all_output.extend(lines)

            if mode == "navigation":
                break

        return truncate_output(all_output)

    def handle_request(self, request_data):
        """Handle a single request from ZSH."""
        self.last_activity = time.time()
        log_debug("Received request:", request_data)

        try:
            request = json.loads(request_data)
        except json.JSONDecodeError as e:
            log_debug(f"Invalid JSON: {e}")
            return json.dumps({"lines": ["Error: invalid request"], "cwd": self.cwd})

        query = request.get("query", "")
        mode = request.get("mode", "navigation")

        if not query:
            return json.dumps({"lines": ["Error: empty query"], "cwd": self.cwd})

        if not self.shell_proc or self.shell_proc.poll() is not None:
            log_debug("Shell is dead, restarting")
            self.start_shell()

        lines = self.run_agent_loop(query, mode)

        response = {
            "lines": lines,
            "cwd": self.cwd,
        }
        log_debug("Response:", response)
        return json.dumps(response)

    def cleanup(self):
        """Clean up resources."""
        log_debug("Cleaning up daemon")
        self.running = False
        self.stop_shell()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass

    def run(self):
        """Main daemon loop — listen on socket, handle requests."""
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)
                log_debug(f"Daemon already running (PID {old_pid}), exiting")
                sys.exit(0)
            except (OSError, ValueError):
                pass  # Old process dead or stale PID file

        signal.signal(signal.SIGTERM, lambda s, f: self.cleanup())
        signal.signal(signal.SIGINT, lambda s, f: self.cleanup())

        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        log_debug(f"Daemon starting, PID={os.getpid()}")

        self.start_shell()
        self.running = True

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

        server_sock.bind(SOCKET_PATH)
        server_sock.listen(5)
        server_sock.settimeout(1.0)
        log_debug(f"Listening on {SOCKET_PATH}")

        try:
            while self.running:
                elapsed = time.time() - self.last_activity
                if elapsed > INACTIVITY_TIMEOUT:
                    log_debug(f"Inactivity timeout ({INACTIVITY_TIMEOUT}s), shutting down")
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
                        response = self.handle_request(request_data)
                        conn.sendall((response + '\n').encode('utf-8'))
                except Exception as e:
                    log_debug(f"Error handling connection: {e}")
                finally:
                    conn.close()
        finally:
            server_sock.close()
            self.cleanup()


if __name__ == '__main__':
    daemon = KollzshDaemon()
    daemon.run()
