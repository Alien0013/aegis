---
name: performance-profiling
description: Find and fix a performance bottleneck with evidence — measure first, profile to locate the hot path, optimize the real cost, then prove the speedup. Use when code is slow, memory-heavy, or doesn't scale.
version: 1.0.0
metadata:
  category: performance
  tags: [performance, profiling, optimization, latency, memory]
---

## When to Use
Something is too slow, uses too much memory, or degrades under load. Optimize by **measurement, not intuition** — the slow part is rarely where you'd guess. Never optimize without a before-number to beat.

## Procedure
1. **Set the target.** Define the metric and the goal: "p95 request latency from 800ms to <200ms," "import 1M rows in under 30s," "peak RSS under 512MB." A goal you can measure is a goal you can finish.
2. **Reproduce + baseline.** Build a repeatable benchmark (fixed input, warm cache vs cold stated). Run it 3+ times; record the number. This is what every change is measured against.
3. **Profile, don't stare.** Use a real profiler to find where time/memory actually goes. Look for the dominant cost — by Amdahl's law, optimizing a 3% function can't help; the 60% one can.
4. **Identify the class of waste.** Usually one of: an N+1 / accidental O(n²) loop, repeated work that should be cached, a chatty I/O pattern (per-row DB calls, sync network in a loop), unnecessary allocation/copying, or missing batching/parallelism.
5. **Change one thing.** Fix the single biggest cost. Re-run the benchmark. Keep the change only if the number actually moved; revert if it didn't (intuition lies).
6. **Re-profile.** The bottleneck moves after each fix. Repeat from step 3 until you hit the target or the curve flattens.
7. **Stop at "good enough."** More speed past the target trades readability for nothing. Document the benchmark so regressions are catchable.

## Quick Reference
```bash
# python
python -m cProfile -s cumtime prog.py | head -30
python -m pyinstrument prog.py            # statistical, readable tree
python -m memory_profiler / tracemalloc   # allocations
# node
node --prof app.js && node --prof-process isolate-*.log | head -40
node --cpu-prof app.js                    # flamegraph in DevTools
# system / db
hyperfine 'cmd'                           # honest wall-clock benchmark
EXPLAIN ANALYZE <query>                    # the slow query's real plan
```

## Pitfalls
- Optimizing without a profiler — you "fix" a function that wasn't the bottleneck.
- Micro-optimizing a tiny fraction of total time (premature optimization).
- Benchmarking once — noise looks like a result. Run multiple times; watch variance.
- Confusing latency with throughput; improving one can worsen the other.
- Caching as a first move — it hides the cost and adds invalidation bugs. Fix the algorithm first.
- Leaving the change in without confirming it beat the baseline.

## Verification
- The target metric is met, shown as **before → after** numbers from the same benchmark.
- The benchmark is committed (or scripted) so the win is reproducible and regressions are caught.
- Correctness is unchanged: the existing test suite still passes on the optimized code.
- No readability/safety regressions snuck in under the banner of speed.
