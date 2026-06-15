"""gstack — a built-in sprint orchestrator (Garry Tan's gstack, in-harness).

gstack's idea: a solo developer gets a whole engineering org by having one model
switch specialized roles through a sprint. We implement that natively (not as a
skill): one agent runs a goal through a fixed sprint — think → plan → build →
review → test → ship → reflect — sharing a single session so each phase builds on
the last. See https://github.com/garrytan/gstack.

The agent run is injectable (``runner``) so the sprint logic is testable without a
live model; the default runner drives the real AegisClient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# (prompt, session_id, cwd) -> (assistant_text, session_id)
Runner = Callable[[str, "str | None", "Path | None"], "tuple[str, str]"]


@dataclass
class Phase:
    name: str
    role: str
    instruction: str


# The sprint, in order. Each phase reframes the same agent as a different role.
PHASES: list[Phase] = [
    Phase("think", "Founder",
          "Restate the goal in your own words. Clarify scope, list explicit non-goals, "
          "and define concrete, verifiable success criteria. Surface risks and open "
          "questions. Do not write code yet."),
    Phase("plan", "Engineering Manager",
          "Turn the goal and success criteria into a concrete, ordered implementation "
          "plan. Every step must name what changes and how it will be verified. Keep it "
          "minimal — no speculative work or unrequested features."),
    Phase("build", "Engineer",
          "Implement the plan now. Make the smallest set of changes that satisfies the "
          "success criteria, matching existing conventions. Use your tools to edit files "
          "and run commands."),
    Phase("review", "Code Reviewer",
          "Critically review what was just built against the plan and success criteria. "
          "List concrete issues, risks, and gaps, then fix anything clearly wrong or unsafe."),
    Phase("test", "QA Engineer",
          "Verify the work. Add or run tests that exercise the success criteria and the "
          "important edge cases. Report exactly what passed and what failed, with output."),
    Phase("ship", "Release Manager",
          "Make it shippable: confirm tests pass, summarize the full change set, and "
          "prepare a clear commit message. Do NOT commit or push unless explicitly asked."),
    Phase("reflect", "Doc Engineer",
          "Document what changed and why (update docs/README if relevant) and note "
          "lessons learned for next time."),
]
PHASE_NAMES = [p.name for p in PHASES]


@dataclass
class SprintResult:
    goal: str
    session_id: str = ""
    outputs: list[tuple[str, str]] = field(default_factory=list)  # (phase_name, text)


def select_phases(names: list[str] | None = None, start: str | None = None) -> list[Phase]:
    """Resolve which phases to run: an explicit subset (``names``) and/or a
    starting phase (``start``, e.g. resume a sprint from 'build')."""
    phases = PHASES
    if names:
        wanted = {n.strip() for n in names if n.strip()}
        phases = [p for p in PHASES if p.name in wanted]
    if start:
        idx = next((i for i, p in enumerate(phases) if p.name == start), 0)
        phases = phases[idx:]
    return phases or list(PHASES)


def _phase_prompt(phase: Phase, goal: str, first: bool) -> str:
    head = f"You are acting as the **{phase.role}** in a gstack sprint (phase: {phase.name}).\n\n"
    if first:
        head += f"GOAL:\n{goal}\n\n"
    else:
        head += "Continue the same sprint — the goal and the prior phases are above in this conversation.\n\n"
    return head + phase.instruction


def repl_sprint_prompt(goal: str, phases: list[Phase] | None = None) -> str:
    """A single-turn sprint prompt for the REPL: the agent works through all phases
    in one turn, switching roles, rather than across separate sessions (the CLI form)."""
    phases = phases or list(PHASES)
    steps = "\n".join(f"{i + 1}. **{p.name}** ({p.role}): {p.instruction}"
                      for i, p in enumerate(phases))
    return (
        "<system-reminder>Run a gstack sprint on the goal below. Work through every phase "
        "in order during this turn, switching roles as you go, and do the real work — make "
        "the actual edits, run commands and tests — don't just describe it. Announce each "
        "phase with a '### <PHASE> · <role>' header. Do not commit or push unless asked."
        "</system-reminder>\n\n"
        f"GOAL: {goal}\n\nSPRINT PHASES:\n{steps}"
    )


def _default_runner(config, cwd: Path | None) -> tuple[Runner, Callable[[], None]]:
    from .sdk import AegisClient

    client = AegisClient(config=config, cwd=cwd)

    def run(prompt: str, session_id: str | None, run_cwd: Path | None) -> tuple[str, str]:
        res = client.run(prompt, session_id=session_id, cwd=run_cwd, auto=True, expand_refs=False)
        text = res.message.content if res.message else ""
        return text, (res.session_id or session_id or "")

    return run, client.close


def run_sprint(goal: str, config, *, runner: Runner | None = None,
               phases: list[Phase] | None = None, cwd: Path | None = None,
               on_phase: Callable[[Phase, str, str], None] | None = None) -> SprintResult:
    """Run ``goal`` through the sprint, threading one session across phases.

    ``on_phase(phase, state, text)`` is called with state 'start' (text="") before
    each phase and 'done' (text=output) after it — used for live CLI/UI progress.
    """
    phases = phases or list(PHASES)
    close: Callable[[], None] | None = None
    if runner is None:
        runner, close = _default_runner(config, cwd)
    result = SprintResult(goal=goal)
    sid: str | None = None
    try:
        for i, phase in enumerate(phases):
            if on_phase:
                on_phase(phase, "start", "")
            text, sid = runner(_phase_prompt(phase, goal, first=(i == 0)), sid, cwd)
            result.outputs.append((phase.name, text))
            result.session_id = sid or result.session_id
            if on_phase:
                on_phase(phase, "done", text)
    finally:
        if close:
            close()
    return result


def cmd_gstack(args, config) -> int:
    """``aegis gstack <goal> [--phases think,plan,build] [--from build] [--dry]``."""
    goal = " ".join(getattr(args, "goal", []) or []).strip()
    names = (getattr(args, "phases", "") or "").split(",") if getattr(args, "phases", "") else None
    phases = select_phases(names, getattr(args, "from_phase", None))

    if getattr(args, "dry", False) or not goal:
        if not goal:
            print("usage: aegis gstack <goal> [--phases ...] [--from PHASE] [--dry]")
        print("gstack sprint phases:")
        for p in phases:
            print(f"  {p.name:<8} — {p.role}")
        return 0 if goal else 1

    def on_phase(phase: Phase, state: str, text: str) -> None:
        if state == "start":
            print(f"\n=== {phase.name.upper()} · {phase.role} ===", flush=True)
        elif text:
            print(text.rstrip(), flush=True)

    res = run_sprint(goal, config, phases=phases, cwd=Path.cwd(), on_phase=on_phase)
    print(f"\ngstack sprint complete · {len(res.outputs)} phases · session {res.session_id}")
    return 0
