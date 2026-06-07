---
name: commit
description: Stage changes and write a clear, conventional git commit message. Use when asked to commit work.
version: 1.0.0
requires:
  bins: [git]
metadata:
  category: dev
  tags: [git, commit]
---

## When to Use
When the user asks to commit changes.

## Procedure
1. Review what changed: `git status` and `git diff` (and `git diff --staged`).
2. Group related changes; stage with `git add -p` or specific paths.
3. Write a message: a concise imperative subject (<72 chars), then a body
   explaining *why* (not just what). Use conventional prefixes when apt:
   `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
4. Commit. Do not push unless the user explicitly asks.

## Pitfalls
- Never commit secrets or large binaries — check the diff first.
- One logical change per commit; split unrelated work.

## Verification
Run `git log -1 --stat` and confirm the message matches the staged change.
