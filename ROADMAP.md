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
- [x] **File-write path safety** — sensitive paths require approval. (`tools/file_safety.py`)
- [x] **Pre-update state snapshot** — auto before `aegis update`;
  `aegis snapshot create|restore|prune|list`. (`backup.py`)

## Phase 2 — Context economy & safety depth

- [x] **Out-of-band tool-result storage** — oversized outputs spill to disk
  with a pointer (already present; pruned after 7 days). (`agent/loop.py`)
- [ ] **Deferred tool schemas** — rarely-used tools ship name-only; `tool_search`
  activates the full schema on demand (cuts per-call token overhead).
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

## Phase 3 — Agentic depth

- [x] **Async delegation with announce-back** — `spawn_subagent background:true`
  runs in the background and posts the result into the chat (gateway) or the
  live feed (CLI). (`tools/agentic.py`, `background.py`)
- [ ] **Typed subagents** — named agent types (explore = read-only fan-out,
  plan = architect, general) with per-type toolsets and prompts; continue a
  previous subagent with its context intact.
- [ ] **Background task re-invocation** — long-running bash/process completion
  re-enters the agent loop with the output (event-driven, not polled).
- [ ] **Mixture-of-agents tool** — fan one prompt across several models,
  synthesize the answers.
- [ ] **Checkpoint depth** — auto-checkpoint before each edit batch with diff
  preview and selective restore. (L)
- [ ] **Kanban worker lanes** — parallel kanban workers with lane assignment
  and a swarm mode. (L)

## Phase 4 — Gateway & ops polish

- [x] **Admin/user command tiers** — `gateway.admins` + `gateway.user_commands`;
  empty admins = single-user (everyone admin). (`gateway/runner.py`)
- [x] **Shutdown forensics** — SIGTERM/SIGINT logged to `logs/shutdowns.jsonl`. (`gateway/runner.py`)
- [ ] **Restart notifications** — after an update/crash restart, tell the
  last-active chats the gateway is back.
- [ ] **Cross-platform `/handoff`** — move a CLI session to a messaging
  platform (and back) with history replay. (L)
- [x] **Tips engine** — one-time contextual feature hints. (`firstrun.py`)
- [ ] **Doctor depth** — provider probes, channel token validation, service
  health, disk/db integrity in `aegis doctor`.
- [ ] **Multi-profile gateways** — several isolated agent profiles served by
  one gateway process. (L)

## Todo staleness nudge ✅
- [x] System-reminder when the todo list goes stale mid-task. (`agent/loop.py`)

## Explicitly out of scope

Desktop app, terminal-UI multiplexer, dashboard JS plugin SDK, CLI skins,
vendor-specific messaging adapters (DingTalk, Feishu, WeCom, LINE, QQ,
Yuanbao, BlueBubbles, Teams, Mattermost, SMS, Home Assistant), Spotify,
X search, subscription proxy, AWS Bedrock/Azure native transports, video
generation, i18n. Niche or enormous relative to their value for a personal
harness — revisit only on demand.
