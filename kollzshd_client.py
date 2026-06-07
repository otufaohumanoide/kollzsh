#!/usr/bin/env python3
"""CLI for communicating with the kollzsh daemon via Unix socket.

Replaces the inline ``python3 -c`` scripts that were previously in ``koll.zsh``,
eliminating quoting fragility and centralizing socket/Python logic.

Usage:
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
    """Send JSON query to the daemon and return the full response.

    Args:
        sock_path: Path to the daemon's Unix socket.
        query: User query.
        mode: Operation mode (navigation or deep).

    Returns:
        String with the daemon's JSON response.
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
    """Format a streaming event for terminal display.

    Renders daemon events into readable text with
    consistent icons and indentation.

    Args:
        event: Event dict (type, round, msg, cmd, lines, etc).

    Returns:
        Formatted string for stderr, or empty string if the event
        has no textual representation.
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
    """Stream events to stdout (direct to user terminal).

    Connects to the daemon in streaming mode, reads JSON events line by line.
    All output goes to stdout so the ZSH widget displays directly.

    Args:
        sock_path: Path to the Unix socket.
        query: User query for deep search.
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
                        print(f"\n=== Results ({len(lines)} lines) ===", flush=True)
                        for l in lines:
                            print(l, flush=True)
                    else:
                        print("\n[deep search] No results found.", flush=True)
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
    """Read JSON from stdin and print each line of 'lines' to stdout.

    Used by ZSH to extract result lines from daemon responses
    without needing an inline ``python3 -c``.
    """
    try:
        data = json.loads(sys.stdin.read())
        for line in data.get("lines", []):
            print(line)
    except json.JSONDecodeError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI for communicating with the kollzsh daemon.",
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
        help="Extract lines from JSON response for piping to fzf",
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
