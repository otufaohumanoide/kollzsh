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
import shlex
import signal
import socket
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional

import re

from kollzshd_commands import (
    execute_command, truncate_output, log_debug
)
from kollzshd_llm import (
    build_navigation_prompt, build_deep_search_prompt,
    extract_commands, call_llm
)
from kollzshd_pi import run_pi_query

EventSender = Callable[..., None]

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

    def _parse_deep_response(self, content: str):
        """Parsing robusto da resposta do round 2 (deep mode).

        A LLM de 4B frequentemente retorna JSON mal formatado:
        - Arrays extras separados por virgula: "answer": [...], [...]
        - JSON parcial (rotos)
        - Resposta em texto livre sem JSON

        Tenta 3 estrategias em ordem:
        1. json.loads() padrao
        2. Unir arrays extras no answer via regex
        3. Extrair texto livre via regex

        Returns:
            (is_done: bool, extracted: list[str])
            is_done=True  → resposta final: usar extracted como lines
            is_done=False → refine: usar extracted como commands
        """
        if not content:
            return False, []

        # Estrategia 1: JSON puro
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return False, []
            if parsed.get("done") is True:
                ans = parsed.get("answer", [])
                if isinstance(ans, list):
                    return True, [str(a) for a in ans if a]
                return True, [str(ans)] if ans else []
            if parsed.get("done") is False:
                ref = parsed.get("refine", [])
                if isinstance(ref, list):
                    return False, [str(c) for c in ref if c]
            return False, []
        except (json.JSONDecodeError, ValueError):
            pass

        # Estrategia 2: regex para JSON mal formatado
        # Ex: {"done": true, "answer": [...] , [...] , [...]}
        done_match = re.search(r'"done"\s*:\s*true', content)
        refine_match = re.search(r'"done"\s*:\s*false', content)
        answer_match = re.findall(r'"([^"]+)"', content.split('"answer"')[-1]) if '"answer"' in content else []

        answer_lines = [a for a in answer_match if len(a) > 10]
        if done_match and answer_lines:
            return True, answer_lines

        refine_cmds = re.findall(r'"([^"]+)"', content.split('"refine"')[-1]) if '"refine"' in content else []
        if refine_match and refine_cmds:
            return False, refine_cmds

        # Estrategia 3: qualquer texto longo = tentar como resposta
        lines = [l.strip() for l in content.strip().split('\n') if l.strip() and len(l.strip()) > 20]
        if lines:
            log_debug("Fallback: extracted response lines from raw content")
            return True, lines

        return False, []

    def run_agent_loop(self, query: str, mode: str = "navigation", event_sender: Optional[EventSender] = None) -> List[str]:
        """Executa o loop agente de interação com a LLM.

        Modo navegação (1 round):
            LLM gera comandos → daemon executa → retorna output.

        Modo busca profunda (2 rounds via LLM):
            Round 1: LLM gera comandos → daemon executa
            Round 2: LLM recebe output → decide se refina ou finaliza

        Modo busca profunda (via Pi DCI-Agent):
            Delega para run_pi_query com event_callback para streaming.

        Args:
            query: Consulta do usuário.
            mode: ``"navigation"`` ou ``"deep"``.
            event_sender: Callback para emitir eventos de progresso (deep mode).

        Returns:
            Lista de linhas de output truncado.
        """
        log_debug(f"Agent loop: mode={mode}, query={query}")

        if mode == "deep":
            plugin_dir = os.environ.get(
                "KOLLZSH_PLUGIN_DIR",
                os.path.dirname(os.path.abspath(__file__)),
            )
            agent_dir = os.environ.get(
                "KOLLZSH_PI_AGENT_DIR",
                os.path.expanduser("~/.pi/agent"),
            )
            url = os.environ.get("KOLLZSH_URL", "http://localhost:8080")
            model = os.environ.get("KOLLZSH_MODEL", "unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q6_K_XL")
            max_turns = int(os.environ.get("KOLLZSH_PI_MAX_TURNS", "20"))
            context_level = os.environ.get("KOLLZSH_PI_CONTEXT_LEVEL", "level3")
            log_debug(f"Deep mode via Pi: url={url}, model={model}, cwd={self.cwd}")
            try:
                lines = run_pi_query(
                    self.cwd, query, plugin_dir, agent_dir,
                    url, model, max_turns, context_level,
                    event_callback=event_sender,
                )
                log_debug("Pi query completed, returning results")
                return truncate_output(lines)
            except Exception as e:
                log_debug(f"Pi query failed: {e}")
                if event_sender:
                    event_sender("error", msg=f"Pi query failed: {e}")
                return [f"Deep search error: {e}"]

        all_output: List[str] = []
        cwd = self.cwd

        max_rounds = 1 if mode == "navigation" else MAX_ROUNDS

        for round_num in range(1, max_rounds + 1):
            log_debug(f"Round {round_num}")

            if event_sender:
                msg = "Buscando arquivos relevantes..." if round_num == 1 else "Analisando resultados..."
                event_sender("think", round=round_num, status="start", msg=msg)

            if mode == "deep":
                payload = build_deep_search_prompt(
                    cwd, query, round_num,
                    '\n'.join(all_output) if round_num > 1 else None
                )
            else:
                payload = build_navigation_prompt(cwd, query)

            response_data = call_llm(payload)
            if not response_data:
                log_debug("LLM call failed")
                all_output.append("Error: LLM call failed")
                if event_sender:
                    event_sender("error", round=round_num, msg="LLM call failed")
                break

            if event_sender:
                event_sender("think", round=round_num, status="end")

            if mode == "deep" and round_num == 2:
                content = ""
                choices = response_data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")

                is_done, extracted = self._parse_deep_response(content)
                if is_done:
                    all_output.append('')
                    all_output.append('---')
                    all_output.append('')
                    all_output.extend(extracted)
                    if event_sender:
                        event_sender("result", round=round_num, lines=extracted)
                    break
                elif extracted:
                    commands = extracted
            else:
                commands = extract_commands(response_data)

            if not commands:
                log_debug("No commands extracted")
                if round_num == 1:
                    all_output.append("No relevant commands found")
                break

            for cmd in commands:
                if event_sender:
                    event_sender("cmd", round=round_num, cmd=cmd)
                success, output, new_cwd = execute_command(cmd, self.shell_proc)
                if not success:
                    log_debug(f"Command failed, checking shell: {cmd}")
                    if not self.shell_proc or self.shell_proc.poll() is not None:
                        log_debug("Shell died, restarting")
                        self.start_shell()
                if new_cwd:
                    self.update_cwd(new_cwd)
                    cwd = self.cwd
                if output:
                    lines_out = output.strip().split('\n')
                    all_output.extend(lines_out)
                    if event_sender:
                        event_sender("out", round=round_num, lines=lines_out)

            if mode == "deep" and round_num == 1:
                seen = set()
                file_paths = []
                for line in all_output:
                    line = line.strip()
                    if line.endswith(('.txt', '.md')) and os.path.isfile(line) and line not in seen:
                        seen.add(line)
                        file_paths.append(line)
                all_output = [l for l in all_output if l.strip() not in seen]
                if file_paths:
                    file_paths = file_paths[:10]
                    for fp in file_paths:
                        if event_sender:
                            event_sender("read", round=round_num, file=fp)
                    read_cmd = 'echo "========== CONTEUDO DOS ARQUIVOS =========="'
                    for fp in file_paths:
                        escaped = shlex.quote(fp)
                        read_cmd += f"; echo '--- {escaped} ---'; cat {escaped}"
                    log_debug(f"Auto-reading {len(file_paths)} files: {file_paths}")
                    success, output, new_cwd = execute_command(read_cmd, self.shell_proc)
                    if output:
                        all_output.append('')
                        all_output.extend(output.strip().split('\n'))

            if mode == "navigation":
                break

        return truncate_output(all_output)

    def handle_request_streaming(self, request_data: str, conn: socket.socket) -> None:
        """Processa requisição com streaming de eventos.

        Diferente de handle_request (que retorna uma string),
        esta versão escreve eventos JSON line-by-line no socket
        conforme cada etapa do processamento acontece.

        Args:
            request_data: String JSON com ``query`` e ``mode``.
            conn: Socket connection para enviar eventos.
        """
        self.last_activity = time.time()
        log_debug("Received request (streaming):", request_data)

        def send_event(type_name: str, **kwargs) -> None:
            event: Dict[str, object] = {"type": type_name}
            event.update(kwargs)
            try:
                conn.sendall((json.dumps(event) + '\n').encode())
            except Exception:
                pass

        try:
            request = json.loads(request_data)
        except json.JSONDecodeError as e:
            log_debug(f"Invalid JSON: {e}")
            send_event("error", msg="Invalid request")
            send_event("done", lines=["Error: invalid request"], cwd=self.cwd)
            return

        query = request.get("query", "")
        mode = request.get("mode", "navigation")

        if not query:
            send_event("error", msg="Empty query")
            send_event("done", lines=["Error: empty query"], cwd=self.cwd)
            return

        if not self.shell_proc or self.shell_proc.poll() is not None:
            log_debug("Shell is dead, restarting")
            self.start_shell()

        lines = self.run_agent_loop(query, mode, event_sender=send_event)
        send_event("done", lines=lines, cwd=self.cwd)

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
                        if b'\n' in data:
                            break

                    if data:
                        request_data = data.decode('utf-8').strip()
                        if '"deep"' in request_data:
                            self.handle_request_streaming(request_data, conn)
                        else:
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
