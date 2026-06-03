#!/usr/bin/env python3
"""Daemon principal do kollzsh — shell persistente + agente LLM.

Este daemon é o coração da arquitetura estendida do kollzsh. Ele:

1. Mantém um processo bash persistente (``--norc --noprofile``)
2. Escuta em um socket Unix em ``/tmp/kollzshd.sock``
3. Recebe consultas JSON dos widgets ZSH (navegação ou busca profunda)
4. Executa o loop agente com a LLM (1-2 rounds)
5. Retorna resultados formatados para seleção via fzf

Protocolo ZSH → Daemon:
    ``{"query": "...", "mode": "navigation|deep"}``

Resposta Daemon → ZSH:
    ``{"lines": ["...", "..."], "cwd": "/path/to/dir"}``

O daemon controla CWD via ``pwd`` após cada comando, eliminando a
necessidade de parsear sintaxe de shell para detectar ``cd``.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from typing import List, Optional

from kollzshd_commands import (
    execute_command, truncate_output, log_debug
)
from kollzshd_llm import (
    build_navigation_prompt, build_deep_search_prompt,
    extract_commands, call_llm
)

# Caminhos de comunicação entre daemon e ZSH
SOCKET_PATH: str = "/tmp/kollzshd.sock"
PID_FILE: str = "/tmp/kollzshd.pid"

# Limites do loop agente
MAX_ROUNDS: int = 2           # Máximo de rounds por query
INACTIVITY_TIMEOUT: int = 1800  # 30 minutos sem atividade → desliga

LOG_FILE = '/tmp/kollzsh_debug.log'
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class KollzshDaemon:
    """Daemon stateful que gerencia shell persistente e loop agente LLM.

    Attributes:
        cwd: Diretório de trabalho atual (sincronizado via pwd).
        history: Últimos 10 CWDs visitados.
        shell_proc: Processo bash persistente (subprocess.Popen).
        last_activity: Timestamp da última atividade (para timeout).
        running: Flag de controle do loop principal.
    """

    def __init__(self) -> None:
        """Inicializa o daemon com estado vazio."""
        self.cwd: str = os.getcwd()
        self.history: List[str] = []
        self.shell_proc: Optional[subprocess.Popen] = None
        self.last_activity: float = time.time()
        self.running: bool = False

    def start_shell(self) -> None:
        """Inicia o subprocesso bash persistente.

        Usa ``--norc --noprofile`` para evitar que configurações do
        usuário (aliases, PS1, etc) interfiram com a execução de comandos.
        """
        log_debug("Starting shell subprocess")
        self.shell_proc = subprocess.Popen(
            ["bash", "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr no stdout
            text=True,
            bufsize=1,  # Line-buffered para leitura imediata
        )
        log_debug(f"Shell started, PID={self.shell_proc.pid}")

    def stop_shell(self) -> None:
        """Para o subprocesso bash de forma graceful.

        Tenta fechar stdin primeiro (EOF mata o shell). Se não funcionar
        em 5 segundos, força kill.
        """
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

    def update_cwd(self, new_cwd: str) -> None:
        """Atualiza CWD e mantém histórico das últimas 10 entradas.

        O histórico permite que a LLM saiba onde o usuário já navegou,
        mas NÃO é incluído no prompt (economia de tokens).

        Args:
            new_cwd: Novo diretório de trabalho retornado pelo ``pwd``.
        """
        if new_cwd and new_cwd != self.cwd:
            self.history.append(self.cwd)
            # Mantém apenas as últimas 10 entradas
            if len(self.history) > 10:
                self.history = self.history[-10:]
            self.cwd = new_cwd
            log_debug(f"CWD changed: {self.cwd}")

    def run_agent_loop(self, query: str, mode: str = "navigation") -> List[str]:
        """Executa o loop agente de interação com a LLM.

        Modo navegação (1 round):
            LLM gera comandos → daemon executa → retorna output.

        Modo busca profunda (2 rounds):
            Round 1: LLM gera comandos → daemon executa
            Round 2: LLM recebe output → decide se refina ou finaliza

        Se a LLM não retornar ``done: true`` no round 2, o daemon
        coleta o que tem e finaliza — o usuário decide no fzf.

        Args:
            query: Consulta do usuário.
            mode: ``"navigation"`` ou ``"deep"``.

        Returns:
            Lista de linhas de output truncado.
        """
        log_debug(f"Agent loop: mode={mode}, query={query}")
        all_output: List[str] = []
        cwd = self.cwd

        # Navegação: round único. Busca profunda: até MAX_ROUNDS.
        max_rounds = 1 if mode == "navigation" else MAX_ROUNDS

        for round_num in range(1, max_rounds + 1):
            log_debug(f"Round {round_num}")

            # Round 1 sempre usa navigation prompt (com tool_calling)
            # Round 2 usa deep prompt (sem tools, JSON puro)
            if mode == "navigation" or round_num == 1:
                payload = build_navigation_prompt(cwd, query)
            else:
                payload = build_deep_search_prompt(
                    cwd, query, round_num, '\n'.join(all_output)
                )

            response_data = call_llm(payload)
            if not response_data:
                log_debug("LLM call failed")
                all_output.append("Error: LLM call failed")
                break

            # Round 2 do modo deep: LLM retorna done/refine em vez de comandos
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
                            # LLM considerou suficiente — extrai resposta final
                            answer = parsed.get("answer", [])
                            if isinstance(answer, list):
                                all_output.extend([str(a) for a in answer])
                            else:
                                all_output.append(str(answer))
                            break
                        elif parsed.get("done") is False:
                            # LLM quer refinar — extrai comandos de refine
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
                # Rounds de navegação e round 1 de deep: extrai comandos
                commands = extract_commands(response_data)

            if not commands:
                log_debug("No commands extracted")
                if round_num == 1:
                    all_output.append("No relevant commands found")
                break

            # Executa cada comando no shell persistente
            for cmd in commands:
                success, output, new_cwd = execute_command(cmd, self.shell_proc)
                if not success:
                    log_debug(f"Command failed, checking shell: {cmd}")
                    # Se o shell morreu, reinicia para o próximo comando
                    if not self.shell_proc or self.shell_proc.poll() is not None:
                        log_debug("Shell died, restarting")
                        self.start_shell()
                if new_cwd:
                    self.update_cwd(new_cwd)
                    cwd = self.cwd
                if output:
                    lines = output.strip().split('\n')
                    all_output.extend(lines)

            # Navegação: para após round 1
            if mode == "navigation":
                break

        return truncate_output(all_output)

    def handle_request(self, request_data: str) -> str:
        """Processa uma requisição JSON do widget ZSH.

        Args:
            request_data: String JSON com ``query`` e ``mode``.

        Returns:
            String JSON com ``lines`` (lista de resultados) e ``cwd``.
        """
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

        # Reinicia shell se morreu (ex: OOM kill, crash)
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

    def cleanup(self) -> None:
        """Libera recursos: shell, socket, PID file."""
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

    def run(self) -> None:
        """Loop principal do daemon — escuta socket, processa requisições.

        Fluxo:
        1. Verifica se já existe daemon rodando (PID file)
        2. Registra handlers de signal (SIGTERM, SIGINT)
        3. Escreve PID file
        4. Inicia shell persistente
        5. Cria socket Unix e escuta conexões
        6. Para cada conexão: lê request, processa, retorna resposta
        7. Desliga após INACTIVITY_TIMEOUT segundos sem atividade
        """
        # Verifica se já existe daemon rodando
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)
                log_debug(f"Daemon already running (PID {old_pid}), exiting")
                sys.exit(0)
            except (OSError, ValueError):
                pass  # Processo antigo morto ou PID file stale

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
                        # Protocolo: request termina com newline
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
