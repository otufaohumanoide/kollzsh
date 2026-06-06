# Deep Librarian — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Ctrl+F deep mode from "Pi responde perguntas" to "bibliotecário que busca contexto e nunca responde", com saída para stderr.

**Architecture:** Mudanças localizadas em 3 arquivos (kollzshd_pi.py, kollzshd_client.py, koll.zsh) + docs. Sem nova infraestrutura.

**Tech Stack:** Python 3.10+, ZSH, Node.js (Pi, unchanged)

---

### File Structure

| File | Task | Mudança |
|---|---|---|
| `kollzshd_pi.py` | 1 | Adicionar system prompt de bibliotecário ao enviar query para Pi |
| `kollzshd_client.py` | 2 | Remover prefixo `[DONE]` da renderização do evento `result` |
| `koll.zsh` | 3 | `fzf_kollzsh_deep` redireciona stdout para `/dev/null`, remove parse-lines |
| `AGENTS.md` | 4 | Atualizar descrição do deep mode |

---

### Task 1: Adicionar system prompt do bibliotecário no Pi

**Files:**
- Modify: `kollzshd_pi.py`

- [ ] **Ler o arquivo para confirmar os trechos**

```bash
python3 -m py_compile kollzshd_pi.py && echo "syntax OK"
```

- [ ] **Adicionar variável com instrução do bibliotecário e modificar o prompt enviado ao Pi**

Localizar o trecho que envia o prompt para o Pi (linhas 226-228 atuais):

```python
prompt = json.dumps({"id": "1", "type": "prompt", "message": query}) + "\n"
proc.stdin.write(prompt.encode())
proc.stdin.flush()
```

Substituir por:

```python
extra_context = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
msg = (
    "[LIBRARIAN SYSTEM INSTRUCTION]\n"
    "You are a librarian. You search for relevant content. You NEVER answer questions.\n\n"
    "Rules:\n"
    "1. Search the filesystem for content relevant to the user's input\n"
    "2. Use grep/rg/find to locate files related to the topic\n"
    "3. Read matching files in full (.txt, .md, etc.)\n"
    "4. ALWAYS show the file path and its full content\n"
    "5. NEVER answer the user's question directly\n"
    "6. You may ask clarifying questions or suggest related topics\n"
    "7. Treat ALL user input as a search topic\n"
)
if extra_context:
    msg += f"\n[USER CONTEXT]\n{extra_context}\n"
msg += f"\n[USER QUERY]\n{query}"

prompt = json.dumps({"id": "1", "type": "prompt", "message": msg}) + "\n"
```

- [ ] **Verificar sintaxe**

```bash
python3 -m py_compile kollzshd_pi.py && echo "syntax OK"
```

---

### Task 2: Remover prefixo `[DONE]` da renderização do evento `result`

**Files:**
- Modify: `kollzshd_client.py`

- [ ] **Alterar renderização do evento `result`**

Localizar em `_render_event` (linhas 79-81 atuais):

```python
elif event_type == "result":
    for line in event.get("lines", []):
        lines.append(f"  [DONE]   {line}")
```

Substituir por:

```python
elif event_type == "result":
    lines.extend(event.get("lines", []))
```

- [ ] **Verificar sintaxe**

```bash
python3 -m py_compile kollzshd_client.py && echo "syntax OK"
```

---

### Task 3: Widget `fzf_kollzsh_deep` — saída para stderr apenas

**Files:**
- Modify: `koll.zsh`

- [ ] **Substituir `fzf_kollzsh_deep`**

Localizar a função `fzf_kollzsh_deep` (linhas 121-147 atuais):

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

Substituir por:

```zsh
fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I

  ensure_daemon_running

  # Bibliotecário: progresso + resultado vão para stderr.
  # done event (JSON) é descartado em /dev/null.
  stream_from_daemon "$user_query" > /dev/null

  zle reset-prompt
}
```

- [ ] **Verificar que `stream_from_daemon` ainda funciona sem captura**

```bash
rg "stream_from_daemon" koll.zsh
# Expected: definição (linha 116) + uso na função nova (sem captura de stdout)
```

---

### Task 4: Atualizar `AGENTS.md`

**Files:**
- Modify: `AGENTS.md`

- [ ] **Atualizar arquitetura e descrição do deep mode**

Linha 10: `deep mode → Pi RPC (Node.js DCI-Agent) → streamed events → buffer`
→ `deep mode → Pi "librarian" → searches content → streamed events → stderr`

Linha 33 (tabela): `Deep search` → `Librarian search`

Linha 68-76 (seção Deep Mode): Atualizar para descrever o comportamento de bibliotecário.

```markdown
## Deep mode — Bibliotecário (Ctrl+F)

Pi DCI-Agent opera como bibliotecário: busca conteúdo contextualmente relevante
no filesystem local usando grep/rg/find, retorna caminhos de arquivos + conteúdo
completo via stderr (leitura no terminal). Nunca responde perguntas diretamente.
```

---
