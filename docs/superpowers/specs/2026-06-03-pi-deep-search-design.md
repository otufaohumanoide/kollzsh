# Substituição do Ctrl+F (deep search) por Pi/DCI-Agent

## Resumo

Substituir o atual deep search (Ctrl+F) do kollzsh pelo agente Pi do
DCI-Agent-Lite, usando **modelo local (llama.cpp)** com setup zero-touch —
o usuário não precisa instalar Node, clonar repositórios, nem criar
arquivos de configuração manualmente.

O Ctrl+O (navegação) permanece intocado.

## Arquitetura

### Fluxo anterior (Ctrl+F)

```
ZSH → daemon (Python) → LLM API (2 rounds)
  → daemon executa comandos no bash persistente
  → truncamento simples → resultado → ZSH
```

### Novo fluxo (Ctrl+F)

```
ZSH → daemon (Python) → Pi RPC (Node.js, por consulta)
  → Pi chama LLM via provider local
  → Pi executa bash tools para busca
  → Pi context management (truncation + compaction + summarization)
  → Pi retorna resposta final → daemon mata Pi → ZSH
```

### Por que Pi RPC por consulta?

Cada Ctrl+F spawna um processo Pi RPC isolado:

- Envia query via JSON-RPC no stdin
- Lê eventos do stdout (tool_execution, text_delta, agent_end)
- Extrai o texto final
- Mata o processo Pi

Sem estado compartilhado entre consultas. O shell persistente do daemon
continua servindo apenas o Ctrl+O.

## Setup zero-touch

Nenhuma ação manual do usuário. Tudo acontece lazy no **primeiro Ctrl+F:**

### 1. Verificar Node.js ≥20

Se `node --version` retornar <20 ou não existir:

1. Baixa NVM: `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash`
2. Instala Node 20: `nvm install 20 && nvm use 20`
3. Atualiza PATH para incluir o bin do Node 20

### 2. Clonar e buildar Pi

Se `pi-mono/packages/coding-agent/dist/cli.js` não existir:

```bash
git clone https://github.com/jdf-prog/pi-mono.git pi-mono
cd pi-mono
git checkout codex/context-management-ablation
npm install
npm run build
```

Tudo dentro do diretório do plugin kollzsh.

### 3. Gerar `~/.pi/agent/models.json`

Derivado automaticamente de `KOLLZSH_URL` e `KOLLZSH_MODEL`. Se o arquivo já existir e o conteúdo bater com a config atual, não reescreve.

```json
{
  "providers": {
    "local": {
      "baseUrl": "<KOLLZSH_URL>/v1",
      "api": "openai-completions",
      "apiKey": "dummy",
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false
      },
      "models": [
        { "id": "<KOLLZSH_MODEL>" }
      ]
    }
  }
}
```

O daemon escreve esse arquivo automaticamente se não existir.

## Configuração (variáveis de ambiente)

Tudo opcional — defaults automáticos:

| Variável | Default | Descrição |
|---|---|---|
| `KOLLZSH_PI_MAX_TURNS` | `20` | Turns máximos por deep search |
| `KOLLZSH_PI_CONTEXT_LEVEL` | `level3` | Nível de context management (level0-level4) |
| `KOLLZSH_PI_TOOLS` | `read,bash` | Ferramentas habilitadas no Pi |
| `KOLLZSH_PI_PACKAGE_DIR` | `<plugin_dir>/pi-mono/packages/coding-agent` | Path do Pi buildado |
| `KOLLZSH_PI_AGENT_DIR` | `~/.pi/agent` | Dir do models.json |
| `KOLLZSH_PI_PROVIDER` | `local` | Provider slug (bate com models.json) |

`KOLLZSH_URL` e `KOLLZSH_MODEL` continuam sendo usados como hoje.

## Mudanças no código

### Arquivo novo: `kollzshd_pi.py`

Módulo que encapsula a comunicação RPC com o Pi:

- `ensure_pi_ready()` → verifica Node.js, clona+builda Pi, gera/valida models.json
- `run_pi_query(cwd, query)` → spawna Pi, envia RPC, lê eventos, retorna resposta

`ensure_pi_ready()` chama internamente as verificações em sequência. A geração do models.json só reescreve se o conteúdo for diferente do atual (evita reescrita desnecessária).

### Mudanças em `kollzshd.py`

No `run_agent_loop`, quando `mode == "deep"`:

```python
if mode == "deep":
    ensure_pi_setup()  # lazy, só executa setup se necessário
    result = run_pi_query(self.cwd, query, pi_config)
    return result
```

### Mudanças em `koll.zsh`

Mensagem de status no `fzf_kollzsh_deep`:

```diff
- echo "🔍 Buscando e analisando..."
+ echo "🔍 Deep search (DCI-Agent)..."
```

O protocolo ZSH↔daemon não muda.

### Arquivos não modificados

- `kollzshd_commands.py`
- `kollzshd_llm.py`
- `utils.zsh`
- `kollzsh.plugin.zsh`

## Configuração do Pi na RPC

Comando montado pelo daemon:

```bash
node <package_dir>/dist/cli.js \
  --mode rpc \
  --provider local \
  --model <KOLLZSH_MODEL> \
  --tools read,bash \
  --no-session \
  --cwd <cwd_do_daemon> \
  --extra-arg="--context-management-level level3" \
```

Pi recebe a query via JSON-RPC `{"id": "1", "type": "prompt", "message": "<query>"}`.

## Context Management

Pi tem 4 níveis de runtime context management:

| Level | Comportamento |
|---|---|
| `level0` | Nenhum (tudo no contexto) |
| `level1` | Truncation leve |
| `level2` | Truncation forte |
| `level3` | Truncation + compaction (placeholders) |
| `level4` | Truncation + compaction + summarization |

Default: `level3`. Controlado por `KOLLZSH_PI_CONTEXT_LEVEL`.

## Error handling

| Problema | Comportamento |
|---|---|
| Node.js <20 | Daemon instala via NVM automaticamente |
| Pi não buildado | Daemon clona+builda (1x) |
| Pi crasha | Daemon tenta reiniciar 1x; se falhar, retorna erro |
| Consulta excede max_turns | Pi retorna o que tem até o momento |
| Provider offline | Retorna "LLM server not available" |

## Ciclo de vida

1. ZSH carrega plugin → daemon inicia (como hoje)
2. Primeiro Ctrl+F:
   a. Daemon verifica Node.js (instala via NVM se <20)
   b. Daemon verifica Pi (clona+builda se faltar)
   c. Daemon cria `~/.pi/agent/models.json`
   d. Daemon spawna Pi RPC, executa query, mata Pi
3. Ctrl+F seguintes: só spawna Pi (passo d)
4. ZSH morre → trap EXIT → daemon morre (sem cleanup extra de Pi)

## Observações

- Não usamos `uv run` nem dependências Python do DCI-Agent-Lite
- O models.json é escrito uma vez e reescrito apenas se conteúdo diferente de `KOLLZSH_URL`/`KOLLZSH_MODEL` (comparação string, não timestamp)
- O clone+build do Pi é bloqueante e mostra mensagem pro usuário
- Pi RPC usa `--no-session` — cada query é uma sessão fresca
- O `cwd` passado pro Pi é o CWD atual do daemon (sincronizado via pwd)
