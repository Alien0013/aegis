---
name: regex
description: Build, explain, and test regular expressions for a precise matching task; verify against examples. Use when crafting or debugging a regex.
version: 1.0.0
metadata:
  category: text
  tags: [regex, matching, validation]
---

## When to Use
- Crafting a new pattern to match/extract/replace text from a precise spec.
- Debugging a regex that over- or under-matches.
- Explaining what an existing pattern does, token by token.

## Procedure
1. Collect concrete examples FIRST: list strings that MUST match and strings that MUST NOT. Without both, ask the user for them.
2. Identify the engine/flavor (PCRE, Python `re`, JS, Go RE2, grep BRE/ERE). Anchoring, lookaround, and `\d` semantics differ. RE2/Go has no lookaround/backreferences.
3. Draft the pattern incrementally: anchor (`^...$`) only if matching the whole string; build left to right per token.
4. Prefer specific classes over `.*`: use `[^/]+`, `\d{1,3}`, non-greedy `*?` to avoid runaway matches.
5. Test against EVERY example with execute_code (see Quick Reference). Iterate until all positives match and all negatives reject.
6. For debugging: isolate the failing token by removing parts until behavior changes; that localizes the bug.
7. Deliver the final pattern, a one-line plain-English explanation, and the passing test output.

## Quick Reference
```python
# execute_code — verify a pattern against labelled examples
import re
pat = r"^\d{4}-\d{2}-\d{2}$"
must  = ["2026-06-07"]
must_not = ["2026-6-7", "x2026-06-07"]
print([s for s in must if not re.search(pat, s)])      # should be []
print([s for s in must_not if re.search(pat, s)])      # should be []
```
- Shell test: `grep -nE 'PATTERN' file` (ERE) or `rg 'PATTERN'`.
- Common tokens: `\b` word boundary, `(?:...)` non-capture, `(?=...)` lookahead, `\1` backref, `(?i)` inline ignorecase.

## Pitfalls
- `.` matches any char incl. literal dots — escape as `\.`.
- Greedy `.*` swallows too much; use `.*?` or a negated class.
- Forgetting `^`/`$` lets partial matches sneak through validation.
- Unescaped metachars `. * + ? ( ) [ ] { } | ^ $ \`.
- Flavor drift: lookbehind/backrefs unsupported in RE2; `\d` may include Unicode digits in some engines.
- Raw strings: use `r"..."` in Python so `\` is not double-escaped.

## Verification
- All MUST-match examples match; all MUST-NOT examples are rejected (both list comprehensions print `[]`).
- Add one tricky edge case beyond the user's examples and confirm expected behavior.
