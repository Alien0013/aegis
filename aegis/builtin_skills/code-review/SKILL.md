---
name: code-review
description: Review a code diff for correctness bugs, security issues, and simplifications. Use when asked to review changes or a PR.
version: 1.0.0
requires:
  bins: [git]
metadata:
  category: dev
  tags: [review, code, quality]
---

## When to Use
When asked to review uncommitted changes, a branch, or a PR.

## Procedure
1. Get the diff: `git diff` (working tree) or `git diff main...HEAD` (branch).
2. Read each changed file in full for context — never review a hunk in isolation.
3. Look for, in priority order:
   - **Correctness**: off-by-one, null/None, error handling, race conditions, wrong logic.
   - **Security**: injection, secrets in code, unsafe deserialization, path traversal.
   - **Simplification**: duplicated logic, dead code, needless abstraction.
4. For each finding give: file:line, severity, the problem, and a concrete fix.

## Output format
Group findings by severity (blocker / nit). Lead with the highest-impact issues.
If the diff is clean, say so plainly — don't invent problems.

## Verification
Every finding must cite a real line in the diff and a specific fix.
