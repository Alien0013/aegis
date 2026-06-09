---
name: standup-report
description: Generate a daily standup / status update from git history and notes — what shipped, what's in progress, blockers. Use for "write my standup", "what did I do this week", or a status update.
version: 1.0.0
metadata:
  category: productivity
  tags: [standup, status, git, report, assistant]
---

## When to Use
The user wants a status update grounded in real activity, not a guess. Pull from the repo and any notes they point at.

## Procedure
1. **Gather real activity.** Use `bash`:
   - `git log --since="<period>" --author="<them>" --pretty=format:'%h %s' --no-merges`
   - `git diff --stat @{1.day.ago}` or a branch range for scope.
   - Add any linked issues/PRs (`gh` if available) and notes files (`read_file`).
2. **Group commits by theme**, not chronology — collapse "wip/fix typo" noise into the real unit of work.
3. **Write the update** in standup shape:
   - **Done** — shipped/merged since last update.
   - **In progress** — with rough % or next step.
   - **Blockers** — concrete, with what's needed to unblock (or "none").
4. **Keep it honest.** Only list work the evidence supports. Don't pad. If asked for a weekly summary, widen the window and add a one-line theme.
5. **Deliver** — print it, or `send_message` to the team channel if asked.

## Guardrails
- Don't claim work that isn't in the history/notes. "In progress" ≠ "done".
