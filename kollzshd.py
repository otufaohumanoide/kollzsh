#!/usr/bin/env python3
"""Daemon principal do kollzsh — shell persistente + agente LLM.

Este daemon é o coração da arquitetura estendida do kollzsh. Ele:

1. Mantém um processo bash persistente (``--norc --noprofile``)
2. Escuta em um socket Unix em ``/tmp/kollzshd.sock``
3. Recebe consultas JSON dos widgets ZSH (navegação ou busca profunda)
4. Executa o agente: navegação (LLM 1 round) ou busca profunda (Pi DCI-Agent)
5. Retorna resultados formatados para seleção via fzf

Protocolo ZSH → Daemon:
    ``{"query": "...", "mode": "navigation|deep"}``

Resposta Daemon → ZSH (navigation):
    ``{"lines": ["...", "..."], "cwd": "/path/to/dir"}``

Resposta Daemon → ZSH (deep/streaming):
    Linhas JSON line-by-line, cada linha um evento
    (think, cmd, out, read, result, error, done)

O daemon controla CWD via ``pwd`` após cada comando, eliminando a
necessidade de parsear sintaxe de shell para detectar ``cd``.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from typing import Callable, Dict

from kollzshd_commands import (
    execute_command, truncate_output,
)
from kollzshd_llm import (
    build_navigation_prompt,
    extract_commands, call_llm,
)
from kollzshd_logging import setup_logging, log_debug
from kollzshd_pi import run_pi_query

EventSender = Callable[..., None]

# Caminhos de comunicação entre daemon e ZSH
SOCKET_PATH: str = "/tmp/kollzshd.sock"
PID_FILE: str = "/tmp/kollzshd.pid"

# 30 minutos sem atividade → desliga
INACTIVITY_TIMEOUT: int = 1800


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
        self.history: list[str] = []
        self.shell_proc: subprocess.Popen | None = None
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

    def _run_navigation(
        self,
        query: str,
        event_sender: EventSender | None = None,
    ) -> list[str]:
        """Single-round navigation: LLM gera comandos, daemon executa e trunca.

        Args:
            query: Consulta do usuario (buffer do terminal).
            event_sender: Callback opcional para eventos de progresso.

        Returns:
            Lista de linhas de output truncado para selecao via fzf.
        """
        payload = build_navigation_prompt(self.cwd, query)
        response_data = call_llm(payload)
        if not response_data:
            if event_sender:
                event_sender("error", msg="LLM call failed")
            return ["Error: LLM call failed"]

        commands = extract_commands(response_data)
        if not commands:
            return ["No relevant commands found"]

        output: list[str] = []
        for cmd in commands:
            if event_sender:
                event_sender("cmd", cmd=cmd)
            success, cmd_output, new_cwd = execute_command(cmd, self.shell_proc)
            if not success and (not self.shell_proc or self.shell_proc.poll() is not None):
                self.start_shell()
            if new_cwd:
                self.update_cwd(new_cwd)
            if cmd_output:
                output.extend(cmd_output.strip().split("\n"))

        return truncate_output(output)

    def _run_deep_pi(
        self,
        query: str,
        event_sender: EventSender | None = None,
    ) -> list[str]:
        """Deep search via Pi DCI-Agent subprocess.

        Le as configuracoes de ambiente (URL, modelo, diretorios)
        e delega para run_pi_query().

        Args:
            query: Consulta do usuario (busca profunda).
            event_sender: Callback opcional para eventos de progresso.

        Returns:
            Lista de linhas de resultado truncado.
        """
        plugin_dir = os.environ.get(
            "KOLLZSH_PLUGIN_DIR",
            os.path.dirname(os.path.abspath(__file__)),
        )
        agent_dir = os.environ.get(
            "KOLLZSH_PI_AGENT_DIR",
            os.path.expanduser("~/.pi/agent"),
        )
        url = os.environ.get("KOLLZSH_URL", "http://localhost:8080")
        model = os.environ.get(
            "KOLLZSH_MODEL",
            "unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL",
        )
        max_turns = int(os.environ.get("KOLLZSH_PI_MAX_TURNS", "20"))
        context_level = os.environ.get("KOLLZSH_PI_CONTEXT_LEVEL", "level3")

        try:
            lines = run_pi_query(
                self.cwd, query, plugin_dir, agent_dir,
                url, model, max_turns, context_level,
                event_callback=event_sender,
            )
            return truncate_output(lines)
        except Exception as exc:
            if event_sender:
                event_sender("error", msg=f"Pi query failed: {exc}")
            return [f"Deep search error: {exc}"]

    def run_agent_loop(
        self,
        query: str,
        mode: str = "navigation",
        event_sender: EventSender | None = None,
    ) -> list[str]:
        """Dispatch do loop agente conforme modo de operacao.

        Args:
            query: Consulta do usuario.
            mode: ``"navigation"`` (LLM 1 round) ou ``"deep"`` (Pi agent).
            event_sender: Callback opcional para eventos de progresso.

        Returns:
            Lista de linhas de output truncado.
        """
        if mode == "deep":
            return self._run_deep_pi(query, event_sender)
        return self._run_navigation(query, event_sender)

    def handle_request_streaming(self, request: dict, conn: socket.socket) -> None:
        """Processa requisicao com streaming de eventos.

        Escreve eventos JSON line-by-line no socket a medida que
        cada etapa do processamento acontece.

        Args:
            request: Dict com ``query`` e ``mode`` (ja parseado).
            conn: Socket connection para enviar eventos.
        """
        self.last_activity = time.time()
        log_debug("Received request (streaming):", str(request))
        query = request.get("query", "")

        def send_event(type_name: str, **kwargs) -> None:
            event: Dict[str, object] = {"type": type_name}
            event.update(kwargs)
            try:
                conn.sendall((json.dumps(event) + "\n").encode())
            except Exception:
                pass

        if not query:
            send_event("error", msg="Empty query")
            send_event("done", lines=["Error: empty query"], cwd=self.cwd)
            return

        if not self.shell_proc or self.shell_proc.poll() is not None:
            self.start_shell()

        lines = self.run_agent_loop(query, "deep", event_sender=send_event)
        send_event("done", lines=lines, cwd=self.cwd)

    def handle_request(self, request: dict) -> str:
        """Processa uma requisicao JSON do widget ZSH.

        Args:
            request: Dict com ``query`` e ``mode`` (ja parseado).

        Returns:
            String JSON com ``lines`` (lista de resultados) e ``cwd``.
        """
        self.last_activity = time.time()
        log_debug("Received request:", str(request))
        query = request.get("query", "")

        if not query:
            return json.dumps({"lines": ["Error: empty query"], "cwd": self.cwd})

        if not self.shell_proc or self.shell_proc.poll() is not None:
            self.start_shell()

        lines = self.run_agent_loop(query, "navigation")

        result = {"lines": lines, "cwd": self.cwd}
        log_debug("Response:", result)
        return json.dumps(result)

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


if __name__ == '__main__':
    setup_logging()
    daemon = KollzshDaemon()
    daemon.run()
