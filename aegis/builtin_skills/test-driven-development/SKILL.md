---
name: test-driven-development
description: Enforce TDD — write a failing test first, watch it fail, then write the minimum code to pass. Use for new features, bug fixes, and behavior changes.
version: 1.0.0
metadata:
  category: development
  tags: [testing, tdd, red-green-refactor, quality]
  license: MIT
---

## When to Use
Any new feature, bug fix, refactor, or behavior change. Skip only for throwaway
prototypes, generated code, or pure config — and say so out loud when you skip.

If you catch yourself thinking "I'll skip the test just this once," stop — that's
the rationalization TDD exists to prevent.

## The Iron Law
**If you didn't watch the test fail, you don't know that it tests the right thing.**

## Procedure (RED → GREEN → REFACTOR)
1. **RED** — write one small test for the next behavior. Run it. Confirm it fails,
   and that it fails *for the expected reason* (assertion, not an import error).
2. **GREEN** — write the minimum code to make it pass. No extra features, no
   speculative abstraction. Run the test; confirm it passes.
3. **REFACTOR** — clean up names/duplication with the test as your safety net.
   Re-run; stay green.
4. Repeat for the next behavior. Commit at green points.

## For bug fixes
Reproduce the bug as a **failing test first**, then fix until it passes. That test
becomes a permanent regression guard.

## Verify
- Every new behavior has a test that was seen to fail before it passed.
- The suite is green before you call the work done.
- No production code exists that no test exercises.
