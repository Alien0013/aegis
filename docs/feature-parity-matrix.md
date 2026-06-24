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
| Streaming events | Present | `aegis/surface.py`, dashboard event APIs, trace timeline endpoints/page | Keep end-to-end streaming contract fixtures broad. |
| Mid-turn cancellation/interrupt | Present | `aegis/agent/agent.py`, `aegis/agent/loop.py`, ACP/dashboard paths | Add clearer dashboard/user feedback for cancellation state. |
| Mid-turn steering / queued input | Partial | runtime/session/gateway paths; audit found steering concepts | Verify every surface supports it consistently. |
| Context compression | Present | `aegis/agent/compaction.py`, `aegis/agent/loop.py`, session timeline | Keep compression boundary tests broad. |
| Spill-to-disk for large tool outputs | Present | `aegis/agent/loop.py`, session metadata | Add UI links to spilled artifacts. |
| Filesystem checkpoints / rollback | Present | agent loop + tests | Expose rollback status and diffs in dashboard. |
| Session resume / branch / lineage | Partial | `aegis/session.py`, `/api/sessions/{id}/lineage`, `web/src/pages/Sessions.tsx`, `tests/test_session_checks.py` | Add deeper source filters and packaged/live session recovery proof. |
| Prompt-part hashing/audit | Present | `aegis/agent/context.py`, `/api/sessions/{id}/prompt-audit`, `web/src/pages/PromptAudit.tsx` | Continue adding prompt-cache regression fixtures. |
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
| Tool management commands | Present | `aegis/cli/main.py`, tools registry, `aegis tools doctor`, generated `docs/cli-reference.md` and `docs/tools-reference.md` | Keep generated docs drift-checked in `scripts/verify_all.sh`. |
| Skill management commands | Present | `aegis/skills.py`, `aegis/skill_manage.py`, `/api/skills/manage` | Quality/provenance reports now expose frontmatter, prompt-injection, duplicate, support-file, and requirement checks; keep expanding write-time gates. |
| Memory commands | Present | `aegis/memory.py` | Add provider provenance status. |
| Session browse/list/export | Present | `aegis/session.py`, CLI docs | Add richer source filtering and lineage. |
| Config setup/edit/status/doctor | Present | `aegis/config.py`, `aegis/doctor.py`, `aegis/onboarding.py` | Add migration/diff preview for config changes. |
| Runtime slash commands | Present | `aegis/cli/repl.py`, generated `docs/slash-commands.md`, `tests/test_generated_reference_docs.py` | Add more Hermes-like slash commands where product behavior needs them. |
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
| Local providers | Present | registry includes local/no-auth modes | Live probe status is now cached and visible in the provider matrix. |
| OAuth/API-key auth abstraction | Present | `aegis/providers/auth.py`, `aegis/credentials.py`, Accounts page | Dashboard auth state and credential status are visible per provider. |
| Credential pools / rotation | Partial | credentials/provider audit suggests auth support | Focus audit and add tests if missing. |
| Fallback providers | Present | `aegis/providers/fallback.py`, provider matrix fallback chain | Trace attempts include fallback reasons; provider matrix now shows configured fallback chain. |
| Provider capability matrix | Partial | `aegis/providers/registry.py`, `web/src/pages/Models.tsx`, `/api/providers/matrix` | Matrix shows auth/credential status, cached redacted probe result, last error, fallback chain, context/output limits, pricing flags, and tool/streaming/vision/audio/reasoning/responses-state capabilities. |
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
| Destructive command hard blocks | Present | `aegis/tools/permissions.py`, `security_scan.py`, `/api/security/policy-simulate` | Policy simulator explains hardline, scanner, headless approval, and allowlist outcomes without executing. |
| Tool schema validation | Present | `aegis/tools/schema_validation.py`, `aegis tools doctor`, dashboard schema health, `tests/test_tool_schema_validation.py` | Extend gate to plugin/MCP schemas in CI. |
| Tool provenance display | Partial | tool registry and plugin UI expose some source data | Add source/type/version fields consistently to registry output. |

---

## 5. Memory, Skills, Learning, and Curator

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Persistent memory | Present | `aegis/memory.py`, `aegis/memory_providers.py` | Show provider/provenance in dashboard. |
| Pluggable memory backends | Present | memory providers audit | Add conformance tests per backend. |
| Skill loading | Present | `aegis/skills.py`, bundled skills, `aegis skills list`, `/api/skills/manage` | Usage/provenance and duplicate shadowing are dashboard/API-visible; keep broadening bundled-skill quality fixtures. |
| Skill create/edit/remove | Present | `aegis/skill_manage.py`, skill tools, `/api/skills/manage` | Quality report previews unsafe support files, prompt-injection text, missing requirements, and bad frontmatter; enforce the same report before every write/install path. |
| Bundled skills library | Present | `aegis/builtin_skills/**` | Resolve duplicate names and stale docs as needed. |
| Skill hub/marketplace | Partial | `aegis/marketplace.py` | Add install preview and security scan report. |
| Curator/lifecycle maintenance | Present | `aegis/curator.py` | Add dry-run/approval dashboard. |
| Self-improvement loop | Present | `aegis/self_improve.py`, `aegis/learn.py` | Require stronger approval and rollback preview. |
| Skill provenance | Present | `web/src/pages/Skills.tsx`, `/api/skills/manage`, `tests/test_dashboard_fastapi.py`, `tests/test_product_surfaces.py` | Skills page and API show origin, agent-created/curatable/pinned/protected/bundled/installed state, source, usage, duplicates, and quality findings. |
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
| Sessions page | Present | `web/src/pages/Sessions.tsx`, `/api/sessions/{id}/lineage` | Lineage panel now shows roots, parents, current origin, children, descendants, and warnings. |
| Models/providers page | Present | `web/src/pages/Models.tsx` | Full provider matrix shows readiness, cached live probe, fallback chain, limits, pricing, and capability badges. |
| Memory page | Present | `web/src/pages/Memory.tsx` | Add provider sync/provenance. |
| Tools page | Present | `web/src/pages/Tools.tsx`, `/api/tools/inventory`, `/api/tools/validation`, `/api/tools/permission-dry-run` | Tool inventory now exposes source/provenance, schema hash, availability, required env/auth names, output limits, risk, registry rejections, and plugin/MCP metadata without secrets. |
| Skills page | Present | `web/src/pages/Skills.tsx`, `/api/skills/manage` | Shows skill quality warnings, provenance badges, support-file counts, duplicate copies, and curatable/pinned state. |
| Cron page | Present | `web/src/pages/Cron.tsx` | Add dry-run/next-fire timeline. |
| Gateway/channels/webhooks pages | Present | `web/src/pages/Channels.tsx`, `Webhooks.tsx`, gateway outbox/dead-letter endpoints | Add backpressure metrics and live adapter recovery guidance. |
| Config/files/logs/system/security pages | Present | routes detected, `web/src/pages/Security.tsx`, `/api/security/policy-simulate` | Security page simulates file, shell, network, tool, and workspace/profile-boundary policy with redacted inputs. |
| Analytics page | Present | route detected | Connect to cost/latency/governor metrics. |
| Plugin route/slot host | Present | `web/src/plugins/host.tsx`, `/api/extensions/status`, `web/src/pages/Plugins.tsx` | Plugins page now shows safe mode, manifest/load errors, middleware/hooks, dashboard API route counts, and extension contract health. |
| Trace timeline page | Present | `web/src/pages/TraceTimeline.tsx`, `/api/sessions/{id}/timeline`, `/api/runs/{id}/timeline` | Add richer filters and cross-links as trace depth grows. |
| Prompt/system-context audit page | Present | `web/src/pages/PromptAudit.tsx`, `/api/sessions/{id}/prompt-audit` | Add prompt-cache diff views if needed. |

---

## 8. Desktop App

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Electron desktop shell | Present | `desktop/electron/main.js` | Continue mirror drift checks. |
| Backend launch/probe/readiness | Present | `backend-ready.cjs`, `desktop-lifecycle.cjs`, desktop tests | Keep lifecycle state and readiness probes covered by desktop tests. |
| Remote dashboard mode | Present | desktop lifecycle/UI tests | Remote mode is reported as a first-class lifecycle state. |
| Packaged backend staging | Present | desktop scripts/tests, backend manifest hashes | Release jobs stage/probe a backend and generate artifact provenance. |
| Update eligibility/guards | Present | desktop tests | Add release artifact proof in CI. |
| Secure navigation policy | Present | desktop tests | Keep regression tests. |
| Crash/restart resilience | Present | `desktop-lifecycle.cjs`, restart/splash lifecycle tests | Crash history is bounded and surfaced to the desktop shell. |
| Desktop settings | Present | desktop settings tests | Add in-app settings diagnostics. |
| Signing/notarization guard | Present | desktop tests | Confirm CI secret-less behavior and docs. |

---

## 9. Gateway, Messaging, Webhooks

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Multi-platform gateway runner | Present | `aegis/gateway/runner.py`, `tests/test_gateway_adapter_contract.py` | Keep fake contract coverage synced with platform registry. |
| Telegram/Discord/Slack/Signal/Matrix/email/etc. adapters | Present/Partial | `aegis/gateway/*_channel.py`, fake contract matrix | Add credentialed live-smoke evidence per platform before claiming live coverage. |
| Pairing/admin controls | Present | `aegis/gateway/pairing.py`, CLI/dashboard routes | Add dashboard approval flow polish. |
| Per-channel sessions | Present | gateway/session code | Add routing visualization. |
| Gateway commands/status/restart | Present | gateway tests/docs | Add command registry generation. |
| Queue/idempotency | Present | `aegis/gateway/queue.py`, `tests/test_gateway_adapter_contract.py` | Add deeper backpressure metrics. |
| Dead-letter store for failed delivery | Present | `aegis/gateway/queue.py`, `aegis/dashboard_fastapi.py`, `web/src/pages/Channels.tsx`, `tests/test_gateway_queue_ops.py`, `tests/test_gateway_adapter_contract.py` | Add platform-specific retry playbooks and live-smoke logs. |
| Webhook subscriptions | Present | `aegis/webhook.py`, docs | Add payload templating UI and replay. |
| Handoff from CLI to gateway | Present | `aegis/handoff.py` | Add dashboard handoff history. |
| Voice message STT/TTS through gateway | Partial | voice tools exist | Focus audit per adapter. |

---

## 10. Automation, Cron, Kanban, Background Work

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Durable cron scheduler | Present | `aegis/cron.py`, `/api/cron/jobs/{id}/preview`, `/api/cron/fire` | Add more live scheduler smoke evidence. |
| Cron with skills/model/toolsets/workdir/context chaining | Present/Partial | cron preview, run history, model/toolset/workdir fields | Add broader docs per field. |
| Script-only scheduled jobs | Present/Partial | cron tool/source | Verify no-agent behavior and delivery semantics. |
| Webhook-triggered agent runs | Present | webhook modules | Add replay/security UI. |
| Kanban durable board | Present | `aegis/kanban.py`, `kanban_auto.py` | Add stuck-card and worker dashboard. |
| Multi-agent worker dispatch | Present/Partial | kanban automation | Add lifecycle + failure limits UI. |
| Self-improvement benchmarks | Present | `aegis/bench.py`, `self_improve.py` | Require explicit approvals and rollback. |
| Ambient/watch mode | Present/Partial | `aegis/ambient.py` | Add status/stop UI and resource caps. |
| Background jobs UI | Present/Partial | `aegis/background.py`, `/api/background/jobs`, `web/src/pages/Agents.tsx` | Add durable restart-persistent background storage if needed. |

---

## 11. MCP, ACP, SDK, RPC, API Server

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| MCP client | Present | `aegis/mcp/client.py`, `/api/extensions/status`, `web/src/pages/Mcp.tsx` | MCP page shows stdio/HTTP servers, env/header provenance, include/exclude tool filters, selected/excluded counts, catalog, and live probe actions. |
| MCP server | Present | `aegis/mcp/server.py`, `tests/test_product_surfaces.py` | MCP server uses full ToolContext, memory/provider tools, session-stop hooks, and visible inventory; keep expanding schema fixtures. |
| JSON-RPC stdio | Present | `aegis/rpc.py` | Version all method schemas. |
| Python SDK | Present | `aegis/sdk.py` | Add async API and typed exceptions. |
| ACP/IDE integration | Present | `aegis/acp.py`, `/api/extensions/status`, `tests/test_acp.py` | ACP routes through `SurfaceRunner`, shares SessionStore/trace metadata, supports session list/detail/search/fork/load, permission requests, cancel, and diff updates. |
| OpenAI-compatible API server | Present | `aegis/server.py` | Ensure dependency install path is documented; add endpoint contract docs. |
| API auth | Present | `aegis/server.py`, dashboard auth | Add probe/status UI. |
| API model listing | Present | server tests passed | Keep contract tests current. |

---

## 12. Profiles, Config, Plugins, Packaging

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Profile-aware config/home | Present | `aegis/config.py` | Add import/export profile commands if missing. |
| Env/secrets separation | Present | config/credentials modules | Add redaction tests for every surface. |
| Plugin loading | Present | `aegis/plugins.py`, `/api/extensions/status`, `web/src/pages/Plugins.tsx` | Plugin manifests, safe mode, middleware/hooks, dashboard auth/setup hooks, route mounts, manifest traversal errors, and contribution drift are API/UI-visible. |
| Dashboard plugins | Present | `web/src/plugins/host.tsx` | Add version compatibility checks. |
| Config migration | Partial | config/onboarding modules | Add migration dry-run. |
| Installer scripts | Present | `install.sh`, `install.ps1`, uninstall scripts | Add installer CI smoke. |
| Release scripts/workflows | Present | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/verify_all.sh`, `RELEASING.md` | Keep release gate synced with parity phases. |
| Update command/runtime update | Partial | desktop/update scripts | Focus audit and tests. |

---

## 13. Security, Privacy, and Governance

| Capability | AEGIS status | Evidence / likely files | Gap to close |
|---|---:|---|---|
| Secret redaction | Present | `aegis/redact.py`, tests likely | Expand to all event/log outputs. |
| Prompt-injection scanning | Present | `aegis/security_scan.py`, `aegis security audit --json`, `aegis security audit --markdown` | Scanner output is available as redacted Markdown and machine-readable JSON reports. |
| SSRF/network safety | Present | `aegis/net_safety.py`, `/api/security/policy-simulate`, `web/src/pages/Security.tsx` | Dashboard simulator explains URL allow/deny decisions without fetching. |
| Dangerous shell hard blocks | Present | `aegis/tools/permissions.py` | Keep denylist regression tests. |
| File safety / sensitive path guards | Present | `aegis/tools/file_safety.py` | Add UI explanation. |
| Approvals/manual smart policy | Present/Partial | permission engine | Verify smart policy behavior and docs. |
| Gateway allowlists/admins | Present/Partial | gateway config/tests | Add dashboard guardrail checker. |
| PII redaction | Needs audit | not confirmed in full pass | Add if required for messaging surfaces. |
| Security report command | Present | `aegis/ops.py`, `aegis security audit --json`, `aegis security audit --markdown` | JSON/Markdown audit scans dependencies, MCP commands, plugins, and skills with redacted findings. |
| Release provenance/SBOM | Present | `scripts/release_provenance.py`, release workflow | CI writes and verifies `SHA256SUMS`, `sbom.cdx.json`, and `release-summary.json` for artifacts. |

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
| Security docs | Present | `docs/security.md`, `docs/dashboard.md` | Dashboard docs include the policy simulator and security report surfaces. |
| Dashboard docs | Present | docs and README | Trace, prompt audit, security, plugin, model, tool, session, and desktop lifecycle pages are documented; keep screenshots/prose refreshed after UI changes. |
| Desktop docs | Present | README/docs | Packaged lifecycle, repair state, updater status, and troubleshooting proof are documented. |
| Provider docs | Present | docs and registry | Provider capability/probe matrix details are surfaced; live-account smoke remains credential-bound. |
| Gateway docs | Present | docs/gateway | Platform contract, fake-adapter testing, delivery observability, and live-smoke boundaries are documented. |
| Release docs | Present | `RELEASING.md` | Keep credential-bound signing/notarization notes current. |
| Full parity ledger | Present | `docs/hermes-code-map.csv`, `docs/hermes-parity-ledger.csv`, `scripts/check_hermes_parity_ledger.py --final` | Final mode is closed: 950 rows covered, 723 complete, 227 AEGIS-specific site rows justified, zero pending/partial. |
| Roadmap | Present | README and this matrix now describe current credential-bound limits | Keep refreshed after each production slice. |
| Full audit doc | Present | `docs/full-repo-audit.md` | Keep updated after major slices. |
| Production plan | Present | `docs/production-harness-plan.md` | Keep tied to this matrix. |

---

## 16. Current Verified State

The implementation phases in `/home/alienai/AEGIS_to_Hermes_Full_Parity_Codex_MASTER.md`
are closed against the local parity gate:

1. Prompt audit, trace timeline, tool provenance, provider matrix, generated docs, sessions, gateway delivery observability, cron/background jobs, memory/skills governance, MCP/plugins, security simulator, dashboard/desktop lifecycle, and release provenance are implemented.
2. `bash scripts/verify_all.sh` is the single local gate for ledger coverage, generated docs, Python tests, release provenance smoke, web typecheck/build, desktop tests, compileall, and `git diff --check`.
3. `python scripts/check_hermes_parity_ledger.py --final` closes every code-map row: 950 total, 723 complete, 227 AEGIS-specific site rows justified, zero pending/partial.
4. Remaining proof that cannot be produced locally is credential-bound: signed/notarized release artifacts and live Telegram/Slack/Discord/webhook account smoke runs.

---

## 17. Definition of Done for Full Parity

AEGIS reaches the target when:

1. Every row above is **Present** or deliberately marked **not-needed-aegis-specific** with a reason.
2. Python, desktop, web, docs, security, installer, ledger, and release-provenance checks pass from one command.
3. Every user-facing feature has a CLI command, dashboard/API status, tests, and docs.
4. Every dangerous action has one policy path and a traceable explanation.
5. Every background/remote/durable system has status, retry/failure records, and recovery instructions.
6. No reference-agent branding, private prompts, secrets, or copied implementation text exists in the repo.
