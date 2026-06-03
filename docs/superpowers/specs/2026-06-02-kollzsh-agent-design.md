# kollzsh: Agente de navegação com busca profunda

## Resumo

Estender o plugin kollzsh com um daemon Python stateful que permite à LLM
executar comandos shell arbitrários (grep, find, rg, ls, cat etc.) em um
processo persistente, mantendo CWD e histórico entre chamadas. A LLM escreve os
comandos — sem abstrações intermediárias — e o daemon executa, captura output,
e decide em no máximo 2 rounds se refinou o suficiente. O resultado final é
exibido no fzf para seleção interativa.

## Arquitetura

```
zsh widget (Ctrl+O / Ctrl+G)
       │
       ▼
Daemon Python persistente
  ├── Processo shell permanente (bash)
  ├── CWD sincronizado via pwd após cada comando
  ├── Filtro de comandos (whitelist read-only)
  ├── Truncamento de output (sanduíche: top 20 + bottom 20)
  └── Máximo 2 rounds por query
       │
       ├─── Round 1: LLM recebe [CWD] + query → escreve comandos
       │             → daemon executa → stdout truncado → LLM avalia
       │
       ├─── Round 2 (se necessário): LLM refina → executa → avalia
       │
       └─── Final: output formatado → stdout → fzf → usuário escolhe
```

O daemon é iniciado automaticamente pelo plugin no carregamento do ZSH e
permanece em execução durante toda a sessão. Um arquivo PID em /tmp controla
que apenas uma instância exista por sessão.

## Estado mantido pelo daemon

| Campo | Tipo | Descrição |
|---|---|---|
| cwd | string | Diretório atual, sincronizado via pwd |
| history | list | Últimos 10 CWDs visitados |
| last_query | string | Query atual (para referência no round 2) |
| last_output | string | Output truncado do round 1 |

## Protocolo ZSH → Daemon

O daemon escuta em um socket Unix em /tmp/kollzshd.sock. O widget envia JSON
e recebe JSON:

```python
# Requisição
{"query": "onde uso validate_required?"}

# Resposta
{
  "lines": ["grep -rn 'validate_required' . --include='*.py'"],
  "conversation_id": "c7a3b1"
}
```

O fluxo inteiro (rounds com a LLM) acontece dentro do daemon. O ZSH só vê o
resultado final.

## Dois modos de operação

### Modo 1: Navegação (Ctrl+O) — fzf_kollzsh

Prompt enviado à LLM:

```
You are in: {cwd}
User query: "{query}"
Generate 1-3 shell commands to explore, navigate, or answer this.
Return ONLY a JSON list of strings. No explanation.
```

Sem rounds adicionais. O output vai direto para o fzf. Mesmo comportamento do
kollzsh original, mas agora a LLM pode executar comandos reais.

Exemplos:

| Query | LLM escreve |
|---|---|
| "o que tem aqui?" | `ls -la` |
| "entre em docs" | `cd docs && ls -la` |
| "qual o tamanho dos arquivos .py?" | `find . -name '*.py' -exec wc -l {} +` |

### Modo 2: Busca profunda (Ctrl+G) — fzf_kollzsh_deep

Prompt enviado à LLM:

```
You are in: {cwd}
User query: "{query}"

You can run shell commands to explore and find information.
Write precise commands using grep, rg, find, cat, head, etc.
Use pipes to limit output: head -30, tail -20.

If the output is insufficient, I will ask you to refine ONCE MORE.
After that, the results go to the user.

Return a JSON list of 1-2 commands:
{"commands": ["...", "..."]}
```

Após o round 1, o daemon executa e envia de volta:

```
Command output (truncated):
{stdout}

Is this enough to answer "{query}"?
If yes, return {"done": true, "answer": ["relevant", "lines"]}
If no, return {"done": false, "refine": ["more precise command"]}
```

Máximo 2 rounds. Se a LLM não retornar `done: true` no round 2, o daemon
coleta o que tem e finaliza. O usuário decide no fzf.

## Filtro de comandos (whitelist)

### Comandos de leitura (execução automática)

```
grep rg ag find ls cat head tail wc stat file sort uniq diff tree pwd echo
which type du df bat less strings nl od xxd column cut tr fmt fold expand
pr printf env dirname basename realpath readlink date cal bc seq shuf
tsort comm paste join look split cksum md5sum sha1sum sha256sum
```

### Comandos de escrita/sistema (pedem confirmação)

Qualquer comando **não listado acima** ou que contenha `>`, `>>`, `|` seguido
de comando destrutivo, ou `sudo`, `rm`, `mv`, `cp`, `chmod`, `chown`, `kill`,
`apt`, `pacman`, `brew`, `dnf`, `yum`, `pip`, `npm`, `docker`, `systemctl`
— exige confirmação do usuário via fzf (sim/não) antes de executar.

### Implementação do filtro

```python
READONLY_COMMANDS = {"grep", "rg", "ag", "find", "ls", "cat", ...}
DESTRUCTIVE_COMMANDS = {"rm", "mv", "cp", "chmod", "chown", "sudo", "kill"}

def is_readonly(command: str) -> bool:
    tokens = shlex.split(command)
    has_readonly = False
    for t in tokens:
        if t in DESTRUCTIVE_COMMANDS or t in (">", ">>"):
            return False
        if t in READONLY_COMMANDS:
            has_readonly = True
    return has_readonly
```

## Processo shell persistente

O daemon inicia um bash interativo com `subprocess.Popen` e pipes de
stdin/stdout/stderr. Para cada comando:

1. Escreve o comando no stdin do shell + `; echo "__KSEP__"; pwd; echo "__KEND__"`
2. Lê até encontrar `__KEND__`
3. Extrai stdout + CWD entre os marcadores
4. Atualiza `cwd` com o resultado do `pwd`

Isso elimina completamente a necessidade de parsear sintaxe de shell. O
shell interpreta `cd docs && rg 'algo'`, `cd $HOME`, `cd ../..` — tudo que
a LLM escrever — e o `pwd` posterior revela o estado real.

## Truncamento de output (sanduíche)

```python
def truncate_output(lines: list[str], max_lines: int = 40) -> list[str]:
    if len(lines) <= max_lines:
        return lines
    top = 20
    bottom = 20
    omitted = len(lines) - top - bottom
    return (lines[:top]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-bottom:])
```

## Ciclo de vida do daemon

1. **koll.zsh** verifica se o daemon está rodando (PID em /tmp/kollzshd.pid)
2. Se não estiver, inicia: `python3 {plugin_dir}/kollzshd.py &`
3. O daemon escreve seu PID e aguarda conexões no socket Unix
4. Quando o ZSH termina (trap EXIT), o plugin mata o daemon
5. O daemon também se mata se ficar inativo por 30 minutos

## Arquivos novos

| Arquivo | Descrição |
|---|---|
| `kollzshd.py` | Daemon stateful: socket, shell persistente, loop agente |
| `kollzshd_commands.py` | Whitelist, execução, truncamento, filtro |
| `kollzshd_llm.py` | Prompt, rounds, parse de resposta da LLM |

Modificações mínimas nos arquivos existentes:

- `koll.zsh`: adicionar widget Ctrl+G, verificação/início do daemon
- `utils.zsh`: adicionar `check_daemon_running`

## Segurança

- Whitelist read-only executada automaticamente
- Comandos destrutivos exigem confirmação via fzf
- Timeout de 30s por comando executado
- Timeout de 120s por chamada à API da LLM
- O daemon recusa conexões de fora do localhost (socket Unix)

## Consideração de tokens

- Prompt é minimalista: `[CWD] query: "..."` — sem histórico de conversa
- Output truncado a 40 linhas
- Máximo 2 rounds = no máximo 3 chamadas à API por query
- Estado (CWD, histórico) fica no Python, nunca no prompt
