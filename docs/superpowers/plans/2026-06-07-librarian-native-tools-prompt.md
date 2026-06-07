# Librarian Native Tools & Sophisticated Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Pi agent native tools (grep, find, ls) and replace the generic librarian prompt with a sophisticated legal query decomposition instruction set.

**Architecture:** Single file change to `pi_client.py`: enable Pi-mono native tools and rewrite the librarian prompt with knowledge base structure docs, query decomposition examples, and search strategy.

**Tech Stack:** Python 3.10+, Pi-mono Node.js subprocess

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pi_client.py:55` | Enable native tools (`grep,find,ls`) |
| `pi_client.py:90-99` | Rewrite librarian prompt with decomposition instructions |

---

### Task 1: Verify current syntax

**Files:**
- `pi_client.py`

- [ ] **Step 1: Verify Python compiles**

Run: `python3 -m py_compile pi_client.py && echo "OK"`
Expected: `OK`

---

### Task 2: Enable native Pi tools

**Files:**
- Modify: `pi_client.py:55`

- [ ] **Step 1: Change tools argument**

Change line 55 from:
```python
            "--tools", "read,bash",
```
To:
```python
            "--tools", "read,bash,grep,find,ls",
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile pi_client.py && echo "OK"`
Expected: `OK`

---

### Task 3: Rewrite librarian prompt

**Files:**
- Modify: `pi_client.py:88-99`

- [ ] **Step 1: Replace prompt with sophisticated version**

Replace lines 88-99 from:
```python
    extra = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
    kb_path = os.path.join(os.path.expanduser("~/.pi/agent/extensions/estagiario-data"), "atomos")
    librarian_query = (
        f"Search topic: {query}\n\n"
        f"Find the answer in the knowledge base at {kb_path}\n"
        f"Use your domain search tools to find the relevant atom(s).\n"
        f"STOP as soon as you find the answer — do not search further.\n"
        f"Return ONLY the content of the matching atom(s), nothing else.\n"
        f"AVOID: raw file listings, directory dumps, or extra explanation."
    )
    if extra:
        librarian_query += f"\n\nUser context: {extra}"
```

To:
```python
    extra = os.getenv('KOLLZSH_SYSTEM_CONTEXT', '').strip()
    kb_path = os.path.join(os.path.expanduser("~/.pi/agent/extensions/estagiario-data"), "atomos")
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
    if extra:
        librarian_query += f"\n\nUser context: {extra}"
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile pi_client.py && echo "OK"`
Expected: `OK`

---

### Task 4: Manual test

**Files:**
- `pi_client.py`

- [ ] **Step 1: Start a new ZSH session or source rc**

Run: `source ~/.zshrc`

- [ ] **Step 2: Test simple query**

Press Ctrl+F and type: `qual pena para roubo?`
Expected: Pi uses grep/find tools, returns relevant atoms

- [ ] **Step 3: Test complex query**

Press Ctrl+F and type: `qual pena para roubo onde a vitima foi agredida?`
Expected: Pi decomposes into separate searches for roubo, lesao corporal, agravantes

- [ ] **Step 4: Check debug log**

Run: `tail -50 /tmp/kollzsh_debug.log`
Expected: No errors, Pi uses native tools

---

### Task 5: Commit

- [ ] **Step 1: Stage and commit**

```bash
git add pi_client.py
git commit -m "feat(librarian): enable native tools and rewrite prompt for legal query decomposition

- Enable Pi-mono native tools: grep, find, ls (in addition to read, bash)
- Rewrite librarian prompt with knowledge base structure docs
- Add query decomposition instructions with legal examples
- Add search strategy for agravantes, concurso, circunstancias"
```
