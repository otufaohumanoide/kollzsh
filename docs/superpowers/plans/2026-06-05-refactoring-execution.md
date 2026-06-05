# Refatoração Estrutural — Plano de Implementação

> **Para workers agenticos:** Steps usam checkbox (`- [ ]`) para tracking.

**Goal:** Implementar os 4 itens de refatoração do spec (agente, logging, client, streaming fix)

**Arquitetura:** Modificações paralelas que não quebram o protocolo ZSH↔daemon. Cada task produz um estado funcional.

**Tech Stack:** Python 3.10+, ZSH, stdlib-only

---

### Task 1: Criar `kollzshd_logging.py`

**Arquivos:**
- Create: `kollzshd_logging.py`
- Modify (next tasks): `kollzshd.py`, `kollzshd_commands.py`, `kollzshd_llm.py`, `kollzshd_pi.py`

- [ ] **Criar o módulo de logging centralizado**

```python
"""Configuracao centralizada de logging para o daemon kollzsh."""

import logging

LOG_FILE: str = "/tmp/kollzsh_debug.log"

_configured: bool = False


def setup_logging(log_file: str = LOG_FILE) -> None:
    """Inicializa o logging uma unica vez.

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
        data: Dados adicionais opcionais.
    """
    if data is not None:
        logging.debug(
            f"{message}\nData: {data}\n"
            "----------------------------------------",
        )
    else:
        logging.debug(message)
```

- [ ] **Commit**

---

### Task 2: Atualizar `kollzshd_commands.py` — remover `logging.basicConfig` e `def log_debug`

**Arquivos:**
- Modify: `kollzshd_commands.py`

- [ ] **Remover `logging.basicConfig()` e `def log_debug`, importar de `kollzshd_logging`**

Changes:
1. Remove lines 16-21 (`logging` import stays, remove `basicConfig` block, remove `def log_debug`)
2. Add `from kollzshd_logging import log_debug` no topo

- [ ] **Commit**

---

### Task 3: Atualizar `kollzshd_llm.py` — remover `logging.basicConfig()`

**Arquivos:**
- Modify: `kollzshd_llm.py`

- [ ] **Remover `logging.basicConfig()`, import `log_debug` de `kollzshd_logging`**

Changes:
1. Remove lines 24-30 (`logging.basicConfig` block)
2. Change `from kollzshd_commands import log_debug, parse_and_validate_commands` to `from kollzshd_commands import parse_and_validate_commands` + `from kollzshd_logging import log_debug`

- [ ] **Commit**

---

### Task 4: Atualizar `kollzshd_pi.py` — remover `def log_debug` e `logging.basicConfig()`

**Arquivos:**
- Modify: `kollzshd_pi.py`

- [ ] **Remover `logging.basicConfig()` e `def log_debug`, importar de `kollzshd_logging`**

Changes:
1. Remove lines 13-19 (`logging.basicConfig` block)
2. Remove `def log_debug` (lines 26-30)
3. Add `from kollzshd_logging import log_debug`

- [ ] **Commit**

---

### Task 5: Atualizar `kollzshd.py` — logging setup + decompor `run_agent_loop`

**Arquivos:**
- Modify: `kollzshd.py`

- [ ] **Mudar import de logging e adicionar `setup_logging()` no main**

1. Remove `logging.basicConfig(...)` block (lines 55-60)
2. Keep `import logging` (linha 23)
3. Add `from kollzshd_logging import setup_logging, log_debug` (remover `log_debug` de kollzshd_commands import)
4. In `if __name__ == '__main__':` (linha 535), add `setup_logging()` as first call

- [ ] **Extrair `_run_navigation` e `_run_deep_pi` como métodos privados**

Replace `run_agent_loop` (lines 196-350) with:
```python
def run_agent_loop(
    self,
    query: str,
    mode: str = "navigation",
    event_sender: EventSender | None = None,
) -> list[str]:
    """Dispatch da acao conforme modo de operacao."""
    if mode == "deep":
        return self._run_deep_pi(query, event_sender)
    return self._run_navigation(query, event_sender)


def _run_navigation(
    self,
    query: str,
    event_sender: EventSender | None = None,
) -> list[str]:
    """Single-round navigation: LLM gera comandos, daemon executa e trunca."""
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

- [ ] **Remover código morto**

Remove `_parse_deep_response` (lines 135-194), `MAX_ROUNDS` (line 51), auto-file-read code.

- [ ] **Atualizar handlers de request — parse JSON antes do dispatch**

No accept loop (lines 508-527):
```python
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

- [ ] **Simplificar `handle_request` e `handle_request_streaming`**

Mudar assinatura de `request_data: str` para `request: dict`. Remover parsing duplicado. Cortar ~10 linhas de cada.

`handle_request` (lines 397-433):
```python
def handle_request(self, request: dict) -> str:
    """Processa requisicao JSON (ja parseada). Retorna JSON string."""
    self.last_activity = time.time()
    query = request.get("query", "")
    if not query:
        return json.dumps({"lines": ["Error: empty query"], "cwd": self.cwd})
    if not self.shell_proc or self.shell_proc.poll() is not None:
        self.start_shell()
    lines = self.run_agent_loop(query, "navigation")
    result = {"lines": lines, "cwd": self.cwd}
    log_debug("Response:", result)
    return json.dumps(result)
```

`handle_request_streaming` (lines 352-395):
```python
def handle_request_streaming(self, request: dict, conn: socket.socket) -> None:
    """Processa requisicao com streaming de eventos."""
    self.last_activity = time.time()
    query = request.get("query", "")
    if not query:
        self._send_event(conn, "error", msg="Empty query")
        self._send_event(conn, "done", lines=["Error: empty query"], cwd=self.cwd)
        return
    if not self.shell_proc or self.shell_proc.poll() is not None:
        self.start_shell()

    def send_event(type_name: str, **kwargs) -> None:
        event: dict[str, object] = {"type": type_name}
        event.update(kwargs)
        try:
            conn.sendall((json.dumps(event) + "\n").encode())
        except Exception:
            pass

    lines = self.run_agent_loop(query, "deep", event_sender=send_event)
    send_event("done", lines=lines, cwd=self.cwd)
```

Note: `send_event` continues as a closure inside `handle_request_streaming` — it captures `conn`. No extra method needed.

- [ ] **Commit**

---

### Task 6: Criar `kollzshd_client.py`

**Arquivos:**
- Create: `kollzshd_client.py`

- [ ] **Criar o módulo CLI**

Conteúdo conforme spec (seção 2). 3 subcomandos: `send`, `stream`, `parse-lines`.

- [ ] **Commit**

---

### Task 7: Simplificar `koll.zsh` — remover inline Python scripts

**Arquivos:**
- Modify: `koll.zsh`

- [ ] **Substituir `send_to_daemon`**

Replace lines 80-99:
```zsh
send_to_daemon() {
  local query="$1"
  local mode="${2:-navigation}"
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" send \
    --query "$query" --mode "$mode" --lines
}
```

- [ ] **Substituir `stream_from_daemon`**

Replace lines 145-206:
```zsh
stream_from_daemon() {
  local query="$1"
  python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" stream --query "$query"
}
```

- [ ] **Simplificar `fzf_kollzsh` para usar novas funções**

O pipeline `response=$(send_to_daemon ...)` agora já retorna linhas limpas. O bloco de extração (lines 119-128) vira obsoleto:

```zsh
fzf_kollzsh() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I
  echo -n "👻 Please wait..."

  ensure_daemon_running
  local result
  result=$(send_to_daemon "$user_query" "navigation")

  if [ -n "$result" ]; then
    result=$(echo "$result" | FZF_DEFAULT_OPTS="--reverse --cycle" fzf)
  fi

  if [ -n "$result" ]; then
    BUFFER="$result"
    CURSOR=${#BUFFER}
  fi

  zle reset-prompt
}
```

- [ ] **Simplificar `fzf_kollzsh_deep`**

```zsh
fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I
  ensure_daemon_running

  local response
  response=$(stream_from_daemon "$user_query")

  if [ -z "$response" ]; then
    log_debug "No response from daemon"
    zle reset-prompt
    return
  fi

  local lines
  lines=$(echo "$response" | python3 "${KOLLZSH_PLUGIN_DIR}/kollzshd_client.py" parse-lines)

  if [ -n "$lines" ]; then
    BUFFER="$lines"
    CURSOR=${#BUFFER}
  fi

  zle reset-prompt
}
```

- [ ] **Commit**

---

## Pos-implantacao

Os seguintes itens foram implementados em sessao separada
(`2026-06-05-refactoring-cleanup.md`):

1. Remocao de `llm_util.py` e `ollama_util.py` (334 linhas mortas)
2. Correcao de `truncate_output` (sanduiche dinamico proporcional a `max_lines`)
3. Remocao de `is_readonly()` (dead code em `kollzshd_commands.py`)
4. Remocao de `build_deep_search_prompt()` (dead code em `kollzshd_llm.py`)
5. Atualizacao de docs (AGENTS.md, spec e plan)
