# AEGIS Full Repository Audit

**Date:** 2026-06-22
**Repo:** `/home/alienai/aegis`
**Branch:** `main`
**HEAD audited:** `93c6f4b69bd78e73f0960081ea8b7e89229900a7`

This audit scanned the whole repository inventory, parsed Python/TypeScript/JavaScript sources for structure, inspected critical files across every subsystem, and ran the broad local verification suites. Dependency/build/generated directories were excluded from inventory counts where appropriate (`.git`, `node_modules`, caches, build/dist outputs), but source mirrors, docs, bundled skills, desktop source, dashboard source, tests, scripts, and untracked `site-next/` were included.

---

## 1. Workspace State

Current dirty work to protect:

```text
 M .gitignore
 M README.md
 M aegis/agent/coding_context.py
 M aegis/agent/loop.py
 M aegis/builtin_skills/creative/comfyui/scripts/extract_schema.py
 M aegis/builtin_skills/creative/comfyui/scripts/run_batch.py
 M aegis/builtin_skills/creative/comfyui/tests/test_cloud_integration.py
 M aegis/builtin_skills/creative/comfyui/tests/test_extract_schema.py
 M aegis/builtin_skills/productivity/maps/scripts/maps_client.py
 M aegis/cli/main.py
 M aegis/gateway/discord_channel.py
 M tests/test_coding_context.py
?? docs/production-harness-plan.md
?? docs/full-repo-audit.md
?? site-next/
```

Pre-existing user-owned work before this audit/enhancement pass:

```text
 M .gitignore
 M README.md
?? site-next/
```

Files changed by this pass:

```text
aegis/agent/coding_context.py
aegis/agent/loop.py
aegis/builtin_skills/creative/comfyui/scripts/extract_schema.py
aegis/builtin_skills/creative/comfyui/scripts/run_batch.py
aegis/builtin_skills/creative/comfyui/tests/test_cloud_integration.py
aegis/builtin_skills/creative/comfyui/tests/test_extract_schema.py
aegis/builtin_skills/productivity/maps/scripts/maps_client.py
aegis/cli/main.py
aegis/gateway/discord_channel.py
tests/test_coding_context.py
docs/production-harness-plan.md
docs/full-repo-audit.md
```

---

## 2. Whole-Repo Inventory

Automated inventory scanned:

```text
tracked files:                  800
untracked files:                 10
scanned files excluding deps:    806
text files:                     804
binary files:                     2
total text lines:           251,263
Python files:                   299
collected pytest tests:       1,474
AST-visible Python classes:     719
AST-visible Python functions: 6,846
FastAPI routes detected:        205
CLI commands detected:           60
registered built-in tools:       45
provider presets:                29
bundled skills:                  76
docs markdown files:             18
```

These counts are a dated audit snapshot, not current product badges. Rerun the
inventory and verification commands before copying exact counts into release
notes or the README.

`pygount` language summary:

```text
Python      297 files   96,601 code lines
XML          45 files   19,913 code lines
TSX          45 files    6,989 code lines
JavaScript   29 files    3,007 code lines
TypeScript   14 files      946 code lines
Bash         11 files      831 code lines
Markdown    277 files   28,568 comment/doc lines
Total       806 files  132,593 code lines + 34,293 comment lines
```

Largest source/test surfaces by line count:

```text
aegis/dashboard_fastapi.py              7,237 lines
tests/test_server.py                    6,345 lines
aegis/server.py                         6,337 lines
tests/test_gateway_inbound.py           5,292 lines
tests/test_dashboard_fastapi.py         4,094 lines
tests/test_product_surfaces.py          3,398 lines
aegis/cli/main.py                       2,875 lines
aegis/dashboard.py                      2,520 lines
aegis/cli/repl.py                       2,261 lines
aegis/agent/loop.py                     2,041 lines
aegis/gateway/runner.py                 1,911 lines
aegis/plugins.py                        1,562 lines
aegis/memory_providers.py               1,388 lines
aegis/providers/registry.py             1,263 lines
aegis/surface.py                        1,184 lines
aegis/cron.py                           1,158 lines
aegis/session.py                        1,127 lines
aegis/config.py                         1,101 lines
aegis/tools/builtin.py                  1,088 lines
desktop/electron/main.js                1,079 lines
```

---

## 3. Subsystem Map

```text
builtin_skills      files=342  lines=85,636  py=31  funcs=374
tests               files=96   lines=44,821  py=96  funcs=1,988
dashboard_api       files=70   lines=28,062  py=2   routes=205
desktop             files=69   lines=21,618  JS/CJS/Electron mirror
gateway             files=22   lines=10,347  py=22  funcs=558
tools               files=37   lines=9,632   py=37  funcs=419
cli_tui             files=7    lines=8,708   py=7   funcs=285
agent_runtime       files=16   lines=6,194   py=16  funcs=237
memory_skills       files=8    lines=5,325   py=8   funcs=324
providers           files=12   lines=4,493   py=12  funcs=225
ops_security        files=18   lines=4,255   py=7   funcs=145
automation          files=10   lines=3,901   py=10  funcs=201
sessions_tracing    files=7    lines=2,725   py=7   funcs=148
mcp_rpc_sdk         files=6    lines=2,557   py=6   funcs=158
site_next           files=9    lines=1,513   TS/Next.js internals site
```

---

## 4. Verification Results

### Python / core suite

Command:

```bash
bash scripts/run_tests.sh
```

Final result after installing `.venv` dependencies and fixing lint blockers:

```text
All checks passed!
1474 passed in 241.06s (0:04:01)
```

This was the result for the audited HEAD above. Treat it as historical evidence,
not proof for a later release candidate.

### Desktop Electron suite

Command:

```bash
cd desktop
npm test
```

Result:

```text
tests 87
pass 87
fail 0
duration_ms 342.277209
```

### Dashboard web typecheck

Command:

```bash
cd web
npm run typecheck
```

Result:

```text
> aegis-dashboard@0.1.0 typecheck
> tsc -p . --noEmit
```

Exit code: `0`.

### Internals site check

Command:

```bash
cd site-next
npm run check
```

Result:

```text
✓ Compiled successfully
✓ Generating static pages using 4 workers (3/3)
Route (app)
┌ ○ /
└ ○ /_not-found
```

Exit code: `0`.

### Desktop source mirror drift

Compared `desktop/` against packaged mirror `aegis/desktop_app/`.

```text
desktop mirror files compared: 36
drift: 0
```

---

## 5. What Was Fixed During Audit

The official test runner was initially blocked by Ruff before pytest. Fixed 12 lint blockers:

1. Late-bound lambda variables in `aegis/agent/loop.py`.
2. Unused loop variables in ComfyUI schema extraction.
3. Missing `strict=True` on batch `zip()`.
4. Unused test return-code variables in bundled ComfyUI tests.
5. Exception chaining in maps client HTTP helpers.
6. Constant `setattr` in config CLI handling.
7. Exception chaining in Discord channel fallback.

Also implemented a production context-runtime feature:

- Dynamic subdirectory rule hints for monorepos.
- When path-scoped tools first touch a subtree with local instructions (`AGENTS.md`, `.aegis.md`, `CLAUDE.md`, `.cursorrules`), AEGIS injects those rules once through the tool result instead of rebuilding the system prompt.
- This keeps prompt caching stable while respecting package-local rules.

Focused tests added:

```text
test_subdirectory_rule_hint_loads_nested_rules_once
test_agent_tool_result_injects_subdirectory_rules
```

---

## 6. Core Runtime Audit

Files inspected:

```text
aegis/agent/agent.py
aegis/agent/loop.py
aegis/agent/context.py
aegis/agent/coding_context.py
aegis/agent/compaction.py
aegis/agent/governance.py
aegis/surface.py
aegis/types.py
aegis/config.py
```

Strengths:

- `SurfaceRunner` is the right central entry path for CLI/API/ACP/dashboard/gateway/SDK-style runs.
- `Agent` wires provider, registry, tools, memory, skills, permissions, MCP, context engine, cancellation, steering, and session state.
- The loop has robust features: bounded iterations, tool-call execution, provider spans, middleware hooks, compaction, spill-to-disk, checkpoints, wakeups, trace spans, cost/usage, fallback recovery, and todo nudging.
- Conversation governance normalizes interrupted assistant/tool blocks and orphan tool messages.
- Prompt parts are already tiered, hashed, and tracked in session metadata.

Gaps:

1. Runtime complexity is concentrated in `agent/loop.py` and should be split into provider-call, tool-executor, compaction, and wakeup modules once behavior is locked.
2. Prompt-part inspection exists internally but needs a first-class dashboard route/timeline view.
3. Dynamic context is improving, but stale workspace snapshots after large filesystem changes still need a visible warning.
4. Context budget pressure should be visible by surface and provider.

Verdict: strong foundation; needs modularization and observability, not a rewrite.

---

## 7. Tool System Audit

Files inspected:

```text
aegis/tools/registry.py
aegis/tools/base.py
aegis/tools/builtin.py
aegis/tools/extra_builtin.py
aegis/tools/permissions.py
aegis/tools/process.py
aegis/tools/agentic.py
aegis/tools/devtools.py
aegis/tools/skill_manage.py
aegis/tools/cronjob_tool.py
aegis/tools/code_exec.py
aegis/tools/cloud.py
aegis/tools/lsp.py
aegis/tools/recall.py
aegis/tools/repomap_tool.py
aegis/tools/code_search_tool.py
```

Detected built-in registry:

```text
total tools: 45
core: 37
browser: 2
computer: 1
lsp: 1
vision: 1
voice: 2
web: 1
```

Tool risk groups:

```text
safe/read-only:        14
network:               12
fs:                     5
runtime:                4
automation:             5
network+automation:     2
network+fs:             1
network+runtime:        1
runtime+automation:     1
```

Strengths:

- One registry path for built-ins, optional toolsets, plugin tools, MCP tools, memory tools, context-engine tools.
- Permission engine has hardline blocks, deny groups, modes, allowlist segmentation, security scan escalation, smart classification path, and approver fallback.
- File tools include sensitive-path gating, read stamps, stale-write warnings, locks, atomic writes, fuzzy edit recovery, LSP delta reporting.
- Apply-patch validates relative paths and prevents traversal.
- Network fetches use SSRF/domain policy in `net_safety`.

Gaps:

1. Add a strict schema validator over all registered tools.
2. Add structured `data` outputs consistently across high-use tools.
3. Add dashboard/API permission dry-run explaining allow/deny decisions.
4. Add provenance metadata for plugin/MCP tools in the dashboard.
5. Add tool schema examples for model/tool-search quality.

Verdict: capabilities are broad and safety is solid; next production move is schema/provenance/permission observability.

---

## 8. Providers and Auth Audit

Files inspected:

```text
aegis/providers/registry.py
aegis/providers/base.py
aegis/providers/anthropic.py
aegis/providers/chat_completions.py
aegis/providers/responses.py
aegis/providers/auth.py
aegis/providers/fallback.py
aegis/model_meta.py
aegis/credentials.py
```

Provider presets detected: `29`.

Modes:

- Anthropic messages
- OpenAI-compatible chat completions
- Responses API
- Codex app-server style backend
- local/no-auth providers such as Ollama, LM Studio, vLLM

Strengths:

- Large provider catalog with context lengths and defaults.
- API key, OAuth, local/no-auth, and specialized auth modes are represented.
- Fallback provider path exists.
- Rate-limit and usage accounting are present.
- Model metadata inventory exists.

Gaps:

1. Provider capability metadata is incomplete for UI: tools, streaming, reasoning, vision, structured output, max input/output, auth status, cost source.
2. Need provider contract tests for every mode: streaming, tools, malformed responses, auth failure, rate limit, context overflow, fallback.
3. Dashboard should expose last probe, auth state, and fallback reason per provider.
4. Cost/latency envelopes should feed budget/governor downshift more visibly.

Verdict: provider breadth is already strong; production gap is conformance and capability transparency.

---

## 9. Sessions, Runs, Tracing, Evals Audit

Files inspected:

```text
aegis/session.py
aegis/runs.py
aegis/tracing.py
aegis/evals.py
aegis/ab.py
aegis/trajectory.py
aegis/usage_log.py
```

Strengths:

- SQLite session store with WAL/busy timeout.
- Session rows plus message table and FTS search.
- Session source filtering hides internal review/curator sessions by default.
- Runs and traces exist and are wired through surfaces.
- Eval and AB replay systems exist.
- Session lineage fields exist.

Gaps:

1. Trace timeline endpoint/UI should show prompt parts, provider calls, tool calls, compaction, retries, and final output in order.
2. Session lineage UI should show compaction child, branch, rollback, and replay relationships.
3. Eval comparisons should include tool sequence, cost, latency, and failure categories, not just text.
4. FTS/search UX should expose source filters and exact match windows in dashboard.

Verdict: backend primitives are present; UI/explanation layer is the missing production polish.

---

## 10. Memory, Skills, Learning, Curator Audit

Files inspected:

```text
aegis/memory.py
aegis/memory_providers.py
aegis/skills.py
aegis/skill_manage.py
aegis/curator.py
aegis/learn.py
aegis/marketplace.py
aegis/skill_bundles.py
aegis/builtin_skills/**
```

Strengths:

- File-backed memory with bounded entries, drift detection, locks, injection scanning, and frozen prompt snapshots.
- External memory provider surfaces exist.
- Skills use progressive disclosure with workspace/personal/extra/bundled precedence.
- Bundled skill library is large: 76 `SKILL.md` packages in scan.
- Skill support files are constrained under references/templates/scripts/assets.
- Curator/learning workflows exist.

Gaps:

1. Skill validation should be a hard quality gate for install/create/edit: frontmatter, description length, support-file bounds, required commands, security scan.
2. Skill usage telemetry should be visible and feed curator recommendations.
3. Memory provider sync provenance should be shown in dashboard.
4. Curator dry-run/approval UI should be first-class.
5. Skill bundles need dashboard create/edit/test flows.

Verdict: AEGIS has a real learning/skills foundation; needs governance and UI workflow maturity.

---

## 11. Dashboard/API Audit

Files inspected:

```text
aegis/dashboard_fastapi.py
aegis/dashboard.py
aegis/server.py
web/src/App.tsx
web/src/pages/*
web/src/components/*
web/src/plugins/host.tsx
```

Detected FastAPI routes: `205`.

Route groups:

```text
/api:                193
auth:                  2
dashboard-plugins:     2
root/assets/events/etc 8
```

Dashboard pages detected from React routes:

```text
dashboard/chat/terminal/sessions/models/memory/tools/skills/config/cron/kanban/mcp/channels/webhooks/pairing/accounts/keys/plugins/profiles/persona/files/logs/system/analytics/docs/app
```

Strengths:

- Broad backend API and matching Vite/React control panel.
- Token/basic/loopback auth and host guard logic are present.
- WebSocket/SSE event streams exist.
- PTY-backed embedded terminal exists.
- Plugin route/slot host exists.
- Dashboard has pages for most major product surfaces.

Gaps:

1. `dashboard_fastapi.py` is very large; split into routers by domain after tests remain green.
2. Need trace timeline UI.
3. Need permission dry-run UI.
4. Need provider capability/probe UI.
5. Need gateway queue/dead-letter UI.
6. Need cron dry-run/next-fire preview UI.
7. Need production health score/checklist.
8. Need stronger generated web bundle drift enforcement in everyday workflow.

Verdict: dashboard is already broad; production polish requires domain routers and explanation/diagnostic pages.

---

## 12. Desktop Audit

Files inspected:

```text
desktop/package.json
desktop/electron/main.js
desktop/electron/backend-env.cjs
desktop/electron/backend-ready.cjs
desktop/electron/desktop-status.cjs
desktop/electron/desktop-settings.cjs
desktop/scripts/*.cjs
aegis/desktop_app/** mirror
```

Verification:

```text
87 desktop tests passed
source/package mirror drift: 0
```

Strengths:

- Electron shell starts dashboard backend, probes readiness, handles remote dashboard mode, logs boot errors, restarts backend, and provides tray/deep-link support.
- Secure navigation guard: external URLs opened externally; webviews disabled.
- Packaged backend staging, stamps, manifests, and update eligibility tests exist.
- Desktop source is mirrored into packaged `aegis/desktop_app` with drift test.

Follow-up:

1. Desktop lifecycle is now formalized and visible in the Desktop Shell operations rail.
2. Renderer connection state is available through the Electron bridge.
3. Desktop repair actions cover logs, backend restart, backend env, update check, and update install.
4. Packaged backend staging/probing and artifact provenance are covered; real signed/notarized artifact evidence still requires release credentials.

Verdict: desktop is more mature than expected; remaining work is credentialed release evidence and continued renderer polish.

---

## 13. Gateway, Channels, Webhooks Audit

Files inspected:

```text
aegis/gateway/runner.py
aegis/gateway/base.py
aegis/gateway/channels.py
aegis/gateway/discord_channel.py
aegis/gateway/slack_channel.py
aegis/gateway/signal_channel.py
aegis/gateway/matrix_channel.py
aegis/gateway/email_channel.py
aegis/gateway/ntfy_channel.py
aegis/gateway/webhook_channel.py
aegis/gateway/queue.py
aegis/gateway/status.py
aegis/gateway/pairing.py
aegis/webhook.py
aegis/handoff.py
```

Strengths:

- Many adapters exist.
- Session routing, handoff, timestamps, approvals, memory notifications, wakeups, and profile overlays are present.
- Gateway tests are substantial (`test_gateway_inbound.py`, `test_gateway_commands.py`, status/service tests).
- Queue/idempotency modules exist.

Gaps:

1. Add delivery dead-letter records for failed sends.
2. Add queue/backpressure metrics to dashboard.
3. Add adapter contract tests for every platform with a common fake adapter spec.
4. Add route/channel dashboard: connected chats, admins, pending approvals, recent failures.
5. Add retry idempotency and alerting around delivery failures.

Verdict: gateway breadth is high; reliability/ops visibility is the gap.

---

## 14. Automation, Cron, Kanban, Specs Audit

Files inspected:

```text
aegis/cron.py
aegis/tools/cronjob_tool.py
aegis/kanban.py
aegis/kanban_auto.py
aegis/spec.py
aegis/bench.py
aegis/self_improve.py
aegis/ambient.py
aegis/goals.py
aegis/gstack.py
aegis/background.py
```

Strengths:

- Cron supports intervals/cron syntax/one-shots/scripts/skills/context chaining/no-agent jobs/toolset restrictions/workdir/model overrides.
- Cron has locks, corrupt-store backup, prompt-injection blocking, and delivery sink support.
- Kanban, specs, benchmarks, self-improvement, watch/ambient mode, and role-stack orchestration exist.

Gaps:

1. Cron dry-run and next-run preview should be in CLI + dashboard.
2. Cron execution timeline should link into traces/runs.
3. Kanban worker state should be visible in dashboard with stuck-card diagnostics.
4. Self-improvement should require more explicit approval and rollback preview for skill edits.
5. Benchmark result diffs should be structured and queryable.

Verdict: automation is deep; scheduling/debugging visibility is the next production layer.

---

## 15. MCP, RPC, SDK, ACP Audit

Files inspected:

```text
aegis/mcp/client.py
aegis/mcp/server.py
aegis/rpc.py
aegis/sdk.py
aegis/acp.py
```

Strengths:

- MCP client supports stdio and streamable HTTP, tools/resources/prompts, namespaced tool wrappers, environment filtering, and redaction.
- MCP server exposes AEGIS tools/resources/prompts to other MCP clients.
- JSON-RPC surface exposes agent.run, sessions, runs, traces, evals.
- SDK drives the same runtime and exposes events, trace IDs, run IDs, usage, images, session continuity.
- ACP server handles editor sessions, cancellation, file-system delegation, and session updates.

Gaps:

1. MCP server should expose stricter schemas and permission dry-run metadata.
2. RPC should define a versioned JSON schema for all methods.
3. SDK needs typed exceptions and optional async API.
4. ACP should get more conformance fixtures for client behavior and file diffs.
5. Need compatibility docs for every external protocol surface.

Verdict: integration surfaces are real; formal schemas/conformance are next.

---

## 16. Security and Ops Audit

Files inspected:

```text
aegis/security_scan.py
aegis/net_safety.py
aegis/tools/permissions.py
aegis/tools/file_safety.py
aegis/redact.py
aegis/doctor.py
aegis/onboarding.py
aegis/backup.py
aegis/ops.py
install.sh
install.ps1
uninstall.sh
scripts/run_tests.sh
.github/workflows/ci.yml
```

Strengths:

- SSRF protections resolve hostnames, pin safe IPs, validate redirect hops, and block metadata/private targets by default.
- Command/text scanner catches invisible unicode, pipe-to-shell, obfuscated payloads, credential exfiltration patterns, homograph hosts, prompt injection, shell persistence.
- Permission engine has hardline catastrophic command blocks.
- Secrets are redacted in multiple paths.
- Test runner strips credentials and uses throwaway `AEGIS_HOME`.
- CI runs tests across Ubuntu/macOS and Python 3.10-3.13.

Static pattern findings:

```text
TODO/FIXME/HACK:          60
subprocess shell=True:     6
pickle references:         4
requests no timeout:       2
secret-like strings:     363 mostly env examples/docs/local refs
localhost/private refs:  275 mostly docs/local server usage
```

Security notes:

- The `shell=True` occurrences should stay reviewed and justified; several are controlled test/ambient/hook contexts.
- Secret-like results include expected `.env.example`, docs, and code paths. No live credential value was confirmed in the sampled results.
- Security scanner and permission engine are strong for a local-first agent; dashboard policy simulation now explains file, shell, network, tool, workspace, and profile decisions without leaking secrets.

Follow-up:

1. Security audit now outputs JSON and Markdown reports.
2. CI lint currently has an advisory `ruff check aegis || true` job; the real runner enforces `F,B` through `scripts/run_tests.sh`. CI should make this clearer.
3. `scripts/verify_all.sh` is the full local release/parity gate for Python, web, desktop, docs, ledger, compile, and provenance smoke.
4. Release artifact hashes and a CycloneDX-style artifact SBOM are generated by `scripts/release_provenance.py`.
5. Installer/update rollback coverage can continue expanding around real signed release artifacts once credentials are available.

Verdict: security model is strong; remaining proof gaps are credentialed release evidence and live-channel smoke, not missing local policy surfaces.

---

## 17. Docs, Website, Product Claims Audit

Files inspected:

```text
README.md
ROADMAP.md
RELEASING.md
SECURITY.md
CONTRIBUTING.md
docs/*.md
site-next/**
assets/*.svg/png
```

Strengths:

- README now describes a broad production product accurately in many areas.
- Docs cover install, providers, tools, gateway, MCP, SDK, dashboard, security, tracing/evals, plugins, memory/skills.
- `site-next/` builds successfully and explains internals/product shape.

Current proof:

1. `scripts/verify_all.sh` is the single local verification gate for ledger, generated docs, Python, release provenance, web, desktop, compile, and whitespace checks.
2. `python scripts/check_hermes_parity_ledger.py --final` closes 950 code-map rows: 723 complete and 227 AEGIS-specific site rows justified.
3. Release docs include the verification matrix plus signed/notarized credential requirements.
4. Product docs now link the production harness plan, parity ledger, and this audit.

Remaining external proof:

1. Signed/notarized desktop artifacts require real GitHub/Apple/Windows signing credentials.
2. Telegram, Slack, Discord, and webhook live smoke requires real platform accounts.
3. Screenshots/assets should continue to use generated provenance when refreshed.

Verdict: docs and product claims now match the local verified state; only credential-bound proof remains outside the repo.

---

## 18. Post-Parity Maintenance Plan

The high-priority parity slices are now implemented and covered by the full
local gate. Future work should stay incremental and evidence-driven:

1. Keep `bash scripts/verify_all.sh` green before release claims.
2. Keep generated docs and the parity ledger in sync with any new source file.
3. Add credentialed live-smoke evidence for providers and messaging platforms
   only when real test accounts are configured.
4. Capture signed/notarized artifact proof in CI when release secrets are
   available.
5. Continue decomposing large modules only after tests pin behavior; do not
   split working runtime surfaces just for shape parity.

---

## 19. Final Verdict

AEGIS is not a toy harness. It already has:

- one shared runtime path,
- broad CLI/dashboard/desktop/API/gateway/SDK/ACP/MCP surfaces,
- 45 built-in tools,
- 29 providers,
- bundled skills surfaced by `aegis skills list`,
- memory/learning/curator systems,
- cron/kanban/spec/bench/self-improvement automation,
- serious security controls,
- Python, desktop, web, docs, ledger, compile, and release-provenance checks
  behind one local verification gate.

The remaining proof gaps are external to the local repo:

1. signed/notarized desktop artifacts need real release credentials,
2. live Telegram/Slack/Discord/webhook smoke needs real platform accounts,
3. screenshots/assets should be refreshed with generated provenance whenever
   the product UI changes.

Recommended maintenance posture: keep parity claims tied to tests, docs, and
ledger evidence instead of adding speculative product shells.
