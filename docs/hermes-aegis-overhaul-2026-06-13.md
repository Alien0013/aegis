# Hermes → AEGIS: Exhaustive Overhaul Gap Analysis

Date: 2026-06-13
Author: subsystem-by-subsystem audit driving the AEGIS overhaul.

## Sources & method

- **Hermes truth**: `/tmp/hermes-llms-full.txt` (full docs site, 62,819 lines). Citations are `H:<line>`.
- **AEGIS code**: `/home/alienai/aegis/aegis/**`. Citations are `file:line`.
- **Predecessor audit**: `docs/hermes-aegis-full-audit-2026-06-13.md` (prose-level). This report supersedes it with code-grounded, per-subsystem verdicts and a finer roadmap.

### Out of scope (explicitly excluded per the task brief — being built in parallel)

These four are **NOT** flagged below even where evidence appears:

1. Gateway compaction hygiene safety-net (85% + hard message limit).
2. Curator run-gating (interval/idle/first-run defer) + tar.gz backups/rollback + per-run reports.
3. Default alignment (agent compaction 0.50, curator archive-after 90d, self-improvement intervals memory=10 / skills=10).
4. (Folded into #2/#3.)

Cosmetic/UX-only surfaces (TUI skins, desktop chrome, LaTeX rendering, light-terminal detection) are noted **DEFERRED** and carry no effort.

---

## Executive summary

AEGIS is architecturally a faithful small-scale Hermes: 3-tier prompt assembly, pluggable context engine, LLM-summarization compaction with token-budgeted tail and summary-folding, SQLite+FTS sessions with lineage, Anthropic prompt caching, fallback chains, an MCP client/server, an ACP adapter with diff/replay, a 225-route FastAPI dashboard, 8-channel gateway, cron, kanban, goals/Ralph-loop, MoA, subagents, and a self-improvement loop with a deterministic curator.

The gaps are now mostly **depth, breadth, and a handful of genuinely-absent mechanisms** rather than missing pillars. The five most consequential genuine holes:

1. **Curator has no LLM consolidation pass at all** — `aegis/curator.py` makes zero provider calls (`grep -c build_provider/run_agent/.chat = 0`). It is deterministic-only: stale/archive transitions + duplicate heuristics. Hermes' curator forks an aux-model agent (`max_iterations=8`) that reads skills and proposes consolidations/patches (H:8904-8916). This is the single biggest *functional* divergence in an in-scope subsystem.
2. **Credential pools are a config stub** — `config.py:377 "credential_pools": {}` exists but no pool manager, rotation strategy, cooldown, or 429/402/401→rotate→fallback ladder (Hermes `agent/credential_pool.py`, H:28155-28360). Subagent pool sharing absent.
3. **OAuth provider catalog is narrow** — AEGIS has PKCE for Anthropic/OpenAI/Codex/Google (`auth.py`); Hermes ships Qwen, MiniMax, xAI/SuperGrok, GitHub Copilot, Nous Portal device-code, plus Bedrock/Azure/Vertex (H:35628-35660, H:24567-24735). No Nous Portal "one login" / Tool Gateway.
4. **External memory providers are HTTP/JSONL shells**, not the 9 plugin-quality providers with prefetch/queue_prefetch/session-switch/compression-hooks/per-provider tool suites Hermes ships (H:9457-10010).
5. **Tool registry is 39 vs ~71** and missing high-value tools: `vision_analyze`, `video_generate`/`video_analyze`, `x_search`, `web_extract` (summarizing extractor distinct from `web_fetch`), browser CDP tools, and Home Assistant tools (H:40774, H:23366).

Secondary but real: session schema lacks normalized message rows + billing/token/cost columns (Hermes schema v11, H:35326-35349); skill telemetry tracks only `count`+`last_used`, not separate view/use/patch counters (`skills.py:201`, vs H:9080-9106); no missed-run recovery / scheduler lock / no-agent script mode in cron; gateway breadth 8 vs 20+ with little rich-media/threads/slash-command/typing parity; no self-update apply, config migration, or supply-chain advisory scanner.

---

## PARITY / DIVERGES / MISSING table

| # | Subsystem | Hermes (cite) | AEGIS (cite) | Verdict | Effort |
|---|---|---|---|---|---|
| 1 | Agent loop & iteration budget | H:34038-34230, budget 90 + 70/90% pressure | `agent/loop.py` (1408 ln), `events.py:21 BUDGET_EXHAUSTED` | PARITY (verify 70/90% inline `_budget_warning` injection) | S |
| 2 | Subagent delegation | H:11961-12230 depth-limit, model override, child timeout | `spawn_subagent` (`agentic.py`) | DIVERGES (no pool sharing, verify depth cap/timeout) | M |
| 3 | Mixture-of-agents | H:40910 (4 ref + 1 aggregator, max reasoning) | `agentic.py:572 MixtureTool` (2–5 models, synth) | PARITY | — |
| 4 | Steering / interrupt | H:18058, two-level guard | `gateway/base.py:104-113` interrupt + `/steer` | PARITY | — |
| 5 | Prompt assembly tiers | H:34297-34389 stable/context/volatile | `agent/context.py` identical 3-tier | PARITY | — |
| 6 | Context engine plugin | H:34558-34583 ABC + config select | `agent/context_engine.py` Protocol + `get_engine` + session-switch hook | PARITY | — |
| 7 | Compaction algorithm | H:34691-34800 4-phase, fold summary, boundary-align | `agent/compaction.py` token-tail, fold, anchor digest | PARITY | — |
| 8 | Prompt caching | H:34848-34920 system+3 rolling, 1h TTL config | `anthropic.py:110-167` system + last-3 ephemeral | DIVERGES (no TTL config, no skill-block breakpoint, OpenRouter/Nous caching path) | S |
| 9 | Built-in MEMORY/USER + write_approval | H:9170-9407 | `memory.py` (619 ln) | PARITY (confirm `memory.write_approval` gate) | S |
| 10 | External memory providers (9) | H:9457-10010 prefetch/session-switch/compression hooks | `memory_providers.py` HTTP/JSONL adapters | DIVERGES | L |
| 11 | Skills progressive disclosure | H:8103-8136 | `skills.py` SKILL.md + index | PARITY | — |
| 12 | skill_manage actions | H:8420-8460 create/patch/edit/delete/write_file/remove_file | `tools/skill_manage.py` | PARITY (confirm write_file/remove_file) | S |
| 13 | Skills hub / taps / sources (9) | H:8470-8660 official/skills-sh/well-known/github/clawhub/lobehub/browse-sh/url | (none — `marketplace.py` minimal) | MISSING | L |
| 14 | Plugin-provided & per-platform skills | H:32785-32922 | partial (`plugins.py`) | DIVERGES | M |
| 15 | Curator LLM consolidation pass | H:8904-8916 aux fork, max_iter=8, consolidation rules | `curator.py` deterministic only (0 LLM calls) | MISSING | M |
| 16 | Curator telemetry counters (view/use/patch) | H:9080-9106 | `skills.py:201` count+last_used only | DIVERGES | S |
| 17 | Session storage schema | H:35326-35349 normalized messages + billing/token/cost, schema v11 | `session.py` JSON-blob sessions + FTS | DIVERGES | M |
| 18 | session_search shapes | H:5422, H:9340 browse/discover/read/scroll | `tools/recall.py` (added in branch) | PARITY (no cross-profile) | S |
| 19 | Recap on resume / lineage | H:5064-5558 lineage, auto-title #N | `session.py:158-329` lineage + forking | DIVERGES (no auto-title-in-lineage, no recap injection) | S |
| 20 | Provider catalog breadth | H:35628-35660 (~40 families) | `providers/registry.py` ~30 specs | DIVERGES | M |
| 21 | OAuth flows (Qwen/MiniMax/xAI/Copilot/Nous) | H:24567-24735 | `auth.py` Anthropic/OpenAI/Codex/Google only | MISSING | M |
| 22 | Nous Portal + Tool Gateway "one login" | H:25726, H:tool-gateway | (none) | MISSING | M |
| 23 | Credential pools | H:28143-28360 rotation/cooldown/refresh/share | `config.py:377` stub only | MISSING | M |
| 24 | Fallback chains | H:35746-35789 | `providers/fallback.py` | PARITY | — |
| 25 | OpenRouter routing / Bedrock / Azure / Vertex | H:25649-25686, H:24534 | partial (no provider_routing extra_body, no Bedrock/Azure) | DIVERGES | M |
| 26 | Auxiliary model slots | H:3817-4040 (9 named tasks, per-task provider/model/base_url) | `config.py:243 auxiliary{}` minimal | DIVERGES | M |
| 27 | Tool registry breadth | H:40774 ~71 tools | 39 tools (`tools/*`) | DIVERGES | M |
| 28 | High-value missing tools | H:40774 vision_analyze/video_*/x_search/web_extract/ha_*/browser CDP | absent (have generic equivs) | MISSING | M |
| 29 | Toolset registry + per-platform config introspection | H:7890-7903, H:/v1/toolsets | partial (`groups` on tools) | DIVERGES | M |
| 30 | Messaging gateway breadth | H:34935 20+ adapters | 8 channels (`gateway/channels.py:180-200`) | DIVERGES | L |
| 31 | Per-platform rich behavior | H:18683-19636 voice/threads/typing/slash/streaming-edits/reactions | `context.py:107 PLATFORM_HINTS` text only | DIVERGES | L |
| 32 | Busy modes / delivery queue | H:18103, gateway/delivery | `gateway/queue.py`, base busy guard | PARITY | — |
| 33 | Channel directory / home channel | H:gateway-internals channel_directory.py | (none) | MISSING | M |
| 34 | Cron typed store + run logs | H:11934, cron-internals | `cron.py` last_run only | DIVERGES | M |
| 35 | Cron missed-run recovery / lock / no-agent script mode | H:11619-11725 | (none — `automation.py` script-context only) | MISSING | M |
| 36 | Cron recursive-cron guard / blueprints / context_from | H:11661, H:37499 blueprints | (none) | MISSING | M |
| 37 | Dashboard control plane | H:169 routes | `dashboard_fastapi.py` 225 routes | PARITY (breadth); DIVERGES on OAuth-login UX + profile-scoped URLs | M |
| 38 | MCP client + server | H:25896-26110 | `mcp/client.py`, `mcp/server.py` | PARITY | — |
| 39 | MCP catalog / install / test / tool-selection | H:25776-25895 curated catalog + checklist | (none) | MISSING | M |
| 40 | ACP history replay / load / approvals | H:26698-26740 list/load/resume/fork + 3-tier approval | `acp.py` session/load replay, diff blocks | DIVERGES (no allow_session tier, verify fork/list) | M |
| 41 | Personalities / SOUL.md / context priority chain | H:10097-10164, H:34375 | `context.py:_persona`, `Workspace.rules()` | PARITY (verify .hermes>AGENTS>CLAUDE>.cursorrules first-match) | S |
| 42 | Security: command scan + URL/SSRF + content scan | H:6893-7100, H:net_safety | `security_scan.py`, `net_safety.py`, `redact.py` | PARITY | — |
| 43 | Supply-chain advisory checking | H:7494-7542 | `tools/process.py dependency_audit`? partial | DIVERGES | S |
| 44 | Profiles & multi-profile gateways / tenancy | H:5608-5897 | (no first-class profiles) | MISSING | L |
| 45 | Self-update check/apply | H:705-918 | `ops.py`/`doctor.py` partial | DIVERGES | S |
| 46 | Config migration | H:38209 `hermes migrate` | (none typed) | MISSING | S |
| 47 | Doctor / backup / import / debug-share / prompt-dump | H:506-521, H:38246 | `doctor.py`,`backup.py`,`checkpoints.py` | DIVERGES (depth) | S |
| 48 | Kanban multi-agent board | H:12251-13206 | `kanban.py`,`kanban_auto.py` | PARITY (verify runs/heartbeat/circuit-breaker) | M |
| 49 | Goals / Ralph loop | H:13531-13700 judge | `goals.py` | PARITY | — |
| 50 | Voice / TTS / STT | H:15571-17795 | `tools/voice.py` speak/transcribe | DIVERGES (no Discord VC, no gateway voice-reply) | M |
| 51 | Vision / image paste / image gen | H:16755-17124 | `cloud_image`,`generate_image` | DIVERGES (no vision_analyze aux, no paste) | M |
| 52 | Video gen / X search / deliverable mode | H:40774, H:21x | (none / partial deliverable in prompt) | MISSING | M |
| 53 | Terminal backends (docker/ssh/modal/daytona/singularity) | H:7907-8000 | `tools/environments/*` all present | PARITY | — |
| 54 | Code execution (programmatic tool calling) | H:13711-13998 | `tools/code_exec.py execute_code` | PARITY | — |
| 55 | Event hooks (gateway/plugin/shell) | H:14005-15340 | `hooks.py`,`eventbus.py` | DIVERGES (verify shell-hook + gateway lifecycle events) | M |
| 56 | Batch processing / trajectories / evals | H:15341-15571, H:29737 | `evals.py`,`trajectory.py`,`runs.py` | PARITY | — |
| 57 | i18n | (minimal in Hermes) | (none) | DEFERRED | — |
| 58 | TUI / desktop chrome | H:2693-2987 | `cli/tui.py`,`desktop.py` | DEFERRED | — |

---

## Per-subsystem detail

### A. Agent loop, budget, delegation, MoA, steering

**Hermes** (H:34038-34230): `AIAgent.run_conversation`, three API modes (chat_completions/codex_responses/anthropic_messages), interruptible API calls in a background thread, concurrent tool exec via ThreadPoolExecutor with interactive tools forced sequential, agent-level intercepted tools (todo/memory/session_search/delegate_task), `IterationBudget` default 90 with 70%/90% `_budget_warning` injected into the *last tool result JSON* (not a separate message — preserves caching). Subagents get independent budgets capped at `delegation.max_iterations=50`; depth-limited nesting; child timeout.

**AEGIS**: `agent/loop.py` (1408 lines) drives the loop; `agent/events.py:21` defines `BUDGET_EXHAUSTED`; MoA `agentic.py:572` fans 2–5 models + synthesis (Hermes does fixed 4+1 at max reasoning — minor divergence, AEGIS is more flexible). Steering/interrupt present (`gateway/base.py:104-113`).

**Verdicts**: loop/MoA/steering **PARITY**. Delegation **DIVERGES** — confirm depth cap + child timeout + that subagents share the (future) credential pool; Hermes explicitly shares pools to children (H:28328-28338).

**Build**: (M) when pools land, propagate to `spawn_subagent`. Verify the budget-warning is injected into tool-result JSON rather than as a user message (caching-preserving), matching H:3730-3750.

### B. Context: prompt assembly, engine, caching

Prompt assembly is a near-1:1 port (`agent/context.py` stable/context/volatile vs H:34297). Context engine is a proper pluggable Protocol with `should_compress/compress/tools/on_session_start/on_pre_compress/on_session_switch` (`context_engine.py`) — this matches and even exposes the session-switch hook Hermes uses for memory providers. Compaction (`compaction.py`) implements all four Hermes guards (token-tail, summary-fold, input-cap, failure anchor-digest).

**Prompt caching DIVERGES** (`anthropic.py:110-167`): AEGIS marks system prompt + last-3 wire messages with `{"type":"ephemeral"}` — correct "system_and_3" shape. Gaps vs H:34848-34920: (1) no `cache_ttl` config (`5m`/`1h`), Hermes defaults system+skills to **1h** (H:3807-3812); (2) no separate cache breakpoint on the skills block; (3) caching only on the native Anthropic path — Hermes also applies it for Claude-via-OpenRouter and Nous Portal.

**Build**: (S) add `prompt_caching.cache_ttl` config and pass `ttl` into the ephemeral markers; consider a skills-block breakpoint; extend cache markers to the OpenRouter Claude path in `chat_completions.py`.

### C. Memory (built-in + external providers)

Built-in MEMORY/USER (`memory.py`, 619 ln) with injection scanning, limits, frozen snapshots — **PARITY**; confirm the `memory.write_approval` staged-write gate (H:9380-9407) and `/memory pending/diff/approve` flow exist.

**External providers DIVERGES → the big one** (`memory_providers.py`): AEGIS has a lifecycle interface + `on_memory_write` mirroring + JSONL/mem0/honcho/generic-HTTP adapters. Hermes ships **9** plugin-quality providers (Honcho, OpenViking, Mem0, Hindsight, Holographic, RetainDB, ByteRover, Supermemory, Memori) each with: (1) **system-prompt context injection**, (2) **prefetch/queue_prefetch before each turn** (background, non-blocking), (3) **per-turn sync**, (4) **session-end extraction**, (5) **built-in-write mirroring**, (6) **per-provider tool suites** (e.g. Honcho's 5 tools, Holographic's `fact_store` 9-action tool), (7) **pre-compression extraction** (ByteRover) and **context-fencing** (Supermemory) (H:9474-10010). AEGIS lacks prefetch, session-switch flush via temp-agent turn (H:35137-35168), and the rich per-provider tools.

**Build**: (L) implement the full provider lifecycle (`on_session_start` context injection → `prefetch` → `on_turn` sync → `on_session_end` extraction → `on_pre_compress` extraction), and build out Holographic (pure-local SQLite+FTS, zero deps — best first target) and Honcho/Mem0/Supermemory SDK providers with their named tools.

### D. Skills, hub, curator

skill_manage (`tools/skill_manage.py`) and progressive disclosure are **PARITY** (confirm `write_file`/`remove_file` actions per H:8420). 

**Skills hub MISSING** (H:8470-8660): nine sources (official/skills-sh/well-known/github taps/clawhub/lobehub/browse-sh/claude-marketplace/url), `browse/search/inspect/install/check/update/audit/uninstall/tap add/publish/snapshot`, security-scan-on-install, `skills.sh.json` category groupings. AEGIS `marketplace.py` is minimal.

**Curator LLM pass MISSING** (the in-scope curator detail): `curator.py` makes **zero** provider calls. It does deterministic `active→stale→archived` transitions and a duplicate-name heuristic (`_find_duplicates`, `_classify_removed`). Hermes' curator phase-2 (H:8904-8916) **forks an `AIAgent` on `auxiliary.curator`, `max_iterations=8`**, that surveys agent-created skills, reads them with `skill_view`, and proposes keep/patch/consolidate/archive — with the package-integrity consolidation rule (re-home `references/scripts/templates` or archive whole, never flatten just SKILL.md).

**Curator telemetry DIVERGES** (`skills.py:201`): AEGIS tracks `{count, last_used}` only. Hermes tracks `use_count` (loaded into prompt), `view_count` (`skill_view` called), `patch_count` (`skill_manage patch/edit/write_file/remove_file`), plus `last_*_at` and `state`/`pinned`/`archived_at` (H:9080-9106).

**Build**: (M) add the curator aux-model review fork on `auxiliary.curator` with the consolidation/package-integrity prompt; (S) split telemetry into view/use/patch counters and surface the LRU-5 in `curator status`.

### E. Sessions

`session.py` stores sessions as JSON blobs (`messages: list[Message]`) with an FTS5 mirror table and `parent_id` lineage + forking (`:158-329`). Hermes uses a **normalized `messages` table** with per-row `token_count/finish_reason/reasoning/codex_*` columns and a `sessions` table carrying **billing/token/cost** columns (`input/output/cache_read/cache_write/reasoning_tokens`, `estimated_cost_usd`, `pricing_version`), schema-versioned to v11 with WAL + jittered retry (H:35326-35370).

**DIVERGES**: AEGIS recall anchors on message *index*, not durable row IDs (acknowledged in prior audit). No auto-title-in-lineage (`Fix Build #2`, H:35434), no recap-on-resume injection. session_search shapes are at parity post-branch except cross-profile.

**Build**: (M) migrate to a normalized messages table with token/cost columns for accurate session-search anchors, billing analytics in the dashboard, and lineage titling; (S) add resume recap + `get_next_title_in_lineage`.

### F. Providers / auth / routing

Catalog (`providers/registry.py`): ~30 API-key specs (anthropic, openai, codex variants, google, openrouter, groq, deepseek, xai, mistral, together, hf, novita, zai, kimi, minimax, nvidia, dashscope, stepfun, cerebras, perplexity, fireworks, hyperbolic, sambanova, vllm, ollama, lmstudio) + custom + plugin registration. Fallback chains (`fallback.py`) **PARITY** including action map (auth/billing→rotate).

**OAuth MISSING** (`auth.py`): PKCE for Anthropic/OpenAI/Codex/Google only. Hermes adds Qwen (Portal), MiniMax, xAI/SuperGrok, GitHub Copilot, and **Nous Portal device-code "one login"** covering 300+ models + **Tool Gateway** (web search/image/TTS/browser without separate keys) (H:24567-24735, H:25726).

**Credential pools MISSING**: only `config.py:377 "credential_pools": {}`. No `credential_pool.py` manager, no rotation strategies (fill_first/round_robin/least_used/random), no 429→retry-once→rotate, 402→24h-cooldown, 401→refresh-then-rotate, no auto-discovery/seeding, no `hermes auth` CLI, no subagent sharing (H:28155-28360).

**Auxiliary slots DIVERGES** (`config.py:243`): minimal `auxiliary{}`. Hermes exposes 9 named task slots (vision/web_extract/approval/tts_audio_tags/compression/skills_hub/mcp/triage_specifier/curator/kanban_decomposer), each provider/model/base_url/api_key/timeout, selectable via picker (H:3817-4040). **OpenRouter routing** `provider_routing`/`min_coding_score`/Pareto via `extra_body` absent.

**Build**: (M each) credential pool manager + `aegis auth`; Qwen/MiniMax/xAI/Copilot OAuth; Nous Portal device-code + Tool Gateway routing; expand `auxiliary.*` to per-task slots reusing the existing aux-model resolution; add OpenRouter `extra_body` passthrough + Bedrock/Azure providers.

### G. Tools & toolsets

39 vs ~71. AEGIS has strong coverage: bash, file ops, apply_patch, search/glob, http_request/web_fetch/web_search, browser, computer, cloud_browser/cloud_image/generate_image, execute_code, lsp, process, memory, session_search, skill/skill_manage, spawn_subagent, mixture_of_agents, cronjob, schedule_task, kanban, todo_write, clarify, send_message, speak, transcribe, system_status, agent_state, tool_search, dependency_audit, download, github.

**Genuinely missing high-value tools** (H:40774): `vision_analyze` (aux-model image analysis distinct from generic browser vision), `web_extract` (aux-model summarizing extractor — AEGIS only has raw `web_fetch`), `video_generate`/`video_analyze`, `x_search` (xAI-gated), Home Assistant `ha_*` (4 tools), and the 10-tool browser CDP suite (AEGIS has a single `browser` tool). Toolset introspection (`/v1/toolsets` returning resolved tool lists per platform, H:27138) is partial — AEGIS tools carry `groups`/`groups=["automation"]` but there's no toolset registry with per-platform enable config UI.

**Build**: (M) add `vision_analyze` + `web_extract` (both just aux-model calls — cheap wins), `x_search` (if xAI creds), `ha_*` (gateway integration); (M) a first-class toolset registry + per-platform config + `/api/toolsets` introspection.

### H. Messaging gateway

8 adapters (`channels.py:180-200`: telegram/discord/slack/signal/matrix/email/webhook/ntfy) vs Hermes 20+ (adds whatsapp, sms/twilio, mattermost, homeassistant, dingtalk, feishu/lark, wecom, weixin, bluebubbles/imessage, qqbot, yuanbao, teams via msgraph). Busy-guard + delivery queue **PARITY**.

**Per-platform rich behavior DIVERGES**: AEGIS `PLATFORM_HINTS` (`context.py:107`) is *prompt text only*. Hermes implements native files/images/audio, **voice-message transcription on receive**, **threads/forum topics**, **typing indicators**, **streaming status-message edits-in-place**, **native slash + app commands** (incl. per-skill slash commands H:20223), **reactions**, **channel directory** (chat-id→name for cron delivery, H:gateway-internals), **per-channel prompts/skill bindings**, and richer per-platform authz. The `MEDIA:` tag convention exists in AEGIS hints but the receiving/transcription/typing/edit machinery does not.

**Build**: (L) prioritize WhatsApp + Mattermost + Home Assistant + SMS by user need; (L) add per-platform: receive-side voice transcription, typing indicator, streaming status edits, channel directory + home-channel resolution for cron, native slash commands. Add voice/media parity to existing adapters before adding more.

### I. Cron / automation

`cron.py` jobs carry `last_run`/`run_at`/interval/clock schedules + skill preload + script context (`automation.py`) + `[SILENT]` + queue/direct delivery + the branch's `cronjob` tool actions. 

**DIVERGES/MISSING vs H:11619-11952 + cron-internals**: no typed `state`/`next_run_at`/`last_error`/`delivery_error`/`runs[]` records; no **missed-run recovery**; no **scheduler file/process lock**; no **no-agent script-only mode** (run a script, deliver stdout, no LLM); no **`context_from` job chaining**; no **recursive-cron guard** (disallow cron creating cron inside a cron session); no **blueprints** (skills that declare a schedule → suggestions, H:37499); no per-job `enabled_toolsets` resolution order (H:11815). Cron deliveries should *not* mirror into gateway session history (H:delivery-path) — verify.

**Build**: (M) upgrade to a typed job store with the above state fields + run-log dir; (M) add lock, missed-run recovery, no-agent mode, recursive guard, `context_from`, blueprints.

### J. Dashboard / web control plane

`dashboard_fastapi.py` now has **225 route decorators** (prior audit's "33" is stale — the parallel branch expanded heavily; it now exceeds Hermes' 169). Login/cookies/Basic-auth/host-peer-guards/WS-tickets present. 

**DIVERGES**: gaps are *quality* not breadth — OAuth-login providers (vs token/Basic/cookie), **profile-scoped URL routing** (Hermes dashboards are profile-aware), provider-OAuth start/submit/poll/revoke routes, self-update apply, ops doctor/security/backup/migrate, richer gateway onboarding/live-probe forms, and `/v1/capabilities`-style feature advertisement. The OpenAI-compatible `aegis serve` should expose `/v1/capabilities`, `/v1/runs`, `/api/sessions/*` REST control, and `X-Hermes-Session-Key`-equivalent memory scoping (H:26838-27160).

**Build**: (M) profile-aware routing + OAuth-login UX + provider-OAuth route group; (S) `/v1/capabilities` + runs API + session-key header on the serve API.

### K. MCP & ACP

MCP client (`mcp/client.py`) + server (`mcp/server.py`) **PARITY** for transport. **MCP catalog MISSING** (H:25776-25895): no curated catalog, `mcp install <name>`, credential prompting, server probe + **tool-selection checklist** writing `tools.include`, manifest version compat, `${ENV_VAR}` substitution, mTLS.

ACP (`acp.py`) does `session/load` history replay, diff content blocks, tool-kind mapping — **DIVERGES**: missing the **three-tier approval** (`allow_once`/`allow_session`/`allow_always` → permanent allowlist, H:26716-26740), and verify `session/list`+`fork`+context-usage/model-picker state.

**Build**: (M) MCP catalog + install/probe/checklist; (M) ACP allow_session tier + session list/fork.

### L. Personalities / context-file chain / security / profiles

SOUL.md + personalities (`context.py:_persona`) and `Workspace.rules()` are **PARITY**; verify the first-match priority `.aegis.md/.hermes.md (→git root) > AGENTS.md > CLAUDE.md > .cursorrules` and 20k-char truncation + injection scan (H:34375-34470). Security scanners (`security_scan.py`, `net_safety.py`, `redact.py`) **PARITY**; **supply-chain advisory checking** (H:7494-7542) DIVERGES — `dependency_audit` tool exists but confirm advisory-DB checks on writes.

**Profiles MISSING** (H:5608-5897): no first-class multi-profile (`aegis profile create/use`, profile-scoped HERMES_HOME, per-profile gateways/skills/memory/tokens, distributions-as-git-repos). This blocks cross-profile session_search and profile-aware dashboard. **(L)** — large but foundational for multi-tenant.

### M. Ops

`ops.py`/`doctor.py`/`backup.py`/`checkpoints.py` exist. Gaps: typed **self-update apply** with config-option diff (H:705-918), **`migrate`** command (H:38209), **debug-share** bundle, **prompt-size/dump**, deeper doctor. **(S each.)**

### N. Media / misc (kanban, goals, voice, vision, video, x_search, handoff, deliverable)

Kanban (`kanban.py`) + goals/Ralph (`goals.py`) + handoff (`handoff.py`) present — **PARITY** (verify kanban runs/heartbeat/circuit-breaker/spawn_failed states H:13106-13497). Voice (`tools/voice.py`) lacks Discord VC + gateway voice-reply + receive-side STT. Vision lacks `vision_analyze` aux + image paste. Video gen + x_search + first-class deliverable-mode **MISSING** (deliverable language is only in the prompt `context.py:81`).

---

## Prioritized overhaul roadmap

Effort key: **S** ≈ ≤1 day, **M** ≈ 2–4 days, **L** ≈ 1–2 weeks.

### P0 — Functional holes in in-scope subsystems (do first)
- **Curator LLM consolidation pass** (M) — fork aux agent on `auxiliary.curator`, `max_iterations≈8`, with package-integrity consolidation rules. `curator.py` (§D). *Currently 0 LLM calls — the headline gap.*
- **Curator telemetry split** view/use/patch counters + state/pinned/archived_at (S) — `skills.py`, `curator.py` (§D).
- **memory.write_approval** staged-write gate verification/closure (S) — `memory.py`, `skill_manage` (§C/D).

### P1 — Provider/account resilience (highest user-visible value)
- **Credential pools** + `aegis auth` + rotation/cooldown/refresh + subagent sharing (M) — new `credential_pool.py` (§F).
- **Auxiliary task slots** expanded to per-task provider/model/base_url + picker (M) — `config.py`, `auth.py` resolver (§F).
- **Prompt-caching TTL config + OpenRouter Claude path + skills breakpoint** (S) — `anthropic.py`, `chat_completions.py` (§B).
- **vision_analyze + web_extract tools** (cheap aux-model wins) (S) — `tools/` (§G).

### P2 — Cron + gateway foundations
- **Typed cron job store** (state/next_run/last_error/runs[]) + run-log dir (M) — `cron.py` (§I).
- **Cron lock + missed-run recovery + no-agent mode + recursive guard + context_from** (M) (§I).
- **Channel directory + home-channel resolution** (M) — `gateway/` (§H).
- **Gateway rich behavior**: receive-side voice transcription, typing indicator, streaming status-edit, native slash commands (L) (§H).

### P3 — Memory providers + skills hub + sessions
- **Full memory-provider lifecycle** (context-inject/prefetch/sync/session-end-extract/pre-compress) + Holographic (local) first, then Honcho/Mem0/Supermemory SDK providers with named tools (L) (§C).
- **Skills hub** sources + browse/search/install/check/update/audit + security-scan-on-install (L) (§D).
- **Normalized session messages table** + token/cost columns + lineage titling + resume recap (M) (§E).

### P4 — Breadth & ecosystem
- **OAuth flows** Qwen/MiniMax/xAI/Copilot + **Nous Portal device-code + Tool Gateway** (M) (§F).
- **MCP catalog** install/probe/checklist (M) (§K).
- **ACP** allow_session tier + session list/fork (M) (§K).
- **Provider catalog** Bedrock/Azure + OpenRouter routing extra_body (M) (§F).
- **First-class profiles / multi-tenant** (L) — unblocks cross-profile recall + profile-aware dashboard (§L).
- **New platforms** WhatsApp/Mattermost/Home Assistant/SMS (L) (§H).
- **Toolset registry + per-platform config introspection** (M) (§G).
- **Ops**: self-update apply, `migrate`, debug-share, prompt-dump, deeper doctor (S each) (§M).
- **Video gen / x_search / deliverable-mode tool** (M) — only if user-driven (§N).

### Deferred (cosmetic / low ROI)
- TUI skins, desktop chrome, LaTeX rendering, i18n.

---

## Top-15 most important gaps (ranked)

1. **Curator has no LLM consolidation pass** — `curator.py` is deterministic-only (0 provider calls) vs Hermes' aux-agent fork (H:8904).
2. **Credential pools absent** — only a `config.py:377` stub; no rotation/cooldown/refresh/share ladder (H:28155).
3. **External memory providers are HTTP/JSONL shells** — no prefetch/session-switch/compression-extraction/per-provider tools (9 providers, H:9457).
4. **Skills hub missing** — no 9-source browse/install/audit/taps (H:8470).
5. **OAuth catalog narrow** — no Qwen/MiniMax/xAI/Copilot/Nous-Portal "one login"/Tool Gateway (H:24567, H:25726).
6. **Tool registry 39 vs ~71**; missing `vision_analyze`/`web_extract`/`x_search`/`video_*`/`ha_*`/browser-CDP (H:40774).
7. **Cron lacks typed store + missed-run recovery + lock + no-agent mode + recursive guard + blueprints** (H:11619).
8. **Gateway 8 vs 20+ platforms** and no receive-side voice/typing/streaming-edits/native-slash/channel-directory (H:34935).
9. **Auxiliary model slots minimal** — no per-task provider/model picker for 9 named tasks (H:3817).
10. **Session schema un-normalized** — no per-message rows or billing/token/cost columns; recall anchors on index not row-id (H:35326).
11. **MCP catalog/install/probe/checklist missing** (H:25776).
12. **Skill telemetry tracks only count+last_used**, not view/use/patch (H:9080).
13. **Prompt caching**: no TTL config, no skills breakpoint, native-Anthropic-only (no OpenRouter Claude path) (H:34848).
14. **No first-class profiles / multi-tenant** — blocks cross-profile recall + profile-aware dashboard (H:5608).
15. **ACP missing allow_session approval tier**; verify session list/fork (H:26716).

---

**Report path:** `/home/alienai/aegis/docs/hermes-aegis-overhaul-2026-06-13.md`
