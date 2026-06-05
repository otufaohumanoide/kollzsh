# Refactoring Cleanup — Dead Code Removal & Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Remove 334 lines of legacy dead code and fix 2 functions with misleading behavior, split into 4 independent parallel missions.

**Architecture:** 4 missions, each touching a disjoint set of files — no merge conflicts. Each mission is independently testable (syntax check + grep for dead references + import verification).

**Tech Stack:** Python 3.10+, stdlib-only, ZSH

---

### File Structure

| File | Mission | Responsibility |
|---|---|---|
| `llm_util.py` | 1 | DELETE — legacy LLM bridge, unused |
| `ollama_util.py` | 1 | DELETE — legacy Ollama client, unused |
| `kollzshd_commands.py` | 2 | Fix `truncate_output` sandwich + remove `is_readonly` + remove unused `import os` |
| `kollzshd_llm.py` | 3 | Remove `build_deep_search_prompt` dead code |
| `AGENTS.md` | 4 | Remove legacy section, fix gotchas |
| `docs/superpowers/specs/2026-06-05-refactoring-design.md` | 4 | Mark deferred items as resolved |
| `docs/superpowers/plans/2026-06-05-refactoring-execution.md` | 4 | Same |

No file is touched by more than one mission.

---

### Mission 1: Remove Legacy Modules `llm_util.py` + `ollama_util.py`

**Files:**
- Delete: `llm_util.py`
- Delete: `ollama_util.py`

**Dependencies:** None. No active code imports from either file (confirmed via `rg`).

- [ ] **Delete both files with git**

```bash
git rm llm_util.py ollama_util.py
```

- [ ] **Verify no dangling references anywhere**

```bash
rg -l "llm_util|ollama_util" --type py 2>/dev/null
# Expected: no output (0 matches)

rg -l "llm_util|ollama_util" . --type-add 'all:*' -t all 2>/dev/null | rg -v "^(\.git/|pi-mono/|DCI-Agent-Lite/)"
# Expected: only AGENTS.md or docs references (which Mission 4 handles)
```

---

### Mission 2: Fix `truncate_output` + Remove `is_readonly`

**Files:**
- Modify: `kollzshd_commands.py`

**Important ordering:** Remove `is_readonly()` FIRST (it shifts lines 16-69 to 16-44), then fix `truncate_output` at its new position. Use string-based edit (oldString/newString), not line numbers.

- [ ] **Remove unused `import os` (line 16)**

Current:
```python
import os
```

Just delete that line. `os` is not used anywhere in the file (confirmed via `rg -n "os\." kollzshd_commands.py`).

- [ ] **Remove `is_readonly()` function**

Delete the entire `is_readonly` function body (roughly lines 45-69 in original). The function is defined but never called — `validate_command_safety` handles validation. The only caller was the `__main__` block, which is fixed in the next step.

Matching text to remove:
```python

def is_readonly(command: str) -> bool:
    """Verifica se um comando é seguro para executar sem confirmação.

    Analisa cada token do comando (tratando quotes com shlex) e verifica
    se contém apenas comandos da whitelist. Retorna False se encontrar
    qualquer comando destrutivo ou redirect (``>``, ``>>``).

    Args:
        command: Comando shell a ser verificado.

    Returns:
        True se o comando contém apenas comandos read-only.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Fallback para shlex em input malformado (quotes não fechadas)
        tokens = command.split()
    has_readonly = False
    for t in tokens:
        if t in DESTRUCTIVE_COMMANDS or t in (">", ">>"):
            return False
        if t in READONLY_COMMANDS:
            has_readonly = True
    return has_readonly
```

- [ ] **Fix `__main__` block**

Replace the bottom of the file:

Old text:
```python
if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: kollzshd_commands.py <user_query>", file=sys.stderr)
        sys.exit(1)

    user_query = sys.argv[1]
    print(f"is_readonly test: {is_readonly(user_query)}")
```

New text:
```python
if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: kollzshd_commands.py <user_query>", file=sys.stderr)
        sys.exit(1)

    user_query = sys.argv[1]
    valid, reason = validate_command_safety(user_query)
    print(f"Safety check: {'SAFE' if valid else 'BLOCKED'} — {reason}")
```

- [ ] **Fix module docstring (line 7)**

Old: `"top 20 + bottom 20 linhas)"` → New: `"top N + bottom N linhas (proporcional a max_lines)"`

- [ ] **Fix `truncate_output()` to use dynamic sandwich**

Replace the entire function:

Old text:
```python
def truncate_output(lines: List[str], max_lines: int = 120) -> List[str]:
    """Trunca output usando método sanduíche: top 60 + bottom 60.

    Quando o output excede ``max_lines``, mantém as primeiras e últimas
    60 linhas, inserindo um marcador indicando quantas foram omitidas.
    Isso evita que outputs gigantes estourem o contexto da LLM.

    Args:
        lines: Lista de linhas de output.
        max_lines: Limite máximo de linhas (padrão 120).

    Returns:
        Lista truncada com marcador de omission no meio.
    """
    if len(lines) <= max_lines:
        return lines
    top = 60
    bottom = 60
    omitted = len(lines) - top - bottom
    return (lines[:top]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-bottom:])
```

New text:
```python
def truncate_output(lines: list[str], max_lines: int = 120) -> list[str]:
    """Trunca output usando método sanduíche: top N + bottom N.

    Quando o output excede ``max_lines``, mantém as primeiras e últimas
    ``max_lines // 2`` linhas, inserindo um marcador indicando quantas
    foram omitidas. Evita que outputs gigantes estourem o contexto da LLM.

    Args:
        lines: Lista de linhas de output.
        max_lines: Número máximo de linhas no output final.
                   Metade vai para o topo, metade para a base.
                   Mínimo 2 (para exibir pelo menos 1 top + 1 bottom).

    Returns:
        Lista truncada com marcador de omissão no meio.
    """
    if max_lines < 2:
        max_lines = 2
    if len(lines) <= max_lines:
        return lines
    half = max_lines // 2
    omitted = len(lines) - 2 * half
    return (lines[:half]
            + [f"... ({omitted} lines omitted) ..."]
            + lines[-half:])
```

- [ ] **Verify syntax**

```bash
python3 -m py_compile kollzshd_commands.py && echo "syntax OK"
```

- [ ] **Verify imports still work (public API intact)**

```bash
python3 -c "
from kollzshd_commands import (
    execute_command, truncate_output,
    parse_and_validate_commands, validate_command_safety,
    READONLY_COMMANDS, DESTRUCTIVE_COMMANDS,
)
print('imports OK')
"
```

- [ ] **Verify no dangling references to removed function**

```bash
rg "is_readonly" . --type py
# Expected: no output
```

---

### Mission 3: Remove `build_deep_search_prompt` Dead Code

**Files:**
- Modify: `kollzshd_llm.py`

- [ ] **Remove `build_deep_search_prompt()` function**

Delete the entire function (lines 97-173 in original). Matching text:

```python
def build_deep_search_prompt(
    cwd: str,
    query: str,
    round_num: int,
    previous_output: Optional[str] = None
) -> Dict[str, Any]:
    """Constrói o payload da API para o modo busca profunda (Ctrl+G).

    Round 1: LLM gera comandos de busca (com tool_calling).
    Round 2: LLM recebe o output executado e decide se precisa refinar.

    No round 2, NÃO usamos tool_calling — a LLM retorna JSON puro
    com ``done`` (True/False) e ``answer``/``refine``.

    Args:
        cwd: Diretório de trabalho atual do daemon.
        query: Consulta do usuário.
        round_num: Número do round (1 ou 2).
        previous_output: Output do round 1 (necessário apenas no round 2).

    Returns:
        Dict com payload completo para POST /v1/chat/completions.
    """
    ...
    # Full function body as-is in the current file
    ...
```

Since the function is ~77 lines, use the Edit tool with `oldString` matching from `def build_deep_search_prompt(` to the `    return payload` at the end of the function (3 lines before the next function `extract_commands`).

- [ ] **Update module docstring**

Old (line 5): `"Construir prompts para os dois modos de operação (navegação e busca profunda)"`  
New: `"Construir prompt para o modo navegação"`

- [ ] **Verify syntax**

```bash
python3 -m py_compile kollzshd_llm.py && echo "syntax OK"
```

- [ ] **Verify no dangling references to removed function**

```bash
rg "build_deep_search_prompt" .
# Expected: no output (zero matches)
```

- [ ] **Verify imports still work**

```bash
python3 -c "
from kollzshd_llm import build_navigation_prompt, extract_commands, call_llm
print('imports OK')
"
```

---

### Mission 4: Update Docs to Reflect Current State

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-06-05-refactoring-design.md`
- Modify: `docs/superpowers/plans/2026-06-05-refactoring-execution.md`

- [ ] **Update `AGENTS.md`**

1. Remove the "Legacy" section (lines 24-26):

```
**Legacy (unused, candidate for removal):**
- `llm_util.py` — old stateless LLM bridge
- `ollama_util.py` — old Ollama client
```

2. Line 20: `truncate_output (sandwich: top 60 + bottom 60 lines)` → `truncate_output (sandwich: top N + bottom N lines)`

3. Line 21: `prompt construction (navigation vs deep)` → `prompt construction (navigation mode)`

4. Replace Gotchas lines 116-118 (inline `python3 -c`):

Old:
```
- Socket communication uses inline Python (`python3 -c '...'`), not `socat` or `jq`
- `send_to_daemon` uses double-quoted Python (safe for single quotes in queries)
- `stream_from_daemon` uses single-quoted Python (all Python strings must use double quotes)
```

New:
```
- Socket communication via kollzshd_client.py (send, stream, parse-lines subcommands), not inline Python
```

- [ ] **Update spec `docs/superpowers/specs/2026-06-05-refactoring-design.md`**

Replace the "Não escopo" section (lines 534-540):

Old:
```markdown
## Nao escopo

- Remocao de `llm_util.py` e `ollama_util.py` (deferido)
- Correcao de `truncate_output` com `max_lines` (deferido)
- `kollzshd_pi.py` tambem troca `def log_debug` local por
  `from kollzshd_logging import log_debug` (sem risco de dependencia
  circular: `kollzshd_logging.py` nao importa de `kollzshd_pi.py`)
```

New:
```markdown
## Nao escopo (itens resolvidos posteriormente)

- Remocao de `llm_util.py` e `ollama_util.py` — **RESOLVIDO** em `2026-06-05-cleanup` (Missao 1)
- Correcao de `truncate_output` com `max_lines` — **RESOLVIDO** em `2026-06-05-cleanup` (Missao 2)
- `is_readonly()` dead code — **RESOLVIDO** em `2026-06-05-cleanup` (Missao 2)
- `build_deep_search_prompt()` dead code — **RESOLVIDO** em `2026-06-05-cleanup` (Missao 3)
```

- [ ] **Update plan `docs/superpowers/plans/2026-06-05-refactoring-execution.md`**

Append at the end of the file:

```markdown
## Pos-implantacao

Os seguintes itens foram implementados em sessao separada
(`2026-06-05-refactoring-cleanup.md`):

1. Remocao de `llm_util.py` e `ollama_util.py` (334 linhas mortas)
2. Correcao de `truncate_output` (sanduiche dinamico proporcional a `max_lines`)
3. Remocao de `is_readonly()` (dead code em `kollzshd_commands.py`)
4. Remocao de `build_deep_search_prompt()` (dead code em `kollzshd_llm.py`)
5. Atualizacao de docs (AGENTS.md, spec e plan)
```
