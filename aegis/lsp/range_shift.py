"""Diff-aware line-shift map for the edit-diagnostics delta filter.

An edit that inserts or deletes lines shifts every diagnostic below it; without
adjustment, shifted-but-identical baseline diagnostics look brand-new and flood
the agent. Same trick as unified diff: build a piecewise map from pre-edit to
post-edit line numbers and remap the baseline before the set difference.
Diagnostics on deleted lines map to None and drop out (they no longer apply).
"""

from __future__ import annotations

import difflib
from typing import Callable


def build_line_shift(pre_text: str, post_text: str) -> Callable[[int], int | None]:
    """Map pre-edit 0-indexed line numbers to post-edit ones (None = line deleted)."""
    pre = pre_text.splitlines() if pre_text else []
    post = post_text.splitlines() if post_text else []
    if pre == post:
        return lambda line: line
    opcodes = difflib.SequenceMatcher(a=pre, b=post, autojunk=False).get_opcodes()

    def shift(line: int) -> int | None:
        for tag, i1, i2, j1, _j2 in opcodes:
            if i1 <= line < i2:
                return line - i1 + j1 if tag == "equal" else None
            if line < i1:
                break
        return max(0, len(post) - 1) if post else None

    return shift


def shift_baseline(baseline: list[dict], shift: Callable[[int], int | None]) -> list[dict]:
    """Remap every baseline diagnostic's range; drop ones whose line was deleted."""
    out: list[dict] = []
    for d in baseline:
        rng = (d.get("range") or {})
        start = rng.get("start") or {}
        end = rng.get("end") or {}
        s = shift(int(start.get("line", 0)))
        if s is None:
            continue
        e = shift(int(end.get("line", start.get("line", 0))))
        shifted = dict(d)
        shifted["range"] = {
            "start": {"line": s, "character": int(start.get("character", 0))},
            "end": {"line": e if e is not None else s,
                    "character": int(end.get("character", 0))},
        }
        out.append(shifted)
    return out


def diag_key(d: dict) -> tuple:
    """Identity for the baseline set-difference (severity, code, source, message, range)."""
    rng = d.get("range") or {}
    start = rng.get("start") or {}
    return (d.get("severity"), str(d.get("code")), d.get("source"),
            d.get("message"), int(start.get("line", 0)), int(start.get("character", 0)))
