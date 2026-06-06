# Deep Librarian — Especificação de Design

## Resumo

Transformar o modo deep (Ctrl+F) de "agente autônomo que responde perguntas"
para **bibliotecário que busca conteúdo contextualmente relevante** no filesystem
local, retornando caminhos de arquivos + conteúdo completo via stderr (leitura
no terminal, sem poluir a linha de comando).

## Arquitetura

```
Antes:  Ctrl+F → Pi ("responding agent") → message_update → BUFFER
Depois: Ctrl+F → Pi ("librarian agent")  → message_update → stderr
```

A infraestrutura existente não muda:
- Pi DCI-Agent (Node.js) com `--tools read,bash`
- Socket Unix, JSON line-by-line streaming
- Eventos `think`, `cmd`, `out`, `read`, `result`, `error`, `done`

O que muda:
1. **System prompt do Pi** — de "responda" para "bibliotecário: busque, nunca responda"
2. **Destino da saída** — de BUFFER para stderr (leitura apenas)
3. **Exibição de progresso** — `think`/`cmd` eventos continuam em stderr (já é assim)

## Componentes Alterados

### 1. `kollzshd_pi.py` — System prompt do bibliotecário

O prompt JSON enviado ao Pi (`{"id": "1", "type": "prompt", "message": query}`)
deve ser substituído por:

```python
prompt = json.dumps({
    "id": "1",
    "type": "prompt",
    "message": (
        f"[SYSTEM] You are a LIBRARIAN. You NEVER answer questions.\n"
        f"Your ONLY job: search for relevant content in this filesystem.\n"
        f"Rules:\n"
        f"1. Use grep/rg/find to locate files related to the search topic\n"
        f"2. Read matching files in full (.txt, .md, etc.)\n"
        f"3. Return FILE PATH + FULL CONTENT for every file you find\n"
        f"4. NEVER answer the user's question directly\n"
        f"5. You may ask clarifying questions about what they need\n"
        f"6. You may suggest related topics/keywords\n"
        f"7. Treat ANY input as a search topic\n\n"
        f"[SYSTEM CONTEXT] {extra_context}\n\n"
        f"[USER QUERY] {query}"
    )
}).encode()
```

O `extra_context` vem da env `KOLLZSH_SYSTEM_CONTEXT` (mesma do modo navegação),
permitindo que o usuário customize o comportamento do bibliotecário.

### 2. `koll.zsh` — Widget `fzf_kollzsh_deep`

O evento `done` do daemon é JSON (`{"lines": [...], "cwd": "..."}`) — útil para
captura programática, mas inviável para leitura direta no terminal. O conteúdo
já vem formatado pelo Pi através do evento `result`, que é renderizado para
stderr pelo `kollzshd_client.py`. A solução: **descartar stdout (done JSON)**
e deixar stderr (progresso + resultado) passar naturalmente.

```zsh
fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I

  ensure_daemon_running

  # Pi envia progresso + resultado para stderr; done JSON vai para /dev/null
  stream_from_daemon "$user_query" > /dev/null

  zle reset-prompt
}
```

Nota: o parser de linhas (`parse-lines`) e a lógica de `BUFFER="$lines"` são
removidos — o conteúdo do bibliotecário é apenas para leitura no terminal.

### 3. `kollzshd_client.py` — Ajuste na renderização

O evento `result` (que carrega o conteúdo do bibliotecário) é renderizado com
prefixo `[DONE]` — herança do design anterior onde o resultado ia para o BUFFER.
Para leitura no terminal, o prefixo é ruído. Mudar de `[DONE]` para nada (linhas
puras):

```python
elif event_type == "result":
    lines.extend(event.get("lines", []))
```

O `stream` subcommand continua inalterado no resto — eventos de progresso
(`think`, `cmd`, `out`, `read`) vão para stderr, e o evento `done` vai para
stdout (agora descartado via `>/dev/null` no ZSH).

### 4. `AGENTS.md` — Atualizar descrição

Deep mode agora é "librarian search" em vez de "deep search".

## Fluxo Completo

1. Usuário digita algo no terminal e aperta Ctrl+F
2. `fzf_kollzsh_deep` pega o BUFFER, chama `stream_from_daemon "$query" > /dev/null`
3. Daemon repassa para Pi com o novo system prompt de bibliotecário
4. Pi busca arquivos (grep/rg/find), lê conteúdo, formata resultado
5. Eventos de progresso (`think`, `cmd`, `out`, `read`) → stderr
6. Evento `result` (conteúdo do Pi) → renderizado sem prefixo → stderr
7. Evento `done` (JSON) → descartado em `/dev/null`
8. Linha de comando (BUFFER) permanece limpa e inalterada

## Exemplo de Saída (stderr)

```
👤 Buscando: git rebase
🧠 Pi turn 1/3
🔍 grep -ril 'rebase|git' --include='*.txt' --include='*.md' .
📖 docs/git-cheatsheet.txt  (153 lines)
📖 notas/workflow.md  (45 lines)

📁 docs/git-cheatsheet.txt:
git rebase -i HEAD~3  → opens interactive rebase
git rebase --abort    → cancels rebase
...

📁 notas/workflow.md:
Always rebase before push to keep history clean
...

🔗 Related: merge, cherry-pick, stash
❓ Looking for: rebase tutorial or conflict resolution?
```

## Configuração

Nenhuma nova env var. Reusa `KOLLZSH_SYSTEM_CONTEXT` (já existente) para
customizações. System prompt base é fixo e hardcoded.

## Casos de Borda

| Caso | Comportamento |
|---|---|
| Input vazio | Pi retorna erro, nada vai para stderr |
| Nenhum arquivo encontrado | Pi informa que não encontrou conteúdo relevante |
| Arquivos muito grandes | Pi lê e retorna conteúdo completo (Pi controla seu output; truncate_output do daemon não se aplica ao modo deep) |
| Pi timeout (300s) | Timeout normal, erro no stderr |
| `KOLLZSH_SYSTEM_CONTEXT` vazio | Prompt funciona sem contexto extra |
