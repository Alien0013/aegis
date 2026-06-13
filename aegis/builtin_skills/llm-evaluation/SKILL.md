---
name: llm-evaluation
description: Evaluate an LLM or agent rigorously — build a representative eval set, pick metrics that match the task, run baselines, and catch regressions. Use when comparing models/prompts or deciding if a change actually helped.
version: 1.0.0
metadata:
  category: mlops
  tags: [llm, evaluation, eval, metrics, benchmark, regression]
---

## When to Use
You're choosing between models, prompts, or agent changes and need evidence instead of vibes — or you want a regression gate so a "small" prompt edit can't quietly degrade quality. Eval is **measurement design**: a bad eval set lies confidently.

## Procedure
1. **Define the task and what "good" means.** One sentence on the job, and the failure modes you care about (wrong answer, hallucination, unsafe, off-format, too slow/expensive). You can't measure quality you haven't defined.
2. **Build a representative set.** 30–200 real, diverse cases — not cherry-picked easy ones. Include edge cases, adversarial inputs, and known-hard examples. Freeze it; version it. Keep a held-out slice you don't iterate against (so you don't overfit the prompt to the eval).
3. **Pick the cheapest valid metric:**
   - **Deterministic** (preferred): exact/regex match, JSON-schema validity, unit-test pass, numeric tolerance. Cheap, objective, reproducible.
   - **Reference-based:** similarity to a gold answer when there's one right answer.
   - **LLM-as-judge** (last resort): only for open-ended quality. Use a rubric, score one dimension at a time, validate the judge against human labels on a sample, and watch for bias (length, position, self-preference).
4. **Run a baseline first.** Current model/prompt is the number to beat. No baseline = no result.
5. **Hold everything else fixed.** Change one variable (model OR prompt OR temperature) per run, fixed seed/temperature where possible, same eval set. Report n, not one example.
6. **Report the full picture:** accuracy/score, **and** cost per case, latency, and failure breakdown by category. A 1% quality gain at 3× cost is usually a loss.
7. **Wire it as a gate.** Save the harness so it reruns on every prompt/model change and flags regressions.

## Quick Reference
```
Set:     30–200 cases · diverse · versioned · held-out slice
Metric:  deterministic > reference > LLM-judge (rubric + human-validated)
Run:     baseline first · one variable · fixed temp/seed · report n
Judge:   score 1 dimension at a time · randomize order · check for length/position bias
Report:  score · cost/case · p50/p95 latency · failures grouped by type
Pass@k / exact-match / schema-valid % / win-rate vs baseline
```

## Pitfalls
- Eyeballing 3 examples and declaring a winner — noise, not signal.
- Tiny or unrepresentative eval set; iterating the prompt against the *same* set until it overfits (no held-out slice).
- LLM-judge taken on faith — never validated against humans, biased toward longer or first-listed answers.
- Comparing runs while changing model + prompt + temperature at once.
- Reporting accuracy with no cost/latency, then shipping a 3×-pricier model for a rounding-error gain.
- Non-determinism ignored: same input, different output, treated as a stable measurement.

## Verification
- A versioned eval set and a re-runnable harness exist; rerunning the baseline reproduces its number.
- Results show **before → after** across the set with n, plus cost and latency, not a single anecdote.
- Any LLM-judge was spot-checked against human judgment on a sample and agreed.
- The winning change is confirmed on the **held-out** slice, not just the set you tuned against.
