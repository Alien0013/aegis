---
name: ultracode
description: A rigorous, autonomous engineering loop for non-trivial code tasks — understand, plan, implement surgically, verify with real tests, self-review, and iterate until the success criteria are provably met. Use for multi-step features, refactors, or bug fixes where correctness matters and "looks done" isn't enough.
version: 1.0.0
metadata:
  category: software-development
  tags: [workflow, autonomy, planning, testing, verification, self-review, quality]
---

## When to Use
The task is more than a one-line edit: a feature, a refactor, a real bug, or anything where a wrong guess is expensive. The goal is not to *look* finished — it's to *be* finished, with evidence. This loop trades a little speed for far fewer rewrites. For a trivial change, skip it and just make the edit.

## Procedure
1. **Turn the task into a verifiable goal.** Restate the request as a success criterion you can check, not a vibe. "Add validation" → "these invalid inputs are rejected with these errors, proven by a test." If the goal is ambiguous or has multiple readings, surface that and pick the most likely one explicitly before coding.
2. **Understand before touching.** Read the surrounding code, the call sites, and one existing example of the thing you're about to write. Match the project's style, naming, and error handling. Find every site a change must touch (a bug class usually has siblings).
3. **Plan in steps, each with its own check.** Write a short numbered plan where every step names how you'll verify it. A plan whose steps can't be checked is a wish, not a plan. Keep it visible (a todo list) and update it as reality intrudes.
4. **Write the failing test first** when the goal is behavioral. Reproduce the bug or encode the new requirement as a test that fails now — that test *is* your definition of done.
5. **Implement the smallest change that satisfies the goal.** No speculative abstraction, no unrequested features, no drive-by refactors. Every changed line should trace to the task. Touch only what you must; clean up only the orphans your own change created.
6. **Verify for real.** Run the test, the build, and the linter — and read the actual output. "Should pass" is not "passes." If something fails and blocks the real path, say so and try another route; never fabricate output or declare success you didn't observe.
7. **Self-review the diff as a hostile reviewer.** Re-read your own change end to end: edge cases, error paths, resource cleanup, the siblings you might have missed, and whether you introduced inconsistency. Fix what you find before claiming done.
8. **Loop until the criteria are met,** then stop. State plainly what you changed, what you ran, and what the output showed. If a step was skipped or a test still fails, say that — an honest blocker beats a hidden one.

## Quick Reference
```
goal      → one checkable success criterion (not "make it work")
plan      → numbered steps, each with a verify: clause
test      → failing test first for behavioral goals
implement → smallest change; every line traces to the task
verify    → run build + tests + lint; READ the output
review    → reread the diff as an adversary; fix edge/error/siblings
report    → what changed · what ran · what the output showed
```
Pairs well with the [[writing-plans]], [[test-driven-development]], [[debugging]], and [[code-review]] skills — this is the spine that sequences them.

## Pitfalls
- Declaring victory on "it should work" without running anything — the most common failure mode.
- Over-building: abstractions, config, and error handling for cases nobody asked about. If 200 lines could be 50, it's wrong.
- Scope creep into adjacent code — refactoring or "improving" things the task didn't ask for, bloating the diff and the risk.
- Fixing one instance of a bug and missing its siblings (search for the pattern, not the line).
- Fabricating plausible output instead of reporting a real blocker. Honesty about failure is a feature.
- Skipping the self-review because the tests passed — tests catch what you thought of, review catches what you didn't.

## Verification
You're done only when: the success criterion is met and demonstrated by real tool output; the test/build/lint are green and you saw them go green; the diff contains nothing the task didn't require; and your final summary states what you changed, what you ran, and what it showed — with any remaining gap named explicitly.
