"""Verified self-improvement — keep a change only if it doesn't regress the score.

AEGIS already has two halves of a learning loop: the :mod:`aegis.curator` proposes
skill/memory changes, and :mod:`aegis.bench` measures end-to-end task success. This
module connects them. The core primitive is :func:`verify_change`:

    1. measure a baseline score
    2. apply the candidate change
    3. measure again
    4. keep iff the score held or improved; otherwise revert

That turns "the curator wrote a skill and hoped it helped" into "the curator wrote a
skill, proved it didn't make things worse, else rolled it back." Every experiment is
appended to ``improvements.jsonl`` so the trajectory of the harness is auditable.

Scoring is provider-dependent (it runs the benchmark suite live), so the whole loop is
opt-in (``curator.verify_with_evals``, default off) and the scorer is injectable — the
primitive itself is fully deterministic and testable without a model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import config as cfg
from .util import now_iso, read_text

Scorer = Callable[[], float]

_EPS = 1e-9


@dataclass
class Experiment:
    name: str
    baseline: float
    candidate: float
    delta: float
    kept: bool
    reason: str
    at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _log_path() -> Path:
    return cfg.sub("improvements.jsonl")


def record_experiment(exp: Experiment) -> None:
    """Append an experiment to the audit log (best-effort)."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(exp.to_dict()) + "\n")
    except OSError:
        pass


def list_experiments(limit: int = 20) -> list[dict]:
    raw = read_text(_log_path()) or ""
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return rows[-limit:][::-1]


def verify_change(apply: Callable[[], object], revert: Callable[[], object], *,
                  scorer: Scorer, min_delta: float = 0.0, name: str = "change",
                  record: bool = True) -> Experiment:
    """Apply ``apply`` only if it keeps the score within ``min_delta`` of baseline.

    ``min_delta`` is how much the score must *improve* to be kept (0.0 = "must not
    regress"). On regression — or if the scorer raises after applying — ``revert`` is
    called and the change is discarded. Returns the recorded :class:`Experiment`.
    """
    baseline = float(scorer())
    apply()
    try:
        candidate = float(scorer())
    except Exception as exc:  # noqa: BLE001 — a broken candidate must not be kept
        revert()
        exp = Experiment(name, baseline, baseline, 0.0, False,
                         f"reverted: scorer failed after apply ({type(exc).__name__}: {exc})",
                         now_iso())
        if record:
            record_experiment(exp)
        return exp
    delta = candidate - baseline
    kept = candidate + _EPS >= baseline + min_delta
    if not kept:
        revert()
    reason = (f"kept: score {baseline:.3f} -> {candidate:.3f} (Δ{delta:+.3f})" if kept
              else f"reverted: score {baseline:.3f} -> {candidate:.3f} (Δ{delta:+.3f}) "
                   f"below threshold +{min_delta:.3f}")
    exp = Experiment(name, round(baseline, 4), round(candidate, 4), round(delta, 4),
                     kept, reason, now_iso())
    if record:
        record_experiment(exp)
    return exp


def bench_scorer(config, root: str | Path | None = None) -> Scorer:
    """A scorer that runs the benchmark suite live and returns its pass rate in [0,1]."""
    from .bench import suite_score

    return lambda: suite_score(root, config=config)


def scorer_available(config, root: str | Path | None = None) -> bool:
    """True when there is at least one benchmark task to score against."""
    from .bench import _default_root, discover_tasks

    base = Path(root).expanduser() if root else _default_root(config)
    try:
        return bool(discover_tasks(base))
    except OSError:
        return False


def verified_curator_review(config, *, scorer: Scorer | None = None,
                            min_delta: float = 0.0) -> dict:
    """Run the curator's aux-model skill review, but roll skills/ back if it regresses
    the benchmark score. Falls back to a plain review when no scorer is available."""
    from . import curator

    score = scorer or bench_scorer(config)
    if scorer is None and not scorer_available(config):
        # Nothing to measure against — behave exactly like the unguarded review.
        return {"verified": False, "reason": "no benchmark tasks", **curator.llm_review(config)}

    snapshot = curator.backup(reason="pre-verify-review")
    snap_id = snapshot.parent.name if snapshot else None

    result: dict = {}

    def apply() -> None:
        result.update(curator.llm_review(config))

    def revert() -> None:
        if snap_id:
            curator.rollback(snap_id)

    exp = verify_change(apply, revert, scorer=score, min_delta=min_delta,
                        name="curator_review")
    return {"verified": True, "experiment": exp.to_dict(), **result,
            "kept": exp.kept, "reason": exp.reason}


def cmd_improve(args, config) -> int:
    """`aegis improve [run|log]` — run a verified curator review, or show the log."""
    action = getattr(args, "action", "log") or "log"
    if action == "log":
        rows = list_experiments(limit=getattr(args, "limit", 20))
        for r in rows:
            mark = "kept   " if r.get("kept") else "reverted"
            print(f"  {mark} {r.get('name'):<18} {r.get('reason', '')}")
        if not rows:
            print("(no self-improvement experiments yet)")
        return 0
    if not scorer_available(config):
        print("no benchmark tasks found — add benchmarks/<name>/task.yaml first "
              "(see `aegis bench list`).")
        return 1
    print("running verified curator review (this runs the benchmark suite live)…")
    res = verified_curator_review(config, min_delta=float(getattr(args, "min_delta", 0.0) or 0.0))
    print(res.get("reason", json.dumps(res, indent=2)))
    return 0
