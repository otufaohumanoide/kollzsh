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

O prompt enviado ao Pi muda de instruções de sistema longas para uma query
curta e natural, prefixada com o tópico de busca:

```python
librarian_query = (
    f"Search topic: {query}\n\n"
    f"Your job: find relevant files in this filesystem and return "
    f"their paths + full content. NEVER answer questions or explain "
    f"anything. Only search and return files."
)
if extra:
    librarian_query += f"\n\nUser context: {extra}"
prompt = json.dumps({"id": "1", "type": "prompt", "message": librarian_query}) + "\n"
```

A instrução é curta e natural (não usa `[LIBRARIAN SYSTEM INSTRUCTION]`), evitando
confundir o Pi RPC com formato híbrido de system + user message.

### 2. `koll.zsh` — Widget `fzf_kollzsh_deep`

Captura stdout (done event JSON) com `$()` para desbloquear o pipe, mas
descarta o resultado — o conteúdo do bibliotecário já foi para stderr via
evento `result`. A linha de comando (BUFFER) permanece limpa.

```zsh
fzf_kollzsh_deep() {
  setopt extendedglob
  validate_required || return 1

  local user_query="$BUFFER"
  zle -I

  ensure_daemon_running

  local response
  response=$(stream_from_daemon "$user_query")

  zle reset-prompt
}
```

Nota: o parser de linhas (`parse-lines`) e a lógica de `BUFFER="$lines"` são
removidos — o conteúdo do bibliotecário é apenas para leitura no terminal.

### 3. `kollzshd_client.py` — Erros de conexão vão para stderr

O evento `result` (que carrega o conteúdo do bibliotecário) é renderizado sem
prefixo `[DONE]`, para leitura limpa no terminal.

Erros de conexão (`except (BrokenPipeError, OSError)`) agora imprimem no stderr
em vez de stdout. Isso garante que, mesmo quando o widget captura stdout com
`$()`, os erros apareçam no terminal.

### 4. `AGENTS.md` — Atualizar descrição

Deep mode agora é "librarian search" em vez de "deep search".

## Fluxo Completo

1. Usuário digita algo no terminal e aperta Ctrl+F
2. `fzf_kollzsh_deep` pega o BUFFER, chama `stream_from_daemon` com `$()`
3. Daemon repassa para Pi com query prefixada como tópico de busca
4. Pi busca arquivos (grep/rg/find), lê conteúdo, formata resultado
5. Eventos de progresso (`think`, `cmd`, `out`, `read`) → stderr
6. Evento `result` (conteúdo do Pi) → renderizado sem prefixo → stderr
7. Evento `done` (JSON) → stdout → capturado em `$response` (descartado)
8. Linha de comando (BUFFER) permanece limpa e inalterada
9. Erros de conexão vão para stderr (sempre visíveis)

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
