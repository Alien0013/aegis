# AEGIS Production Agent Harness Plan

**Purpose:** turn AEGIS into a production-grade, local-first agent harness with an original implementation, consistent behavior across every surface, strong safety rails, and a polished desktop/dashboard experience.

**Rule:** build capability-equivalent systems in AEGIS's own architecture and naming. Do not copy private prompts, secrets, branding, or implementation text from any external agent. Public ideas are fine; code and product surface must be original AEGIS code.

---

## Current State Verified Locally

Repository:

```text
/home/alienai/aegis
origin: https://github.com/Alien0013/aegis.git
branch: main aligned with origin/main at 93c6f4b69bd78e73f0960081ea8b7e89229900a7
```

Existing local work that must be protected:

```text
 M .gitignore
 M README.md
?? site-next/
```

AEGIS already has strong foundations:

- `aegis/surface.py` centralizes CLI/API/ACP/dashboard/automation runs through `SurfaceRunner`.
- `aegis/agent/agent.py` wires provider, tools, memory, skills, permissions, MCP, and context.
- `aegis/agent/loop.py` has bounded tool loops, concurrent safe tool execution, compaction, tracing, spill-to-disk, checkpoints, and wakeups.
- `aegis/config.py` provides profile-aware config, env handling, workspace rules, and defaults.
- `aegis/session.py` provides SQLite sessions and FTS recall.
- `aegis/dashboard_fastapi.py` exposes a broad local dashboard/API surface.
- `desktop/electron/` contains an Electron shell and packaging/test scripts.
- `aegis/gateway/` contains messaging adapters and gateway orchestration.
- `aegis/builtin_skills/`, `aegis/skills.py`, and `aegis/curator.py` provide a skill/learning system.
- Local scan found 299 Python files, about 133k Python lines, and 94 `test_*.py` files.

Smoke verification:

```text
python -m pytest tests/test_smoke.py -q
26 passed in 0.93s
```

---

## Target Product Shape

AEGIS should become one coherent runtime across every surface:

1. **Terminal REPL** — streaming, slash commands, tool trail, interrupt/steer/retry, snapshots/diff/rollback, session resume/branch.
2. **One-shot CLI** — model/provider overrides, skill preloads, JSON output for automation.
3. **Dashboard/API** — chat, sessions, traces, runs, models/providers, tools/toolsets, skills/curator, memory, cron, gateway, profiles, files/logs/config, system health.
4. **Desktop shell** — secure Electron wrapper, backend lifecycle, token handoff, update/status surfaces, crash/restart resilience.
5. **Gateway** — messaging adapters, per-channel session keys, pairing/admin controls, restart/status commands.
6. **SDK/API/RPC** — Python SDK, OpenAI-compatible chat API, JSON-RPC stdio, ACP/IDE sessions, MCP server/client integration.
7. **Automation** — cron jobs, background tasks, webhooks, kanban workers, verified self-improvement benchmarks.

---

## Non-Negotiable Architecture Invariants

1. Every surface enters through `SurfaceRunner` or a thin wrapper that delegates to it.
2. CLI, dashboard, gateway, API, cron, and SDK write compatible session/run/trace records.
3. Built-ins, plugins, MCP tools, memory tools, and context-engine tools share one registry and one permission path.
4. Dangerous file/runtime/network/automation actions pass the same policy checks everywhere.
5. Session-start prompt blocks stay stable; dynamic context arrives through tool results, wakeups, retrieved memory, or explicit rebuilds.
6. No silent side effects: edits, shell commands, network calls, credentials, and external sends need policy gates and traceability.
7. Every behavior change lands with tests and a command that proves it.

---

## Gap Map

### A. Context and Prompt Runtime

Strengths:

- `ContextBuilder` tiers prompt parts.
- `coding_workspace_block()` gives a repo snapshot once per session.
- Workspace rules support layered project files.
- Tool output spill and compaction are present.

Gaps:

1. Dynamic subdirectory rule loading for monorepos.
2. Dashboard prompt-part audit view.
3. Stale-context warnings after large filesystem changes.
4. Per-surface context budget view.

First slice implemented:

- Dynamic subdirectory rule hints through tool results when a path-scoped tool first touches a subtree with local rule files.

Files:

- `aegis/agent/coding_context.py`
- `aegis/agent/loop.py`
- `tests/test_coding_context.py`

### B. Provider Runtime

Gaps:

1. Provider conformance tests for streaming, tool calls, auth failure, rate limits, malformed responses, and fallback.
2. Dashboard model picker should show auth status, context length, reasoning support, vision/tool support, and last probe.
3. Standard streaming error recovery across providers.
4. Per-provider cost/latency envelopes feeding budget downshift.

### C. Tool System

Gaps:

1. Tool schema validator over every registered tool.
2. Structured `data` for high-use tools that currently return only strings.
3. Permission simulator in dashboard.
4. Better plugin/MCP conflict diagnostics and provenance.

### D. Memory and Skills

Gaps:

1. Memory operation provenance records.
2. Skill create/install quality gate.
3. Skill usage dashboard.
4. Curator dry-run preview and approval UI.

### E. Sessions, Runs, and Tracing

Gaps:

1. Trace replay timeline: prompt parts, provider calls, tool calls, final answer.
2. Session lineage view for compression, branching, rollback, and replay.
3. Surface-specific run metadata schemas.
4. Eval replay diffs for tools, cost, latency, and failures.

### F. Desktop and Dashboard

Gaps:

1. Desktop backend lifecycle state machine: missing, starting, ready, degraded, crashed, updating.
2. Renderer connection status store with reconnect/backoff.
3. Terminal pane with PTY sessions, history, copy/paste, kill/restart.
4. Settings UI that separates config values, env secrets, inherited defaults, and profile overrides.
5. First-run checklist and production health score.

### G. Gateway and Automation

Gaps:

1. Adapter contract tests for every gateway platform.
2. Queue/backpressure visibility.
3. Delivery retry with idempotency and dead-letter records.
4. Gateway dashboard for routes, chats, pending approvals, recent failures.
5. Cron dry-run and next-run preview in dashboard.

### H. Release and Operations

Gaps:

1. One release-gate command validating Python, JS, desktop, docs, installer, and packaging checks.
2. Release manifest with hashes and artifact provenance.
3. Upgrade rollback test from packaged install.
4. Security audit JSON and Markdown reports.
5. CI enforcement for generated asset drift.

---

## Execution Phases

### Phase 0 — Protect the Workspace

- Always start with `git status --short --branch`.
- Identify user-owned dirty files.
- Avoid broad formatters on unrelated work.
- Do not touch `.gitignore`, `README.md`, or `site-next/` unless specifically targeted.

### Phase 1 — Context Runtime Completion

- Dynamic subdirectory rules. **Implemented.**
- Dashboard prompt-part inspection endpoint tests.
- Stale workspace snapshot warnings.
- Docs for stable/context/volatile prompt tiers in AEGIS terms.

Verification:

```bash
python -m pytest tests/test_coding_context.py -q
python -m pytest tests/test_net_and_edit.py::test_agents_md_layers_root_and_subdir -q
```

### Phase 2 — Provider and Model Production Matrix

- Create provider contract tests.
- Add provider capability metadata fields.
- Surface provider probe results in dashboard model inventory.
- Add failover trace spans.

Verification:

```bash
python -m pytest tests/test_providers.py tests/test_provider_model_inventory.py -q
```

### Phase 3 — Tool and Permission Control Plane

- Add a tool schema validator test over `default_registry().all()`.
- Add permission dry-run API and CLI command.
- Add dashboard route for per-tool allow/deny reasoning.
- Add source/provenance fields for plugin and MCP tools.

Verification:

```bash
python -m pytest tests/test_tools.py tests/test_tool_toggle.py tests/test_agent_perms.py -q
```

### Phase 4 — Dashboard/Desktop Production Shell

- Backend lifecycle state machine.
- Renderer connection status store.
- Dedicated pages for runs, traces, gateway, cron, profiles, and security health.
- Secure preload API audit.
- Packaged smoke test.

Verification:

```bash
cd desktop/electron
npm test
```

### Phase 5 — Memory, Skills, Learning, Curator

- Memory operation provenance.
- Skill quality gate.
- Skill usage dashboard.
- Curator dry-run preview.
- Learning review approval UI.

Verification:

```bash
python -m pytest tests/test_memory_lifecycle.py tests/test_skills_memory.py tests/test_self_learning.py -q
```

### Phase 6 — Gateway, Cron, Webhooks, Background Work

- Delivery retry/dead-letter store.
- Queue depth and backpressure metrics.
- Gateway route dashboard.
- Cron dry-run preview.
- Webhook tester with stored request samples.

Verification:

```bash
python -m pytest tests/test_gateway_inbound.py tests/test_gateway_status.py tests/test_cronjob_tool.py tests/test_wakeups.py -q
```

### Phase 7 — Release, Installer, Security Hardening

- Add `scripts/verify_release.py` as the single local release gate.
- Produce release manifest with hashes.
- Add installer rollback coverage.
- Add JSON/Markdown output to security audit.
- Check docs/screenshots for drift.

Verification:

```bash
bash scripts/run_tests.sh
python -m pytest tests/test_installers.py tests/test_packaging.py tests/test_hardening.py -q
```

---

## First Implementation Slice Details

### Dynamic subdirectory rules

Problem:

- AEGIS builds the system prompt once per session for cache stability.
- If a session starts at repo root and later uses tools inside `packages/foo`, package-local `AGENTS.md`, `.aegis.md`, `CLAUDE.md`, or `.cursorrules` can be missed.

Solution:

- `subdirectory_rule_hint()` in `aegis/agent/coding_context.py` finds rule files below the original cwd and above the touched path.
- `ToolExecutor` appends a one-time tool-result hint after successful path-scoped tool calls.
- Seen rule files are tracked in `session.meta.subdir_rule_hints_seen`.
- This keeps the system prompt cache-stable while still loading local rules exactly when they matter.

Focused verification:

```bash
python -m pytest tests/test_coding_context.py::test_subdirectory_rule_hint_loads_nested_rules_once \
  tests/test_coding_context.py::test_agent_tool_result_injects_subdirectory_rules -q
```

Expected:

```text
2 passed
```

---

## Next 10 TDD Slices

1. **Tool schema validator**
   - Test: `tests/test_tools.py::test_all_tool_schemas_are_strict_and_named`
   - Files: `aegis/tools/registry.py`, tool modules as needed

2. **Permission dry-run endpoint**
   - Test: `tests/test_dashboard_fastapi.py::test_permission_dry_run_explains_decision`
   - Files: `aegis/dashboard_fastapi.py`, `aegis/tools/permissions.py`

3. **Provider capability matrix**
   - Test: `tests/test_provider_model_inventory.py::test_inventory_includes_capabilities`
   - Files: `aegis/model_meta.py`, `aegis/providers/registry.py`

4. **Trace timeline endpoint**
   - Test: `tests/test_tracing_evals.py::test_trace_timeline_orders_prompt_provider_tool_final`
   - Files: `aegis/tracing.py`, `aegis/dashboard_fastapi.py`

5. **Gateway dead-letter store**
   - Test: `tests/test_gateway_status.py::test_failed_delivery_records_dead_letter`
   - Files: `aegis/gateway/queue.py`, `aegis/gateway/status.py`

6. **Cron dry-run preview**
   - Test: `tests/test_cronjob_tool.py::test_cron_dry_run_preview_includes_next_fire_and_prompt_summary`
   - Files: `aegis/cron.py`, `aegis/tools/cronjob_tool.py`, dashboard route if needed

7. **Skill quality gate CLI**
   - Test: `tests/test_skills_memory.py::test_skill_install_reports_quality_gate_findings`
   - Files: `aegis/skills.py`, `aegis/marketplace.py`, `aegis/tools/skill_manage.py`

8. **Dashboard health score**
   - Test: `tests/test_dashboard_fastapi.py::test_system_health_score_reports_doctor_memory_tools_gateway`
   - Files: `aegis/dashboard_fastapi.py`, `aegis/doctor.py`

9. **Release verification command**
   - Test: `tests/test_packaging.py::test_verify_release_script_runs_selected_checks`
   - Files: `scripts/verify_release.py`, docs if needed

10. **Desktop lifecycle state enum**
    - Test: `desktop/electron/desktop-status.test.cjs`
    - Files: `desktop/electron/desktop-status.cjs`, `desktop/electron/main.js`

---

## Development Rules for Future Work

- Use tests first for every behavior change.
- Keep changes surgical and avoid broad rewrites.
- Do not edit unrelated dirty files.
- Do not add external branding or copied prompts to repo artifacts.
- Prefer existing extension points before adding new systems.
- Every final report must include exact commands run and real output.
