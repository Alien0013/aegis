# AEGIS Roadmap

The path from "solid harness" to "best-in-class". Phases are ordered by
value-per-line: reliability and cost first, capability second, ops polish third.
Each item is small enough to land with tests in one sitting unless marked (L).

## Phase 1 — Reliability & cost core

- [ ] **Tool-loop guardrails** — track per-turn `(tool, args)` signatures; warn
  after N identical failures, hard-block with a synthetic "change strategy"
  result after M; also catch no-progress loops (same result hash repeatedly).
- [ ] **Conversation cache breakpoints** — cache markers on the last 3
  non-system messages in addition to system+tools (~75% input-cost cut on
  multi-turn Anthropic sessions).
- [ ] **Fuzzy edit matching chain** — when `edit_file`'s exact match fails, try
  line-trimmed → whitespace-normalized → indentation-flexible → escape-
  normalized → block-anchor strategies before giving up (auto-recovers the
  most common LLM edit failure).
- [ ] **File freshness tracking** — read-stamps per file (mtime at read);
  `edit_file`/`write_file` warn when the file changed since it was read, and
  refuse edits to files never read this session. Protects against stale-copy
  clobbering, including between parallel subagents.
- [ ] **Structured compaction handoff** — replace the "summarize, be terse"
  prompt with a structured template: primary request → key concepts → files
  touched (with snippets) → errors & fixes → pending tasks → current state →
  next step. Long sessions survive compaction without losing the thread.
- [ ] **File-write path safety** — deny-by-default writes to sensitive paths
  (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, shell rc files, AEGIS home
  internals outside workspace/) unless explicitly approved.
- [ ] **Pre-update state snapshot** — `aegis update` snapshots config/state
  first; `aegis snapshot create|restore|prune` to manage them.

## Phase 2 — Context economy & safety depth

- [ ] **Out-of-band tool-result storage** — store oversized tool outputs to
  disk, keep a head + pointer in context, let the agent page in more.
- [ ] **Deferred tool schemas** — rarely-used tools ship name-only; `tool_search`
  activates the full schema on demand (cuts per-call token overhead).
- [ ] **Rich `@references`** — `@file:path:10-20`, `@folder:`, `@git:`, `@url:`,
  `@diff`, `@staged`, with a sensitive-path blocklist on references.
- [ ] **Subdirectory hints** — when the agent starts working in a new directory,
  lazily inject that directory's AGENTS.md/.cursorrules into the tool result
  (cache-safe, no system-prompt rebuild).
- [ ] **URL/web safety policy** — domain allow/deny policy for web_fetch beyond
  the SSRF guard; threat-pattern screening of fetched content.
- [ ] **Approval granularity** — "allow always for this tool/command-prefix"
  persistence, so `ask` mode stops re-asking for things the user blessed.
- [ ] **Rate-limit telemetry** — capture `x-ratelimit-*` headers; show
  remaining requests/tokens in `/usage` and the dashboard.
- [ ] **`/compress here [N] | focus <topic>`** — user-chosen compression
  boundary and focus.

## Phase 3 — Agentic depth

- [ ] **Async delegation with announce-back** — subagents that run in the
  background and post their result into the originating chat/session when
  done (gateway + CLI), instead of blocking the turn. (L)
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

- [ ] **Admin/user command tiers** — per-platform admin allowlist; regular
  users get a configurable subset of slash commands.
- [ ] **Shutdown forensics** — on SIGTERM/SIGINT capture who/what killed the
  gateway (fast snapshot + detached ps walk) for post-mortems.
- [ ] **Restart notifications** — after an update/crash restart, tell the
  last-active chats the gateway is back.
- [ ] **Cross-platform `/handoff`** — move a CLI session to a messaging
  platform (and back) with history replay. (L)
- [ ] **Tips engine** — one-time contextual hints that teach features at the
  moment they're relevant (extends firstrun.py).
- [ ] **Doctor depth** — provider probes, channel token validation, service
  health, disk/db integrity in `aegis doctor`.
- [ ] **Multi-profile gateways** — several isolated agent profiles served by
  one gateway process. (L)

## Explicitly out of scope

Desktop app, terminal-UI multiplexer, dashboard JS plugin SDK, CLI skins,
vendor-specific messaging adapters (DingTalk, Feishu, WeCom, LINE, QQ,
Yuanbao, BlueBubbles, Teams, Mattermost, SMS, Home Assistant), Spotify,
X search, subscription proxy, AWS Bedrock/Azure native transports, video
generation, i18n. Niche or enormous relative to their value for a personal
harness — revisit only on demand.
