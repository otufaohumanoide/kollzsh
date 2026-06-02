# Sub-Agent Notes and Learnings

- 2026-06-01T23:15:31.182Z created
- 2026-06-01: kollzsh project uses `depends_on` edge type for BFS-compatible dependency tracking, not `imports` or `calls`. BFS was empty because no code files matched standard entry point patterns (index.js, main.py, etc.); the oh-my-zsh plugin loader (kollzsh.plugin.zsh) is the actual entry point but doesn't match those patterns. Tour must work around this by using edge structure + domain knowledge
