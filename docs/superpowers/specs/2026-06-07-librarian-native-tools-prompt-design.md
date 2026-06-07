# Librarian Native Tools & Sophisticated Prompt Design

**Date:** 2026-06-07
**Status:** Approved

## Problem

The Pi agent (librarian) receives complex legal queries but searches literally for the
exact query string. It does not decompose compound queries like "roubo onde a vitima
foi agredida" into separate search terms (roubo, lesao corporal, agravantes).

Additionally, the Pi agent uses `bash` to run grep/find/ls commands instead of
using the specialized native tools available in Pi-mono.

## Design

Two changes to `pi_client.py`, the only file modified:

### 1. Enable native Pi tools

**Line 55:** Change `--tools` argument from `read,bash` to `read,bash,grep,find,ls`.

This gives Pi access to:
- `grep` — optimized content search (ripgrep-based)
- `find` — file finding
- `ls` — directory listing
- `read` — file reading (already enabled)
- `bash` — shell commands (still available for edge cases)

### 2. Rewrite librarian prompt

Replace the current generic prompt with a sophisticated instruction set covering:

- **Knowledge base structure** — explains the law code directory hierarchy
  (cp/, cc/, cdc/, etc.), topic subdirectories, and the role of `_manifest.json`
  and `_meta.json` as index/metadata files
- **Query decomposition** — explicit instructions to break complex queries into
  separate search terms with concrete examples
- **Search strategy** — step-by-step process: list directories → grep terms →
  read manifests → read meta for relations → read atoms
- **Output format** — return full content of all matching atoms with file paths

New prompt:
```python
librarian_query = (
    f"You are a legal research librarian. Your task is to find relevant legal knowledge atoms.\n\n"
    f"KNOWLEDGE BASE STRUCTURE:\n"
    f"The knowledge base is at {kb_path}\n"
    f"Each subdirectory is a law code: cp/ (Codigo Penal), cc/ (Codigo Civil), cdc/ (Consumidor), etc.\n"
    f"Each law code has topic subdirectories with atom .md files.\n"
    f"Read _manifest.json in each directory for an index of atoms.\n"
    f"Read _meta.json for deontic classification and relations between atoms.\n\n"
    f"QUERY DECOMPOSITION:\n"
    f"Before searching, decompose complex queries into separate search terms.\n"
    f"Example: 'roubo onde a vitima foi agredida' → search for: (1) 'roubo', (2) 'lesao corporal' or 'agressao', (3) 'agravantes' or 'circunstancias'\n"
    f"Example: 'homicidio culposo com reincidencia' → search for: (1) 'homicidio culposo', (2) 'reincidencia'\n"
    f"Example: 'furto com violencia' → search for: (1) 'furto', (2) 'violencia', (3) 'roubo' (furto com violencia pode ser roubo)\n\n"
    f"SEARCH STRATEGY:\n"
    f"1. First, list the top-level directories to understand available law codes\n"
    f"2. Use grep to search for your decomposed terms across the knowledge base\n"
    f"3. Read _manifest.json files to find relevant atoms by topic\n"
    f"4. Read _meta.json to understand relations between atoms (requisito_de, causa_de, etc.)\n"
    f"5. Read the actual atom .md files to get the legal content\n"
    f"6. If searching for agravantes, aggravating circumstances, or special conditions, search for those terms explicitly\n"
    f"7. If the query involves multiple crimes or concurrence (concurso), search for each crime separately\n\n"
    f"OUTPUT:\n"
    f"Return the FULL content of ALL matching atoms found.\n"
    f"Include the atom file path for reference.\n"
    f"STOP as soon as you find clear answers for ALL decomposed terms.\n"
    f"AVOID: raw file listings without reading, directory dumps, or extra explanation."
)
```

If `KOLLZSH_SYSTEM_CONTEXT` is set, append: `\n\nUser context: {extra}`

### Changes Summary

**File:** `pi_client.py`

| Line | Change |
|------|--------|
| 55   | `--tools "read,bash"` → `--tools "read,bash,grep,find,ls"` |
| 90–99 | New sophisticated prompt (see above) |

## Rationale

- Single file change, minimal risk
- Leverages Pi-mono's existing optimized tools instead of bash subprocesses
- Explicit decomposition instructions guide the LLM without requiring code changes
- Knowledge base structure documentation helps Pi navigate the 1,061 atom files
- Backward compatible — existing queries continue to work with improved results

## Testing

- Run `python3 -m py_compile pi_client.py` to verify syntax
- Manual test with Ctrl+F: search "qual pena para roubo onde a vitima foi agredida"
- Verify Pi uses grep/find tools instead of bash
- Verify Pi decomposes the query into separate search terms
