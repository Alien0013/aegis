---
name: debugging
description: Systematically debug failing code: reproduce, isolate, add instrumentation, form and test hypotheses, fix, and verify. Use when something is broken and the cause is unclear.
version: 1.0.0
metadata:
  category: debugging
  tags: [debug, troubleshoot, root-cause, testing]
---

## When to Use
Code is broken (crash, wrong output, flaky, hang) and the cause is not obvious. Skip the guessing; follow the loop below until the root cause is proven, not assumed.

## Procedure
1. **Reproduce reliably.** Find the smallest command/input that triggers the bug. Run it with `bash` and capture exact error text, stack trace, and exit code. If intermittent, run in a loop to measure failure rate.
2. **Read the evidence.** `read_file` the failing frame top-down. The first error in the log is usually the real one; later errors are often cascades. Note line numbers, not vibes.
3. **Isolate.** Bisect to narrow scope: comment out / git-bisect / binary-search inputs until the failing surface is minimal. Confirm it still repros.
4. **Instrument.** Add temporary logging at the boundary between "known good" and "known bad" — print actual values, types, and shapes (not what you assume them to be). Use `edit_file` to insert; use `execute_code` for quick isolated checks.
5. **Form ONE hypothesis.** Write it as a falsifiable statement: "X is null because Y returns early." Predict what the instrumentation will show if true.
6. **Test it.** Run again. If the prediction fails, the hypothesis is wrong — discard it, do not patch around it. Return to step 4 with new data.
7. **Fix at the root.** Change the actual cause, not the symptom. Avoid defensive `try/except` that hides it.
8. **Verify** (see below), then **remove all instrumentation** you added.

## Quick Reference
```bash
cmd 2>&1 | tee /tmp/dbg.log          # capture stdout+stderr
for i in $(seq 20); do cmd || echo "FAIL $i"; done   # flakiness rate
git bisect start BAD GOOD             # find the breaking commit
python -X faulthandler ... ; PYTHONBREAKPOINT=ipdb.set_trace  # py
node --inspect-brk ; node --stack-trace-limit=100            # node
```

## Pitfalls
- Patching the symptom (e.g. swallowing an exception) instead of the cause.
- Changing several things at once — you lose the signal of what fixed it.
- Trusting assumptions over printed reality (wrong type, stale cache, wrong env/file).
- Editing without a repro — you can't tell if you fixed anything.
- Leaving debug prints, breakpoints, or loosened timeouts in the final diff.

## Verification
- Original repro from step 1 now passes; run it 3+ times (or the loop) to confirm.
- Add a regression test that fails on the OLD code and passes on the new.
- Run the surrounding test suite to confirm no new breakage.
- Confirm the diff contains only the fix — no leftover instrumentation.
