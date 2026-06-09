---
name: writing-plans
description: Turn a vague task into an actionable implementation plan — bite-sized steps, exact file paths, and a verification check per step. Use before starting any non-trivial change.
version: 1.0.0
metadata:
  category: planning
  tags: [planning, design, decomposition, strategy]
  license: MIT
---

## When to Use
Before any multi-step or non-obvious task: a feature, a refactor, a migration, a
bug you can't fix in one edit. A good plan turns "make it work" into checkable steps.

## What a good plan has
- **Goal + success criteria** — how you'll *know* it's done (a test passes, output
  matches, command exits 0). Weak criteria ("make it work") cause endless loops.
- **Bite-sized steps** — each independently verifiable, ordered by dependency.
- **Concrete anchors** — exact file paths, function names, commands. Not "update the
  config" but "set `tools.exec_mode` in `aegis/config.py`".
- **A verify line per step** — the check that proves the step worked.
- **Risks / unknowns** — what you're unsure about; resolve or flag before coding.

## Format
```
Goal: <one sentence> · Success: <observable check>
1. <step> → file: <path> → verify: <check>
2. <step> → file: <path> → verify: <check>
3. <step> → verify: <test/command>
Risks: <unknowns, rollback plan>
```

## Procedure
1. Restate the goal and the success criteria first.
2. Explore the relevant code (read, search) before planning — don't plan blind.
3. Decompose into the smallest ordered steps that each end in a green check.
4. Surface assumptions and ask the user (use `clarify`) if a choice is genuinely theirs.
5. Execute step by step, verifying each; adjust the plan when reality disagrees.

## Verify
Every step traces to the goal, names a real file/command, and has a check. If a step
can't be verified, it's too big — split it.
