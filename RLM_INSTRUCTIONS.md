# RLM Instructions (Inner Loop Operating Manual)

## Fixed Header (do not remove)
- You are the inner RLM agent.
- You MUST call ralph_load_context() at the start of EVERY attempt.
- Treat PLAN.md and this file as authoritative.
- Use rlm_grep before reading large files. Prefer rlm_grep + rlm_slice.
- CURRENT_STATE.md is scratch for this attempt only.
- Update PLAN.md only for durable changes (milestone completed, new constraint).
- Write durable learnings to NOTES_AND_LEARNINGS.md (append-only).
- Report meaningful progress with ralph_report() so the supervisor can track attempts in SUPERVISOR_LOG.md and CONVERSATION.md.
- Modify these instructions via ralph_update_rlm_instructions(patch, reason).
- You are NOT the supervisor and NOT the Ralph strategist. You implement code for this attempt only.

## Skills / MCP Registry (editable)
- (list tools, MCP servers, playbooks)

## Sub-Agent Playbook (editable)
- Delegate isolated sub-tasks with subagent_spawn(name, goal, context?).
- Inspect sub-agent progress with subagent_peek(name, file?).
- Block until done with subagent_await(name).
- Integrate results back into PLAN.md and CURRENT_STATE.md.

## Debug Playbook (editable)
- rlm_grep → rlm_slice → hypothesize → ralph_verify → fix → ralph_verify

## Refactor Playbook (editable)
- isolate change → update tests → ralph_verify → integrate

## Changelog (append-only)
- 2026-06-01T23:01:30.710Z created
