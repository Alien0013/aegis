---
name: codebase-onboarding
description: Get productive in an unfamiliar codebase fast — map the structure, find the entry points and data flow, and trace the path for the task at hand before changing anything. Use when starting in a repo you don't know.
version: 1.0.0
metadata:
  category: software-development
  tags: [codebase, onboarding, exploration, architecture, navigation]
---

## When to Use
You've been dropped into a repo you didn't write and need to make a change without breaking it. Resist editing on first contact — **build a map first**. Ten minutes of orientation prevents an hour of wrong-layer changes.

## Procedure
1. **Read the meta first.** README, CONTRIBUTING, `AGENTS.md`/`CLAUDE.md`, docs/, and the dependency manifest (`package.json`, `pyproject.toml`, `go.mod`). These tell you the language, frameworks, conventions, and how to build/test — before you read a single line of logic.
2. **Size and shape it.** Get the language breakdown and the biggest/most-changed files — those are usually the core. Map the top-level directory layout to responsibilities (where do routes, models, services, tests live?).
3. **Find the entry points.** Locate `main`, the server bootstrap, the CLI command table, or route definitions. Start where execution starts, then follow the call chain inward.
4. **Trace one real path end to end.** Pick a single representative request/command and follow it through every layer (entry → handler → service → data → response). One full trace teaches the architecture better than skimming ten files.
5. **Learn the conventions by example.** Before adding code, read 2–3 existing examples of the thing you're about to write (a route, a test, a migration). Match their structure, naming, and error handling — consistency over personal preference.
6. **Run it.** Build, run the test suite, start the app. A green baseline confirms your environment works and gives you the safety net to change things. Note how tests are structured — you'll add one.
7. **Locate the task surface.** `grep`/symbol-search for the feature you're touching; find every call site (fixing a bug class means fixing siblings too, not just the one site).

## Quick Reference
```bash
# orient
fd -e py -e ts | head ;  tokei  ||  cloc .            # languages + LOC
git log --format= --name-only | sort | uniq -c | sort -rn | head   # hottest files
rg -n "def main|if __name__|app = |router|createServer|addCommand"  # entry points
# trace
rg -n "ClassName|functionName" --type py               # call sites of a symbol
git log -p -S "symbol"                                  # when/why a symbol appeared
# run
<build> && <test> && <run>     # from README/CI — get a green baseline
```

## Pitfalls
- Editing before understanding — changing the wrong layer, or duplicating something that already exists.
- Reading files alphabetically instead of following execution order.
- Ignoring the existing style and importing your own conventions (inconsistency is a defect).
- Trusting comments/docs over the running code when they disagree — verify against behavior.
- Fixing one call site of a bug and missing its siblings (search for the pattern, not the instance).
- Skipping the build/test step, then discovering your environment was broken all along.

## Verification
- You can name the entry point, the main layers, and where state lives — and point to the files.
- You traced at least one real request/command through every layer.
- The project builds and its tests pass on your machine (green baseline) before you change anything.
- For the task at hand, you've found every relevant call site and a nearby example to match.
