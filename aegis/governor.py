"""Cost & latency governor — spend caps + automatic model downshift.

Two jobs, both opt-in (``budget.*``):

* **Caps.** Track spend over a rolling window (reusing :mod:`aegis.usage_log`) and compare
  to ``budget.daily_usd`` / ``budget.session_usd``. The surface can warn or refuse a turn
  before it runs, so a runaway loop can't quietly burn the budget.
* **Auto-downshift.** Classify each turn as *simple* or *hard* (a cheap heuristic) and route
  trivial turns to ``budget.cheap_model`` while hard/ambiguous ones stay on the strong model
  — most of the cost cut, invisibly, with no quality loss on the turns that matter.

The decision functions are pure and deterministic; the agent/CLI wire them in. Nothing here
calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Signals that a turn is non-trivial and should stay on the strong model. Leading word
# boundary only (these are stems: "optimiz" → optimize/optimizing) and over-matching to
# "hard" is the safe bias — it just means no downshift.
_HARD_HINTS = re.compile(
    r"\b(refactor|architect|design|debug|why|investigat|root cause|optimiz|"
    r"concurren|race condition|security|migrat|algorithm|prove|derive|trade-?off|"
    r"plan|strateg|complex|implement|rewrite|redesign)", re.I)
# Cheap acknowledgement-style turns.
_SIMPLE_HINTS = re.compile(
    r"\b(rename|typo|format|lint|comment|bump|version|list|show|print|"
    r"what is|where is|add a test|spelling|docstring)\b", re.I)

_SIMPLE_MAX_CHARS = 240
_SIMPLE_MAX_LINES = 4


def classify_complexity(prompt: str) -> str:
    """Heuristically classify a turn as ``"simple"`` or ``"hard"``.

    Hard wins ties: it's cheaper to over-spend on a strong model than to botch a hard task
    on a weak one. A turn is simple only when it's short, single-step, and free of any
    hard-work signal (and ideally carries an explicit simple signal)."""
    text = (prompt or "").strip()
    if not text:
        return "simple"
    if _HARD_HINTS.search(text):
        return "hard"
    if len(text) > _SIMPLE_MAX_CHARS or text.count("\n") + 1 > _SIMPLE_MAX_LINES:
        return "hard"
    if "```" in text or "    " in text:          # embedded code/blocks → treat as hard
        return "hard"
    if _SIMPLE_HINTS.search(text):
        return "simple"
    # Short, no signal either way: lean simple only for very short asks.
    return "simple" if len(text) <= 80 else "hard"


def downshift_model(prompt: str, config) -> str:
    """The cheap model to use for this turn, or "" to keep the default.

    Returns a model name only when ``budget.auto_downshift`` is on, a ``budget.cheap_model``
    is configured, and the turn classifies as simple."""
    if config is None or not bool(config.get("budget.auto_downshift", False)):
        return ""
    cheap = str(config.get("budget.cheap_model", "") or "")
    if not cheap:
        return ""
    return cheap if classify_complexity(prompt) == "simple" else ""


def spend_window(config, days: int = 1) -> float:
    """Total USD spent over the last ``days`` days (reuses the usage log)."""
    try:
        from .usage_log import cost_report
        return float(cost_report(days=days, config=config).get("total_cost_usd", 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


@dataclass
class BudgetStatus:
    enabled: bool
    daily_spend: float
    daily_cap: float
    session_spend: float
    session_cap: float
    enforce: str            # "off" | "warn" | "block"

    @property
    def over_daily(self) -> bool:
        return self.daily_cap > 0 and self.daily_spend >= self.daily_cap

    @property
    def over_session(self) -> bool:
        return self.session_cap > 0 and self.session_spend >= self.session_cap

    @property
    def over(self) -> bool:
        return self.over_daily or self.over_session

    @property
    def should_block(self) -> bool:
        return self.enforce == "block" and self.over

    @property
    def warning(self) -> str:
        if not self.over:
            # near-cap nudge at 80%
            if self.daily_cap > 0 and self.daily_spend >= 0.8 * self.daily_cap:
                return (f"budget: ${self.daily_spend:.2f}/${self.daily_cap:.2f} today "
                        f"({self.daily_spend / self.daily_cap:.0%})")
            return ""
        parts = []
        if self.over_daily:
            parts.append(f"daily ${self.daily_spend:.2f} ≥ ${self.daily_cap:.2f}")
        if self.over_session:
            parts.append(f"session ${self.session_spend:.2f} ≥ ${self.session_cap:.2f}")
        verb = "blocked" if self.enforce == "block" else "over budget"
        return f"budget {verb}: " + ", ".join(parts)


def budget_status(config, *, session_spend: float = 0.0) -> BudgetStatus:
    enabled = bool(config.get("budget.enabled", False)) if config else False
    daily_cap = float(config.get("budget.daily_usd", 0) or 0) if config else 0.0
    session_cap = float(config.get("budget.session_usd", 0) or 0) if config else 0.0
    enforce = str(config.get("budget.enforce", "warn") or "warn") if config else "warn"
    daily = spend_window(config, days=1) if enabled and daily_cap > 0 else 0.0
    return BudgetStatus(enabled=enabled, daily_spend=round(daily, 4), daily_cap=daily_cap,
                        session_spend=round(session_spend, 4), session_cap=session_cap,
                        enforce=enforce if enabled else "off")


def cmd_budget(args, config) -> int:
    """`aegis budget [status]` — live spend vs caps."""
    st = budget_status(config)
    print(f"budget governor: {'ON' if st.enabled else 'off'}  (enforce={st.enforce})")
    cap = f"${st.daily_cap:.2f}" if st.daily_cap > 0 else "—"
    print(f"  today:   ${st.daily_spend:.2f} / {cap}")
    if st.session_cap > 0:
        print(f"  session cap: ${st.session_cap:.2f}")
    print(f"  auto-downshift: {'on' if config.get('budget.auto_downshift', False) else 'off'}"
          + (f" → {config.get('budget.cheap_model')}" if config.get("budget.cheap_model") else ""))
    if st.warning:
        print(f"  ⚠ {st.warning}")
    return 0
