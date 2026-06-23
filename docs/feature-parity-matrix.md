# AEGIS Feature Parity Matrix

**Purpose:** make the production target explicit: every major Codex/Hermes-style capability family should either already exist in AEGIS, have a verified partial implementation, or be listed as a concrete build gap with likely files to change.

**Boundary:** this matrix is for capability parity, not copied implementation. AEGIS must keep its own code, names, prompts, docs voice, security model, and product identity. Third-party user-modeling integrations outside the local AEGIS runtime are out of scope for this product-polish pass.

Status legend:

- **Present** — implemented and covered by current repo/audit evidence.
- **Partial** — implemented but missing production polish, visibility, contracts, or full surface coverage.
- **Missing** — not confirmed in the repo audit; should be built if desired.
- **Needs audit** — likely present in part, but needs a focused source/test pass before marking done.

---

## 1. Runtime and Conversation Loop

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Shared runtime used by CLI/API/dashboard/gateway/automation | Present | `aegis/surface.py`, `aegis/agent/agent.py`, `aegis/agent/loop.py` | Keep all new surfaces entering through `SurfaceRunner` or thin wrappers. |
| Tool-call loop with bounded iterations | Present | `aegis/agent/loop.py`, `tests/test_smoke.py` | Split loop into smaller modules after behavior is fully pinned. |
| Streaming events | Present | `aegis/surface.py`, dashboard event APIs | Add end-to-end trace timeline UI. |
| Mid-turn cancellation/interrupt | Present | `aegis/agent/agent.py`, `aegis/agent/loop.py`, ACP/dashboard paths | Add clearer dashboard/user feedback for cancellation state. |
| Mid-turn steering / queued input | Partial | runtime/session/gateway paths; audit found steering concepts | Verify every surface supports it consistently. |
| Context compression | Present | `aegis/agent/compaction.py`, `aegis/agent/loop.py` | Show compression decisions in traces/UI. |
| Spill-to-disk for large tool outputs | Present | `aegis/agent/loop.py`, session metadata | Add UI links to spilled artifacts. |
| Filesystem checkpoints / rollback | Present | agent loop + tests | Expose rollback status and diffs in dashboard. |
| Session resume / branch / lineage | Partial | `aegis/session.py`, session lineage fields | Build lineage graph UI. |
| Prompt-part hashing/audit | Partial | context builder/session metadata | Build prompt-part audit endpoint and UI. |
| Dynamic subdirectory rules | Present | `aegis/agent/coding_context.py`, `aegis/agent/loop.py`, `tests/test_coding_context.py` | Done for first production slice; monitor monorepo cases. |
| Persistent goals across turns | Partial | `aegis/goals.py`, CLI docs | Verify gateway/dashboard support and tests. |
| Background task execution | Partial | `aegis/background.py`, dashboard/process APIs | Add lifecycle UI and durable state. |

---

## 2. CLI, REPL, and Slash Commands

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Interactive terminal REPL | Present | `aegis/cli/repl.py` | Improve parity tests for slash command registry. |
| One-shot CLI query | Present | `aegis/cli/main.py`, docs | Add strict JSON output contracts. |
| Model/provider switching | Present | `aegis/cli/main.py`, provider registry | Add provider capability display. |
| Tool management commands | Present | `aegis/cli/main.py`, tools registry, `aegis tools doctor` | Generate command docs from the parser. |
| Skill management commands | Present | `aegis/skills.py`, `aegis/skill_manage.py` | Add mandatory skill quality gate. |
| Memory commands | Present | `aegis/memory.py` | Add provider provenance status. |
| Session browse/list/export | Present | `aegis/session.py`, CLI docs | Add richer source filtering and lineage. |
| Config setup/edit/status/doctor | Present | `aegis/config.py`, `aegis/doctor.py`, `aegis/onboarding.py` | Add migration/diff preview for config changes. |
| Runtime slash commands | Partial | `aegis/cli/repl.py`, docs | Generate slash-command docs from registry to prevent drift. |
| Voice toggles | Partial | voice tools exist; CLI support needs focused audit | Verify exact REPL commands and gateway behavior. |
| Snapshot/rollback commands | Partial | checkpoints exist | Add complete command docs/tests. |
| Debug report command | Needs audit | ops/debug modules likely exist | Confirm implementation and add docs/tests if needed. |

---

## 3. Providers, Models, Auth, and Fallback

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Multi-provider catalog | Present | 29 provider presets in `aegis/providers/registry.py` | Keep catalog current. |
| OpenAI-compatible chat providers | Present | `aegis/providers/chat_completions.py` | Add conformance fixtures. |
| Responses-style providers | Present | `aegis/providers/responses.py` | Add streaming/tool-call contract tests. |
| Anthropic-style providers | Present | `aegis/providers/anthropic.py` | Add malformed response/retry tests. |
| Local providers | Present | registry includes local/no-auth modes | Add live probe status UI. |
| OAuth/API-key auth abstraction | Present | `aegis/providers/auth.py`, `aegis/credentials.py` | Add dashboard auth state per provider. |
| Credential pools / rotation | Partial | credentials/provider audit suggests auth support | Focus audit and add tests if missing. |
| Fallback providers | Present | `aegis/providers/fallback.py` | Surface fallback reason/cost/latency in traces. |
| Provider capability matrix | Partial | `aegis/providers/registry.py`, `web/src/pages/Models.tsx` | Add stronger live probe UX and provider-specific fixtures. |
| Cost/usage accounting | Partial | `aegis/usage_log.py`, trace data | Add budget governor integration and UI. |
| Model discovery | Partial | registry discovery flags | Add provider-specific live discovery tests. |

---

## 4. Tools and Permissions

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Central registry for built-ins/plugins/MCP/memory/context tools | Present | `aegis/tools/registry.py`, audit found 45 built-ins | Add strict registry validation. |
| File read/write/search/edit/patch tools | Present | `aegis/tools/builtin.py`, `file_safety.py` | Add structured outputs everywhere. |
| Terminal/process tools | Present | `aegis/tools/builtin.py`, `process.py` | Add process lifecycle dashboard. |
| Python code execution | Present | `aegis/tools/code_exec.py` | Verify sandbox boundaries and resource limits. |
| Web fetch/search tools | Present | `aegis/tools/extra_builtin.py`, `net_safety.py` | Add per-domain policy explanation. |
| Browser automation | Present | registry found browser tools | Add live backend health/probe UI. |
| Computer-use automation | Present | registry found computer tool | Add stricter permission examples and tests. |
| Vision/image tools | Present | registry found vision/voice/media tools | Verify provider readiness checks. |
| Text-to-speech / speech tools | Present | registry found voice tools | Add voice config UI. |
| LSP/code intelligence | Present | `aegis/tools/lsp.py` | Add editor/dashboard diagnostics view. |
| Subagents/delegation | Present | `aegis/tools/agentic.py`, runtime docs | Add durable/orchestrator limits UI. |
| Cron job tool | Present | `aegis/tools/cronjob_tool.py` | Add dry-run preview and next-fire endpoint. |
| Permission cascade | Present | `aegis/tools/permissions.py`, `aegis/dashboard.py`, `web/src/pages/Tools.tsx`, `tests/test_agent_perms.py` | Extend examples/docs to plugin and MCP tools. |
| Destructive command hard blocks | Present | `aegis/tools/permissions.py`, `security_scan.py` | Add policy regression examples. |
| Tool schema validation | Present | `aegis/tools/schema_validation.py`, `aegis tools doctor`, dashboard schema health, `tests/test_tool_schema_validation.py` | Extend gate to plugin/MCP schemas in CI. |
| Tool provenance display | Partial | tool registry and plugin UI expose some source data | Add source/type/version fields consistently to registry output. |

---

## 5. Memory, Skills, Learning, and Curator

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Persistent memory | Present | `aegis/memory.py`, `aegis/memory_providers.py` | Show provider/provenance in dashboard. |
| Pluggable memory backends | Present | memory providers audit | Add conformance tests per backend. |
| Skill loading | Present | `aegis/skills.py`, bundled skills, `aegis skills list` | Add usage telemetry UI. |
| Skill create/edit/remove | Present | `aegis/skill_manage.py`, skill tools | Enforce quality gate before write/install. |
| Bundled skills library | Present | `aegis/builtin_skills/**` | Resolve duplicate names and stale docs as needed. |
| Skill hub/marketplace | Partial | `aegis/marketplace.py` | Add install preview and security scan report. |
| Curator/lifecycle maintenance | Present | `aegis/curator.py` | Add dry-run/approval dashboard. |
| Self-improvement loop | Present | `aegis/self_improve.py`, `aegis/learn.py` | Require stronger approval and rollback preview. |
| Skill provenance | Partial | skills have metadata, but UI unclear | Add provenance dashboard and tests. |
| External coding skills from public repo | Done in assistant environment | assistant skill library has coding guidelines saved | Do not import process-skills into AEGIS unless product integration is explicitly requested. |

---

## 6. Sessions, Search, Runs, Tracing, Evals

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| SQLite session store | Present | `aegis/session.py` | Add migration/version dashboard. |
| FTS conversation search | Present | `aegis/session.py` | Add search windows/source filters in UI. |
| Runs table / run metadata | Present | `aegis/runs.py` | Link runs to traces/session lineage everywhere. |
| Trace spans | Present | `aegis/tracing.py`, provider/tool spans | Add timeline endpoint and UI. |
| Eval replay | Present | `aegis/evals.py`, `aegis/ab.py` | Compare tool sequence/cost/latency/error classes. |
| Usage analytics | Partial | `aegis/usage_log.py` | Add dashboard cost/budget views. |
| Session export/import | Present | CLI docs/source likely | Verify exact formats and tests. |
| Role alternation/governance | Present | `aegis/agent/governance.py` | Keep regression tests for every surface. |

---

## 7. Dashboard, API, and Local Web UI

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| FastAPI dashboard backend | Present | `aegis/dashboard_fastapi.py`, 205 routes | Split into routers after behavior is pinned. |
| React/Vite admin dashboard | Present | `web/src/App.tsx`, pages for sessions/models/tools/etc. | Add missing ops/control pages. |
| Chat/PTY terminal in dashboard | Present | `web/src/pages/Chat.tsx` | Add connection diagnostics and reconnect history. |
| Sessions page | Present | `web/src/pages/Sessions.tsx` | Add lineage graph. |
| Models/providers page | Present | `web/src/pages/Models.tsx` | Add capability/probe/auth matrix. |
| Memory page | Present | `web/src/pages/Memory.tsx` | Add provider sync/provenance. |
| Tools page | Present | `web/src/pages/Tools.tsx`, `/api/tools/validation`, `/api/tools/permission-dry-run` | Add fuller provenance and plugin/MCP schema coverage. |
| Skills page | Present | `web/src/pages/Skills.tsx` | Add quality gate + curator flow. |
| Cron page | Present | `web/src/pages/Cron.tsx` | Add dry-run/next-fire timeline. |
| Gateway/channels/webhooks pages | Present | `web/src/pages/Channels.tsx`, `Webhooks.tsx`, gateway outbox/dead-letter endpoints | Add backpressure metrics and live adapter recovery guidance. |
| Config/files/logs/system pages | Present | routes detected | Add production health score. |
| Analytics page | Present | route detected | Connect to cost/latency/governor metrics. |
| Plugin route/slot host | Present | `web/src/plugins/host.tsx` | Add plugin provenance/security UI. |
| Trace timeline page | Missing | trace backend exists | P2/P3 high priority. |
| Prompt/system-context audit page | Missing | prompt metadata exists | Build after trace timeline. |

---

## 8. Desktop App

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Electron desktop shell | Present | `desktop/electron/main.js` | Continue mirror drift checks. |
| Backend launch/probe/readiness | Present | `backend-ready.cjs`, `desktop-status.cjs` | Formal lifecycle enum. |
| Remote dashboard mode | Present | desktop tests | Add UI state for remote/local. |
| Packaged backend staging | Present | desktop scripts/tests | Add packaged smoke artifact verification. |
| Update eligibility/guards | Present | desktop tests | Add release artifact proof in CI. |
| Secure navigation policy | Present | desktop tests | Keep regression tests. |
| Crash/restart resilience | Partial | restart/splash lifecycle tests | Add persistent crash history and repair action. |
| Desktop settings | Present | desktop settings tests | Add in-app settings diagnostics. |
| Signing/notarization guard | Present | desktop tests | Confirm CI secret-less behavior and docs. |

---

## 9. Gateway, Messaging, Webhooks

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Multi-platform gateway runner | Present | `aegis/gateway/runner.py` | Add unified adapter contract tests. |
| Telegram/Discord/Slack/Signal/Matrix/email/etc. adapters | Partial/Present | `aegis/gateway/*_channel.py` | Confirm every adapter with fake integration tests. |
| Pairing/admin controls | Present | `aegis/gateway/pairing.py`, CLI/dashboard routes | Add dashboard approval flow polish. |
| Per-channel sessions | Present | gateway/session code | Add routing visualization. |
| Gateway commands/status/restart | Present | gateway tests/docs | Add command registry generation. |
| Queue/idempotency | Present | `aegis/gateway/queue.py` | Add backpressure metrics. |
| Dead-letter store for failed delivery | Present | `aegis/gateway/queue.py`, `aegis/dashboard_fastapi.py`, `web/src/pages/Channels.tsx`, `tests/test_gateway_queue_ops.py` | Add platform-specific retry playbooks and metrics. |
| Webhook subscriptions | Present | `aegis/webhook.py`, docs | Add payload templating UI and replay. |
| Handoff from CLI to gateway | Present | `aegis/handoff.py` | Add dashboard handoff history. |
| Voice message STT/TTS through gateway | Partial | voice tools exist | Focus audit per adapter. |

---

## 10. Automation, Cron, Kanban, Background Work

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Durable cron scheduler | Present | `aegis/cron.py` | Add dry-run/next-fire preview. |
| Cron with skills/model/toolsets/workdir/context chaining | Present/Partial | cron audit found rich fields | Add full docs/tests per field. |
| Script-only scheduled jobs | Present/Partial | cron tool/source | Verify no-agent behavior and delivery semantics. |
| Webhook-triggered agent runs | Present | webhook modules | Add replay/security UI. |
| Kanban durable board | Present | `aegis/kanban.py`, `kanban_auto.py` | Add stuck-card and worker dashboard. |
| Multi-agent worker dispatch | Present/Partial | kanban automation | Add lifecycle + failure limits UI. |
| Self-improvement benchmarks | Present | `aegis/bench.py`, `self_improve.py` | Require explicit approvals and rollback. |
| Ambient/watch mode | Present/Partial | `aegis/ambient.py` | Add status/stop UI and resource caps. |
| Background jobs UI | Missing/Partial | background module exists | Build unified active jobs panel. |

---

## 11. MCP, ACP, SDK, RPC, API Server

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| MCP client | Present | `aegis/mcp/client.py` | Add server health/provenance UI. |
| MCP server | Present | `aegis/mcp/server.py` | Expose richer schemas and permission metadata. |
| JSON-RPC stdio | Present | `aegis/rpc.py` | Version all method schemas. |
| Python SDK | Present | `aegis/sdk.py` | Add async API and typed exceptions. |
| ACP/IDE integration | Present | `aegis/acp.py` | Add conformance fixtures and editor diff tests. |
| OpenAI-compatible API server | Present | `aegis/server.py` | Ensure dependency install path is documented; add endpoint contract docs. |
| API auth | Present | `aegis/server.py`, dashboard auth | Add probe/status UI. |
| API model listing | Present | server tests passed | Keep contract tests current. |

---

## 12. Profiles, Config, Plugins, Packaging

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Profile-aware config/home | Present | `aegis/config.py` | Add import/export profile commands if missing. |
| Env/secrets separation | Present | config/credentials modules | Add redaction tests for every surface. |
| Plugin loading | Present | `aegis/plugins.py` | Add plugin sandbox/provenance UI. |
| Dashboard plugins | Present | `web/src/plugins/host.tsx` | Add version compatibility checks. |
| Config migration | Partial | config/onboarding modules | Add migration dry-run. |
| Installer scripts | Present | `install.sh`, `install.ps1`, uninstall scripts | Add installer CI smoke. |
| Release scripts/workflows | Present | `.github/workflows/ci.yml`, `RELEASING.md` | Add full release gate and artifact hashes. |
| Update command/runtime update | Partial | desktop/update scripts | Focus audit and tests. |

---

## 13. Security, Privacy, and Governance

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Secret redaction | Present | `aegis/redact.py`, tests likely | Expand to all event/log outputs. |
| Prompt-injection scanning | Present | `aegis/security_scan.py` | Output JSON/Markdown reports. |
| SSRF/network safety | Present | `aegis/net_safety.py` | Add dashboard policy simulator. |
| Dangerous shell hard blocks | Present | `aegis/tools/permissions.py` | Keep denylist regression tests. |
| File safety / sensitive path guards | Present | `aegis/tools/file_safety.py` | Add UI explanation. |
| Approvals/manual smart policy | Present/Partial | permission engine | Verify smart policy behavior and docs. |
| Gateway allowlists/admins | Present/Partial | gateway config/tests | Add dashboard guardrail checker. |
| PII redaction | Needs audit | not confirmed in full pass | Add if required for messaging surfaces. |
| Security report command | Missing/Partial | scanner exists | Build `aegis security audit --json --md`. |
| Release provenance/SBOM | Missing | not confirmed | Add hashes/SBOM to release gate. |

---

## 14. Media, Browser, Computer Use, Creative Tools

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Vision/image input | Present | registry found vision tool; CLI docs mention `--image` | Verify provider fallback and dashboard upload path. |
| Image generation | Partial/Needs audit | creative/media skills exist; registry details need focused pass | Confirm tool-level image generation support. |
| Video/audio analysis | Needs audit | creative/media skills exist | Add tool/provider readiness checks if missing. |
| TTS | Present | registry found voice tools | Add voice selection/status UI. |
| STT | Partial/Needs audit | gateway/media likely | Confirm per-platform voice transcription. |
| Browser automation | Present | registry found browser tools | Add backend setup docs and health checks. |
| Computer-use desktop automation | Present | registry found computer tool | Add safety/approval examples and limits. |
| Creative bundled workflows | Present | many creative skills | Keep bundled skill tests green and isolate external deps. |

---

## 15. Docs and Product Surface

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| User quickstart | Present | `docs/quickstart.md` | Keep generated command examples in sync. |
| CLI reference | Present | `docs/cli.md` | Generate from command registry where possible. |
| Security docs | Present | `docs/security.md` | Add security report examples. |
| Dashboard docs | Present/Partial | docs and README | Add trace, permission, provider, and dead-letter page details. |
| Desktop docs | Partial | README/docs | Add packaged lifecycle and troubleshooting proof. |
| Provider docs | Present/Partial | docs and registry | Add provider capability/probe matrix details. |
| Gateway docs | Present/Partial | docs/gateway | Add platform contract table. |
| Release docs | Present | `RELEASING.md` | Add full verification matrix and artifact hashes. |
| Roadmap | Partial | README and this matrix now describe current product polish gaps | Keep refreshed after each production slice. |
| Full audit doc | Present | `docs/full-repo-audit.md` | Keep updated after major slices. |
| Production plan | Present | `docs/production-harness-plan.md` | Keep tied to this matrix. |

---

## 16. Immediate Build Order

Fastest high-impact path:

1. **Trace timeline + prompt/context audit** — prompt parts, provider calls, tool calls, retries, compaction, outputs.
2. **Provider capability/probe/auth matrix hardening** — live readiness, cost/latency, model capabilities, and failure reasons.
3. **Tool provenance and plugin/MCP schema gate** — consistent source/type/version fields and CI validation beyond built-ins.
4. **Cron dry-run, next-fire, and active jobs panel** — one operational view for scheduled and background work.
5. **Desktop lifecycle state machine** — boot, backend, restart, crash history, repair actions, and packaged smoke verification.
6. **Skill quality gate + curator approval UI** — safe long-term self-improvement with rollback preview.
7. **API/SDK contract fixtures** — streaming, cancellation, auth, run events, MCP, eval replay, and responses-style behavior.
8. **Gateway fake-adapter/live-test matrix** — adapter contract coverage plus explicit credentialed smoke steps.
9. **Release provenance** — artifact hashes, SBOM, signing/notarization proof when credentials exist, and a single release gate.
10. **Generated docs** — CLI/API/dashboard references generated from code where practical.

---

## 17. Definition of Done for Full Parity

AEGIS reaches the target when:

1. Every row above is **Present** or deliberately marked **Won't build** with a reason.
2. Python, desktop, web, site, docs, security, installer, and release checks pass from one command.
3. Every user-facing feature has a CLI command, dashboard/API status, tests, and docs.
4. Every dangerous action has one policy path and a traceable explanation.
5. Every background/remote/durable system has status, retry/failure records, and recovery instructions.
6. No reference-agent branding, private prompts, secrets, or copied implementation text exists in the repo.
