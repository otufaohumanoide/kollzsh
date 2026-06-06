#!/usr/bin/env python3
"""Entry point do daemon kollzsh — verifica PID e inicia servidor.

Delega para ``server.DaemonServer`` toda a lógica de socket, shell,
e dispatch de agentes.
"""

import os
import sys

from kollzshd_logging import setup_logging, log_debug
from server import DaemonServer

SOCKET_PATH: str = "/tmp/kollzshd.sock"
PID_FILE: str = "/tmp/kollzshd.pid"
INACTIVITY_TIMEOUT: int = 1800


if __name__ == '__main__':
    setup_logging()

    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log_debug(f"Daemon already running (PID {old_pid}), exiting")
            sys.exit(0)
        except (OSError, ValueError):
            pass

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    log_debug(f"Daemon starting, PID={os.getpid()}")

    server = DaemonServer(SOCKET_PATH, PID_FILE, INACTIVITY_TIMEOUT)
    server.run()
