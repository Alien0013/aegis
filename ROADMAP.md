# AEGIS Roadmap

The path from "solid harness" to "best-in-class". Phases are ordered by
value-per-line: reliability and cost first, capability second, ops polish third.
Each item is small enough to land with tests in one sitting unless marked (L).

## Phase 1 — Reliability & cost core ✅ DONE

- [x] **Tool-loop guardrails** — per-turn `(tool, args)` signatures; warn after
  N identical failures, hard-block after M, no-progress warnings. (`agent/guardrails.py`)
- [x] **Conversation cache breakpoints** — cache markers on the last 3 wire
  messages + system (~75% input-cost cut). (`providers/anthropic.py`)
- [x] **Fuzzy edit matching chain** — line-trimmed → whitespace-collapsed →
  indentation-blind → block-anchor, unique-match only. (`tools/fuzzy.py`)
- [x] **File freshness tracking** — read-stamps; stale-write warnings across
  the agent and parallel subagents. (`tools/file_state.py`)
- [x] **Structured compaction handoff** — sectioned template (request /
  decisions / files / errors / completed / next step). (`agent/compaction.py`)
- [x] **Compaction quality** — token-budgeted tail (scales with the window,
  not a fixed message count), iterative summary fold (prior summary updated,
  no summary-of-summary drift), deterministic anchor fallback on summarizer
  failure, summarizer input capped to the aux model's window. (`agent/compaction.py`)
- [x] **File-write path safety** — sensitive paths require approval. (`tools/file_safety.py`)
- [x] **Pre-update state snapshot** — auto before `aegis update`;
  `aegis snapshot create|restore|prune|list`. (`backup.py`)

## Phase 2 — Context economy & safety depth ✅ (one item open)

- [x] **Out-of-band tool-result storage** — oversized outputs spill to disk
  with a pointer (already present; pruned after 7 days). (`agent/loop.py`)
- [x] **Deferred tool schemas** — rarely-used tools ship name-only in a stable
  system-prompt index; `tool_search` activates the full schema on demand
  (`tools.defer_schemas`, `tools.deferred`). (`agent/agent.py`, `tools/devtools.py`)
- [x] **Rich `@references`** — `@file:path:10-20`, `@folder:`, `@git:`, `@url:`,
  `@diff`, `@staged`, sensitive-path blocklist. (`cli/repl.py`)
- [ ] **Subdirectory hints** — when the agent starts working in a new directory,
  lazily inject that directory's AGENTS.md/.cursorrules into the tool result
  (cache-safe, no system-prompt rebuild).
- [x] **URL/web safety policy** — `web.allow_domains`/`deny_domains`. (`net_safety.py`)
- [x] **Approval granularity** — "allow always" (answer `a`) persists for the
  session. (`tools/permissions.py`)
- [x] **Rate-limit telemetry** — `x-ratelimit-*` captured, shown in `/usage`. (`ratelimit.py`)
- [x] **`/compress here [N] | focus <topic>`** — user-chosen boundary + focus. (`cli/repl.py`)

## Phase 3 — Agentic depth ✅ DONE

- [x] **Async delegation with announce-back** — `spawn_subagent background:true`
  runs in the background and posts the result into the chat (gateway) or the
  live feed (CLI). (`tools/agentic.py`, `background.py`)
- [x] **Typed subagents** — `agent_type: explore|plan|review` (read-only tool
  whitelists + role preambles) and `continue_id` follow-ups with context intact.
  (`tools/agentic.py`)
- [x] **Background task re-invocation** — `process start` and background
  subagents queue completion wakeups; the next turn folds them in, and gateway
  chats get an announce-back. (`agent/wakeups.py`, `tools/process.py`)
- [x] **Mixture-of-agents tool** — `mixture_of_agents` fans one prompt across
  2–5 models in parallel and synthesizes one answer. (`tools/agentic.py`)
- [x] **Checkpoint depth** — each turn's edit batch auto-checkpoints as ONE
  unit (new files tracked, rollback deletes them); `/diff` + `aegis checkpoints
  diff` preview. On by default. (`checkpoints.py`, `agent/loop.py`)
- [x] **Kanban worker lanes** — `kanban.workers` N parallel lane workers;
  pre-assigning a ready card to `lane-K` pins it to that worker. (`kanban_auto.py`)

## Phase 4 — Gateway & ops polish ✅ DONE

- [x] **Admin/user command tiers** — `gateway.admins` + `gateway.user_commands`;
  empty admins = single-user (everyone admin). (`gateway/runner.py`)
- [x] **Shutdown forensics** — SIGTERM/SIGINT logged to `logs/shutdowns.jsonl`. (`gateway/runner.py`)
- [x] **Restart notifications** — unclean previous run detected at gateway
  start (START/shutdown pairing in `logs/shutdowns.jsonl`) and DM'd to admins.
  (`doctor.py`, `gateway/runner.py`)
- [x] **Cross-platform `/handoff`** — `/handoff <platform> <chat_id>` queues
  the session; the gateway adopts it (full history) on the chat's next message.
  (`handoff.py`)
- [x] **Tips engine** — one-time contextual feature hints. (`firstrun.py`)
- [x] **Doctor depth** — `aegis doctor --probe`: live one-token provider call
  with latency + Telegram/Discord/Slack token validation. (`doctor.py`)
- [x] **Multi-profile gateways** — `gateway.profiles` per-platform overlay
  (personality / model / provider) on one gateway process. (`gateway/runner.py`)

## Todo staleness nudge ✅
- [x] System-reminder when the todo list goes stale mid-task. (`agent/loop.py`)

## Explicitly out of scope

Desktop app, terminal-UI multiplexer, dashboard JS plugin SDK, CLI skins,
vendor-specific messaging adapters (DingTalk, Feishu, WeCom, LINE, QQ,
Yuanbao, BlueBubbles, Teams, Mattermost, SMS, Home Assistant), Spotify,
X search, subscription proxy, AWS Bedrock/Azure native transports, video
generation, i18n. Niche or enormous relative to their value for a personal
harness — revisit only on demand.
