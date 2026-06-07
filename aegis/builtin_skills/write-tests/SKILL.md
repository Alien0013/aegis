---
name: write-tests
description: Write thorough automated tests (unit/integration) for code: cover happy paths, edge cases, and failure modes; make them deterministic. Use when asked to add or improve tests.
version: 1.0.0
metadata:
  category: testing
  tags: [tests, unit, integration, coverage]
---

## When to Use
When asked to add or improve automated tests for a function, module, or feature — including raising coverage, reproducing a bug, or hardening flaky/non-deterministic tests.

## Procedure
1. Detect the stack: use bash to find the runner/config (e.g. `pytest.ini`/`pyproject.toml`, `package.json` jest/vitest, `go test`, `cargo test`). Match the existing framework and conventions.
2. read_file the code under test plus an existing test file to copy style, fixtures, and import patterns.
3. Establish a baseline: run the suite with bash so you know what passes before you touch anything.
4. Enumerate behaviors to cover, then write tests with write_file/edit_file for each:
   - Happy path: typical valid inputs and expected outputs.
   - Edge cases: empty/null, zero, boundaries (min/max, off-by-one), large input, unicode, duplicates.
   - Failure modes: invalid input raises the right error/type; assert message/exception, not just "raises".
5. Make it deterministic: freeze time, seed RNG, mock network/filesystem/clock, avoid sleeps and real I/O. One logical assertion per test; descriptive names (`test_<unit>_<condition>_<expected>`).
6. For a bug fix: write the failing test that reproduces it FIRST, confirm it fails, then verify the fix flips it green.
7. Run the new tests with bash; iterate until green. Re-run the full suite to confirm no regressions.
8. Check coverage if a tool exists (`pytest --cov`, `jest --coverage`); fill meaningful gaps, not just lines.

## Quick Reference
- Python: `pytest -q`, `pytest path::test -x -vv`, `pytest --cov=pkg`, `@pytest.mark.parametrize`, `monkeypatch`, `freezegun`.
- JS: `npx vitest run` / `npx jest`, `jest --coverage`, `vi.useFakeTimers()`, `jest.mock()`.
- Go: `go test ./... -run TestX -v`; Rust: `cargo test`.

## Pitfalls
- Tests coupled to implementation detail (mocking internals) instead of observable behavior.
- Asserting only "no error" — assert actual values and error types/messages.
- Hidden order-dependence/shared state between tests; run in isolation to catch it.
- Real network/clock/random making tests flaky.
- Over-mocking until the test no longer exercises real logic.

## Verification
- New tests fail before the fix/feature and pass after.
- Full suite passes with no regressions; running twice and in isolation gives identical results.
- Edge and failure cases are present, not just the happy path.
