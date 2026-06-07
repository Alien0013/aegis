---
name: refactor
description: Refactor code safely: ensure tests pass before and after, make small behavior-preserving changes, reduce duplication and complexity. Use for cleanup/restructuring tasks.
version: 1.0.0
metadata:
  category: engineering
  tags: [refactor, testing, cleanup]
---

## When to Use
- Restructuring, deduplicating, or simplifying code WITHOUT changing observable behavior.
- Cleanup tasks: extract function, rename, split module, reduce nesting/complexity.
- NOT for adding features or fixing bugs (behavior changes) — those need new tests first.

## Procedure
1. Locate the test command (bash: `cat package.json`, `pyproject.toml`, `Makefile`; or read_file). If none exist, STOP and tell the user — refactoring without tests is unsafe; offer to write characterization tests first.
2. Establish a green baseline: run the full suite (bash). Record pass count. If red, stop — fix or report before touching anything.
3. Make ONE small behavior-preserving change at a time (edit_file). Examples: extract/inline, rename, dedupe, flatten conditionals.
4. Re-run tests after each change (bash). If red, revert that step and reassess — never accumulate broken steps.
5. Keep changes surgical: touch only what the refactor requires; match existing style; don't reformat untouched lines.
6. Remove only orphans YOUR change created (now-unused imports/vars). Leave pre-existing dead code; mention it.
7. After all steps, run the full suite once more (bash) and confirm same-or-better pass count.

## Quick Reference
- Run tests: `npm test` | `pytest -q` | `go test ./...` | `cargo test` | `make test`
- Single file/test: `pytest path::test_name` | `npm test -- <pattern>`
- Diff scope check: `git diff --stat` (every changed file should trace to the refactor)
- Coverage gap before risky change: add a characterization test capturing current output.

## Pitfalls
- Refactoring + behavior change in one commit — keep them separate.
- "Improving" adjacent code, comments, or formatting not in scope.
- Big-bang rewrites: prefer many tiny verified steps over one large diff.
- Trusting a suite you never ran green first — baseline is mandatory.
- Deleting pre-existing dead code uninvited.

## Verification
- Suite was green BEFORE and is green AFTER (same or higher pass count).
- `git diff` shows only behavior-preserving edits; no new failing/skipped tests.
- No new public API or output changed; orphaned symbols from your edits removed.
