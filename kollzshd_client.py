#!/usr/bin/env python3
"""CLI de comunicacao com o daemon kollzsh via Unix socket.

Substitui os scripts inline ``python3 -c`` que antes estavam em ``koll.zsh``,
eliminando quoting fragility e centralizando a logica de socket/Python.

Uso:
    python3 kollzshd_client.py send --query "..." --mode navigation [--lines]
    python3 kollzshd_client.py stream --query "..."
    python3 kollzshd_client.py parse-lines
"""

import argparse
import json
import socket
import sys

SOCKET_PATH: str = "/tmp/kollzshd.sock"


def _send_query(sock_path: str, query: str, mode: str) -> str:
    """Envia query JSON ao daemon e retorna a resposta completa.

    Args:
        sock_path: Caminho do socket Unix do daemon.
        query: Consulta do usuario.
        mode: Modo de operacao (navigation ou deep).

    Returns:
        String com a resposta JSON do daemon.
    """
    payload = json.dumps({"query": query, "mode": mode})
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(sock_path)
    except ConnectionRefusedError:
        print("Error: kollzsh daemon is not running.", file=sys.stderr)
        sock.close()
        sys.exit(1)
    try:
        sock.sendall(payload.encode() + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode().strip()
    finally:
        sock.close()


def _render_event(event: dict) -> str:
    """Formata um evento de streaming para exibicao no terminal.

    Renderiza para texto legivel os eventos enviados pelo daemon,
    com icones e identacao consistentes.

    Args:
        event: Dict do evento (type, round, msg, cmd, lines, etc).

    Returns:
        String formatada para stderr, ou string vazia se evento
        nao tiver representacao textual.
    """
    event_type = event.get("type", "")
    round_num = event.get("round", "")
    lines: list[str] = []

    if event_type == "think":
        if event.get("status") == "start":
            if round_num:
                sep = "\u2500" * 38
                lines.append(f"\u2500\u2500 Round {round_num}/2 {sep}")
            lines.append(f"  [THINK]  {event.get('msg', '')}")
    elif event_type == "error":
        lines.append(f"  [ERRO]   {event.get('msg', '')}")

    return "\n".join(lines)


def _stream_query(sock_path: str, query: str) -> None:
    """Streaming de eventos para stdout (direto ao terminal do usuario).

    Conecta ao daemon em modo streaming, le eventos JSON linha a linha.
    Todo output vai para stdout para que o widget ZSH exiba diretamente.

    Args:
        sock_path: Caminho do socket Unix.
        query: Consulta do usuario para busca profunda.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(sock_path)
    except ConnectionRefusedError:
        print("Error: kollzsh daemon is not running.", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.dumps({"query": query, "mode": "deep"})
        sock.sendall(payload.encode() + b"\n")
        sock.settimeout(300.0)
        sock.shutdown(socket.SHUT_WR)

        buf = ""
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            buf += data.decode()
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "done":
                    lines = event.get("lines", [])
                    if lines:
                        print(f"\n=== Resultado ({len(lines)} linhas) ===", flush=True)
                        for l in lines:
                            print(l, flush=True)
                    else:
                        print("\n[busca profunda] Nenhum resultado encontrado.", flush=True)
                    return

                rendered = _render_event(event)
                if rendered:
                    print(rendered, flush=True)

    except (BrokenPipeError, OSError) as exc:
        print(f"[kollzsh] Connection lost: {exc}", flush=True)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _parse_lines() -> None:
    """Le JSON da stdin e imprime cada linha de 'lines' no stdout.

    Utilizado pelo ZSH para extrair linhas de resultado da resposta
    do daemon sem precisar de um inline ``python3 -c``.
    """
    try:
        data = json.loads(sys.stdin.read())
        for line in data.get("lines", []):
            print(line)
    except json.JSONDecodeError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI para comunicacao com o daemon kollzsh.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    send_parser = sub.add_parser("send")
    send_parser.add_argument("--query", required=True)
    send_parser.add_argument("--mode", default="navigation")
    send_parser.add_argument("--sock", default=SOCKET_PATH)
    send_parser.add_argument(
        "--lines",
        action="store_true",
        help="Extrai linhas do JSON de resposta para pipe no fzf",
    )

    stream_parser = sub.add_parser("stream")
    stream_parser.add_argument("--query", required=True)
    stream_parser.add_argument("--sock", default=SOCKET_PATH)

    sub.add_parser("parse-lines")

    args = parser.parse_args()

    if args.command == "send":
        response = _send_query(args.sock, args.query, args.mode)
        if args.lines:
            try:
                data = json.loads(response)
                for line in data.get("lines", []):
                    print(line)
            except json.JSONDecodeError:
                pass
        else:
            print(response)

    elif args.command == "stream":
        _stream_query(args.sock, args.query)

    elif args.command == "parse-lines":
        _parse_lines()


if __name__ == "__main__":
    main()
