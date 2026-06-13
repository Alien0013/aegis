---
name: incident-response
description: Triage and resolve a production incident — establish impact, stabilize first, find the cause, then write a blameless postmortem. Use when something is down, degraded, or erroring in production.
version: 1.0.0
metadata:
  category: ops
  tags: [incident, oncall, sre, outage, postmortem]
---

## When to Use
Production is broken or degraded: users affected, alerts firing, error rate up, latency spiking. The goal order is **stop the bleeding → find the cause → prevent recurrence** — in that order. Restoring service beats a perfect diagnosis.

## Procedure
1. **Declare and size it.** State impact in one line: *what* is broken, *who* is affected, *since when*. Pick a severity (full outage vs. partial/degraded). This sets urgency and who to pull in.
2. **Stabilize before diagnosing.** If a fast mitigation exists, do it now: roll back the last deploy, disable the bad feature flag, scale up, fail over, or shed load. You can understand *why* later — users can't wait.
3. **Build a timeline.** Pull the recent change history (`git log`, deploy log, config/flag changes, infra events) and line it up against when the alert started. The cause is almost always a recent change.
4. **Read the signals.** Check the four golden signals — latency, traffic, errors, saturation. Look at logs and dashboards *around the start time*, not now. The first anomaly is the lead; later ones are usually downstream.
5. **Form one hypothesis, test cheaply.** "Errors began at 14:32, two minutes after deploy abc123 changed the DB pool size." Verify against the timeline before acting.
6. **Apply the real fix** once stable, with a reviewer if possible. Avoid risky changes mid-incident.
7. **Communicate.** Post a short status at declare, at mitigation, and at resolution. Say what's known, what's being done, and the next update time.
8. **Write the postmortem** (see Verification) within a day, while memory is fresh.

## Quick Reference
```bash
git log --oneline -20 --since="3 hours ago"     # recent changes
kubectl rollout undo deploy/<name>               # fast rollback (k8s)
kubectl rollout history deploy/<name>            # what changed when
journalctl -u <svc> --since "30 min ago" -p err  # recent errors
# golden signals: error rate, p50/p95/p99 latency, RPS, CPU/mem/conns
```

## Pitfalls
- Diagnosing for 30 minutes while users are down instead of rolling back first.
- Changing several things at once during mitigation — you lose the signal of what helped.
- Blaming a person. Incidents are system failures; the fix is in the system (guardrails, tests, alerts), not the human.
- No timeline — you end up debating from memory instead of evidence.
- Declaring "resolved" on a hunch without confirming the signals actually recovered.

## Verification
- The originating signal (error rate / latency / availability) is back to baseline and stays there for a sustained window, not just one good data point.
- A clear single line of impact and a timeline exist.
- Postmortem captures: impact, timeline, root cause, **why detection/mitigation took as long as it did**, and concrete action items with owners (prefer a guardrail or automated check over "be more careful").
- At least one follow-up makes the same failure either impossible or auto-detected next time.
