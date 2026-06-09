---
name: coding-agent
description: Delegate a large autonomous coding task (big refactor, feature build, issue-to-PR) to an external coding-agent CLI such as claude, codex, or opencode. Use for long-running background builds.
version: 1.0.0
requires:
  anyBins: [claude, codex, opencode]
metadata:
  category: dev
  tags: [coding, delegation, background]
---

## When to Use
When a task is large/long-running and better handed to a dedicated coding-agent
CLI than done inline — e.g. "build this whole feature", "do this big refactor",
"turn this issue into a PR".

## Available drivers (detect with `which`)
- `claude -p "<prompt>"` — Claude Code headless mode
- `codex exec "<prompt>"` — OpenAI Codex CLI non-interactive
- `opencode run "<prompt>"` — OpenCode

## Procedure
1. Pick the first available driver (`which claude || which codex || which opencode`).
2. Write a precise, self-contained prompt: goal, constraints, files in scope, how to verify.
3. Run it via the `bash` tool from the repo root (it edits files in place).
4. Review the resulting diff (`git diff`) and run the test suite before reporting.

## Pitfalls
- These agents edit the working tree — run inside a clean branch/worktree.
- Pass non-interactive flags so the run doesn't block on a prompt.

## Verification
Inspect `git diff` and run tests; summarize what the delegated agent changed.
