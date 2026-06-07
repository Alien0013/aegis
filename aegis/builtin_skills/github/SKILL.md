---
name: github
description: Manage GitHub issues, pull requests, CI runs, and reviews from the terminal using the gh CLI. Use for any GitHub repo operation.
version: 1.0.0
requires:
  bins: [gh, git]
metadata:
  category: dev
  tags: [github, git, ci, pr]
---

## When to Use
Any task involving GitHub: opening/triaging issues, creating or reviewing PRs,
checking CI status, or an issue→PR workflow.

## Quick Reference
- Auth check: `gh auth status`
- Issues: `gh issue list`, `gh issue view <n>`, `gh issue create -t "..." -b "..."`
- PRs: `gh pr list`, `gh pr view <n>`, `gh pr create -t "..." -b "..."`, `gh pr checkout <n>`
- Diff/review: `gh pr diff <n>`, `gh pr review <n> --approve|--request-changes -b "..."`
- CI: `gh run list`, `gh run view <id> --log-failed`

## Procedure (issue → PR)
1. `gh issue view <n>` to understand the request.
2. Create a branch, make the change, run tests.
3. `gh pr create` linking the issue (`Closes #<n>`), with a clear body.
4. Watch CI with `gh run watch`; fix failures.

## Pitfalls
- Always run `gh auth status` first; stop and ask the user to `gh auth login` if unauthenticated.
- Never force-push shared branches. Confirm before closing issues.

## Verification
After a PR, confirm `gh pr checks <n>` is green before reporting done.
