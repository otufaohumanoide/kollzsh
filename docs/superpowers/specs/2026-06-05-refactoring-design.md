# kollzsh: Refatoração estrutural — agente, logging, client e modo streaming

## Resumo

Refatorar os 4 problemas estruturais mais críticos identificados no daemon
Python do kollzsh, nesta ordem:

1. Decompor `run_agent_loop` (155 linhas, 3 fluxos misturados, ~80 linhas
   de código morto) em métodos privados focados
2. Criar `kollzshd_client.py` — CLI Python dedicada que substitui os 3
   inline `python3 -c` no ZSH, eliminando quoting fragility
3. Centralizar logging — `kollzshd_logging.py` único, remover 5
   `logging.basicConfig` duplicados e 3 definições de `log_debug`
4. Corrigir detecção de streaming: parse JSON **antes** do dispatch, não
   substring no payload bruto

Saldo: ~60 linhas a menos, código mais legível, zero breaking changes
no protocolo ZSH ↔ daemon.

---

## 1. Decomposição de `run_agent_loop`

### Problema

`KollzshDaemon.run_agent_loop` (kollzshd.py:196-350, 155 linhas) mistura
3 fluxos no mesmo corpo:

- **Pi deep search** (linhas 219-245): coleta env vars, chama
  `run_pi_query`, com early return. Bloco auto-contido.
- **LLM navigation** (1 round): prompt de navegação → LLM → execução →
  truncate (linhas 247-349).
- **LLM deep search** (2 rounds, linhas 259-348 dentro do loop): código
  **nunca alcançado** porque o bloco Pi retorna antes.

O resultado: 4 condicionais `if mode == "deep"` espalhadas, constante
`MAX_ROUNDS` que só é usada se mode="navigation" (recebe 2, mas navigation
quebra após 1 round), e método `_parse_deep_response` de 60 linhas que
nunca é chamado.

### Solução

Substituir classes de agente (proposta inicial) por **métodos privados
flat** no proprio `KollzshDaemon`. Justificativa: o estado compartilhado
(`shell_proc`, `cwd`) já vive no daemon. Uma classe por modo criaria
overhead de `__init__` + `run()` sem beneficio real. PEP 20: *Flat is
better than nested.*

```
KollzshDaemon
├── run_agent_loop()          → dispatch de 2 linhas
│   ├── _run_navigation()     → 25 linhas: LLM → exec → truncate
│   └── _run_deep_pi()        → 20 linhas: coleta env → Pi query
└── _parse_deep_response()    → REMOVIDO (dead code)
```

#### run_agent_loop

```python
def run_agent_loop(
    self,
    query: str,
    mode: str = "navigation",
    event_sender: EventSender | None = None,
) -> list[str]:
    if mode == "deep":
        return self._run_deep_pi(query, event_sender)
    return self._run_navigation(query, event_sender)
```

#### _run_navigation

```python
def _run_navigation(
    self,
    query: str,
    event_sender: EventSender | None = None,
) -> list[str]:
    """Single-round navigation: LLM → commands → execute → truncate."""
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
            output.extend(cmd_output.strip().split('\n'))

    return truncate_output(output)
```

#### _run_deep_pi

```python
def _run_deep_pi(
    self,
    query: str,
    event_sender: EventSender | None = None,
) -> list[str]:
    """Deep search via Pi DCI-Agent subprocess."""
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
```

### Código removido

| Símbolo | Linhas | Motivo |
|---|---|---|
| `_parse_deep_response` | ~60 | Só usado no fluxo LLM deep, nunca alcançado |
| `MAX_ROUNDS` | 1 | Apenas navigation executa; navigation usa 1 round |
| Auto-file-read em deep round 1 | ~20 | Parte do fluxo LLM deep, nunca executado |
| `mode == "deep"` checks no loop | 4 locais | Nao mais necessarios |

---

## 2. kollzshd_client.py — CLI de socket dedicada

### Problema

O arquivo `koll.zsh` contém 3 scripts inline `python3 -c '...'`:

| Script | Local | Linhas | Risco |
|---|---|---|---|
| `send_to_daemon` | 83-98 | 16 | Inofensivo (Python em double quotes) |
| Parser de resposta | 119-128 | 10 | Quotes simples, query com `'` quebra |
| `stream_from_daemon` | 145-205 | 61 | Idem, mais complexo |

Todos os 3 usam quoting frágil. Nenhum faz tratamento de erro adequado.
Toda a lógica de socket/resposta está em strings Python dentro do ZSH,
impossível de testar.

### Solução

Criar `kollzshd_client.py` na raiz do plugin, com 3 subcomandos CLI:

```
python3 kollzshd_client.py send --query "..." --mode navigation [--lines]
python3 kollzshd_client.py stream --query "..."
python3 kollzshd_client.py parse-lines
```

#### send

Envia requisição JSON para o daemon via Unix socket, lê resposta,
imprime no stdout.

- **Sem `--lines`** (padrão): imprime o JSON da resposta do daemon.
- **Com `--lines`**: extrai o campo `lines` e imprime uma linha por entrada
  (para pipe direto no fzf).

#### stream

Conecta ao daemon em modo streaming, lê eventos JSON linha a linha:

- **stderr**: renderização legível de cada evento (think, cmd, out, read,
  result, error).
- **stdout**: apenas o evento `done` serializado como JSON
  (`{"lines": [...], "cwd": "..."}`).

Separação stderr/stdout permite que o ZSH capture o evento `done` no
stdout enquanto o usuario ve o progresso no terminal.

#### parse-lines

Lê JSON da stdin (stdin), imprime cada entry de `"lines"` como uma
linha no stdout. Independe de socket.

```python
"""CLI de comunicacao com o daemon kollzsh via Unix socket."""

import argparse
import json
import socket
import sys

SOCKET_PATH = "/tmp/kollzshd.sock"


def _send_query(
    sock_path: str,
    query: str,
    mode: str,
) -> str:
    """Envia query JSON ao daemon e retorna resposta completa."""
    payload = json.dumps({"query": query, "mode": mode})
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    sock.sendall(payload.encode() + b"\n")
    sock.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    sock.close()
    return data.decode().strip()


def _render_event(event: dict) -> str:
    """Formata um evento de streaming para exibicao no terminal (stderr)."""
    event_type = event.get("type", "")
    round_num = event.get("round", "")
    lines: list[str] = []

    if event_type == "think":
        if event.get("status") == "start":
            if round_num:
                sep = "\u2500" * 38
                lines.append(f"\u2500\u2500 Round {round_num}/2 {sep}")
            lines.append(f"  [THINK]  {event.get('msg', '')}")
    elif event_type == "cmd":
        lines.append(f"  [CMD]    {event.get('cmd', '')}")
    elif event_type == "out":
        for line in event.get("lines", []):
            lines.append(f"  [OUT]      {line}")
    elif event_type == "read":
        lines.append(f"  [READ]   Lendo {event.get('file', '')}...")
    elif event_type == "result":
        for line in event.get("lines", []):
            lines.append(f"  [DONE]   {line}")
    elif event_type == "error":
        lines.append(f"  [ERRO]   {event.get('msg', '')}")

    return "\n".join(lines)


def _stream_query(
    sock_path: str,
    query: str,
) -> None:
    """Streaming: eventos na stderr, evento 'done' no stdout."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    payload = json.dumps({"query": query, "mode": "deep"})
    sock.sendall(payload.encode() + b"\n")
    sock.shutdown(socket.SHUT_WR)
    sock.settimeout(300.0)

    try:
        reader = sock.makefile("r")
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "done":
                result = json.dumps({
                    "lines": event.get("lines", []),
                    "cwd": event.get("cwd", ""),
                })
                print(result)  # stdout -> capturado pelo ZSH
                break

            rendered = _render_event(event)
            if rendered:
                print(rendered, file=sys.stderr)

    except (BrokenPipeError, OSError) as exc:
        print(json.dumps({
            "lines": [f"Connection lost: {exc}"],
            "cwd": "",
        }))
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _parse_lines() -> None:
    """Le JSON da stdin e imprime cada linha de 'lines' no stdout."""
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
        help="Extrai linhas do JSON de resposta (pipe para fzf)",
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
```

### Impacto no ZSH

`koll.zsh` perde ~70 linhas inline. As funcoes de socket viram wrappers
de uma linha:

```zsh
send_to_daemon() {
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" send \
    --query "$1" --mode "${2:-navigation}" --lines
}

stream_from_daemon() {
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" stream --query "$1"
}
```

`fzf_kollzsh` e `fzf_kollzsh_deep` simplificam: nao precisam mais do
pipeline extra `echo "$response" | python3 -c '...'` para parsear.

**Nao muda**: bindkeys, hotkeys, validate_required, daemon lifecycle
(ensure_daemon_running, _kollzsh_cleanup). Nada.

---

## 3. Centralização de logging

### Problema

6 arquivos chamam `logging.basicConfig()` com parametros identicos.
Apenas o primeiro tem efeito; os 5 sao ruidosos mas inofensivos.

`log_debug` definida em 4 lugares:

| Definição | Locais que importam |
|---|---|
| `kollzshd_commands.py` | ✅ `kollzshd.py`, `kollzshd_llm.py` |
| `kollzshd_pi.py` (redefinida) | ❌ Deveria importar |
| `llm_util.py` (redefinida) | ❌ Legado, sera removido |
| `ollama_util.py` (redefinida) | ❌ Legado, sera removido |

### Solução

Criar `kollzshd_logging.py` como fonte unica de logger e funcao `log_debug`.

```python
"""Configuracao centralizada de logging para o daemon kollzsh."""

import logging

LOG_FILE: str = "/tmp/kollzsh_debug.log"

_configured: bool = False


def setup_logging(log_file: str = LOG_FILE) -> None:
    """Inicializa o logging uma unica vez.

    Chamar mais de uma vez nao tem efeito (handlers duplicados
    sao prevenidos pelo flag _configured).

    Args:
        log_file: Caminho para o arquivo de log.
    """
    global _configured
    if _configured:
        return
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configured = True


def log_debug(message: str, data: str | None = None) -> None:
    """Registra mensagem de debug no log do daemon.

    Args:
        message: Mensagem principal.
        data: Dados adicionais opcionais (payload, output, etc).
    """
    if data is not None:
        logging.debug(
            f"{message}\nData: {data}\n"
            "----------------------------------------",
        )
    else:
        logging.debug(message)
```

### Mudanças em cada módulo

| Arquivo | Remove | Adiciona |
|---|---|---|
| `kollzshd.py` | `logging.basicConfig(...)` no topo | `setup_logging()` no main |
| `kollzshd_commands.py` | `logging.basicConfig(...)` + `def log_debug` | `from kollzshd_logging import log_debug` |
| `kollzshd_llm.py` | `logging.basicConfig(...)` | `from kollzshd_logging import log_debug` |
| `kollzshd_pi.py` | `logging.basicConfig(...)` + `def log_debug` | `from kollzshd_logging import log_debug` (`logging` permanece como import se usado para outro fim) |
| `llm_util.py` | Nenhuma (sera removido em etapa separada) | Nenhuma |
| `ollama_util.py` | Nenhuma (sera removido em etapa separada) | Nenhuma |

---

## 4. Detecção de streaming + dedup de handlers

### Problema

Em `kollzshd.py:518-524`, o loop principal detecta modo streaming com
substring no payload bruto:

```python
if '"deep"' in request_data:  # FRAGIL!
    self.handle_request_streaming(request_data, conn)
else:
    response = self.handle_request(request_data)
```

Se o usuario digitar `grep -r 'deep' .`, cai no branch de streaming.

Paralelamente, `handle_request` e `handle_request_streaming` duplicam
~35 linhas de logica identica: parse JSON, extrair query/mode, validar,
reiniciar shell se morto, chamar run_agent_loop.

### Solução

Parsear JSON uma vez no loop principal e passar dict parseado para ambos
os handlers:

```python
# Dentro do accept loop (kollzshd.py ~508)
if data:
    request_data = data.decode("utf-8").strip()
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
```

Os handlers mudam de `def handle_request(self, request_data: str) -> str`
para `def handle_request(self, request: dict) -> str`. Cada um perde ~10
linhas de parsing que se tornam desnecessarias.

---

## Checklist PEP 8

- Imports: stdlib → local (com linha em branco entre grupos)
- Docstrings em todo modulo, funcao publica e metodo
- Type hints (Python 3.10+: `X | None`, `list[str]`)
- `snake_case` para funcoes, `PascalCase` para classes
- `if __name__ == "__main__":` com 2 linhas em branco antes
- `from module import Name` (sem `import *`)
- `Optional[X]` preterido por `X | None` (PEP 604)

## Nao escopo

- Remocao de `llm_util.py` e `ollama_util.py` (deferido)
- Correcao de `truncate_output` com `max_lines` (deferido)
- `kollzshd_pi.py` tambem troca `def log_debug` local por
  `from kollzshd_logging import log_debug` (sem risco de dependencia
  circular: `kollzshd_logging.py` nao importa de `kollzshd_pi.py`)
