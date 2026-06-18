# CLI Reference

Run any command with `-h` for details. `aegis` alone opens the REPL.

## Chat
- `aegis [chat]` — interactive REPL
- `aegis chat -q "…"` — one-shot (`--model`, `--provider`, `--image`, `--resume`,
  `--continue`, `--worktree/-w`, `--yolo`)
- `aegis batch FILE` — run a prompt per line

Prompt references work in REPL, one-shot, SDK/API/gateway,
and automation: `@file:path[:10-20]`, `@folder:path`, `@diff`, `@staged`,
`@git:<ref>`, `@url:https://...`, and `@mcp:<server>:<resource-uri>`. Each
expansion records metadata on the session for replay/debugging.

REPL and one-shot CLI turns use the same shared surface runner as SDK,
dashboard chat, gateway, cron, webhooks, and `aegis serve`, so terminal turns
also write durable run rows, trace ids, surface metadata, and prompt-part
debugging metadata.

The REPL uses the same terminal turn lifecycle everywhere: `/goal` startup,
automatic goal continuations, `/retry`, manual `/compress`, run history,
context-reference expansion, and persistence all go through the shared runner.

## Persistent goals (`/goal`)

A standing objective that survives turns (the Ralph loop). After every turn a
small judge call decides done-or-continue; on continue the agent automatically
takes the next step — until done, paused, or the budget (`goals.max_turns`,
default 20) runs out. Works in the REPL and on every gateway channel.

- `/goal <text>` — set the goal and start working
- `/goal` / `pause` / `resume` / `clear`
- `/subgoal <text>` — add acceptance criteria mid-loop (`remove <N>`, `clear`)

State lives in the session, so it survives resume. Any real message you send
preempts the loop; `/status` shows the active goal plus a local session recap.

## Setup & config
- `aegis setup` / `aegis onboard` — wizard
- `aegis model [list|doctor|set <provider> [<model>]]`
- `aegis auth [status|login <p>|logout <p>]`
- `aegis config [summary|show]` — Hermes-style terminal summary with paths,
  masked secret status, model/runtime settings, and edit/setup hints
- `aegis config edit [--secrets]` — open `config.yaml` or the profile `.env`
  in `$EDITOR`, with backup/validation for YAML edits
- `aegis config [get|set|path|env-path|paths|dump|check|migrate|setup]`
- `aegis doctor [--fix] [--probe]` — `--probe` makes a live one-token provider call
  (reports latency) and validates channel tokens (Telegram/Discord/Slack)
- `aegis update [--check|--branch]`, `aegis uninstall [--purge]`
- `aegis completion bash|zsh|fish`

## Desktop
- `aegis desktop` — copy the bundled Electron app to `~/.aegis/desktop`, run
  `npm install` when needed, and launch the native window
- `aegis desktop --install-only` — prepare/update the desktop app without launching
- `aegis desktop --reinstall` — force `npm install` before launching
- `aegis desktop --sandbox` — opt into Chromium's Linux sandbox when your
  `chrome-sandbox` helper is configured

## Tools, skills, memory, learning
- `aegis tools [list|status]`
- `aegis skills [list|view|new|install|search|remove|hub] …`
- `aegis memory [show|add|clear]`
- `aegis learn [review|list|apply|reject]`
- `aegis curator [status|review|prune|archive|restore]`

## Sessions & recall
- `aegis sessions [list|show|rm|summarize|search]` — `show` prints runtime
  metadata, trace id, prompt hash/token estimate, prompt part count, and the
  transcript for debug/replay.
- `aegis checkpoints [list|diff|rollback|clear]` — each turn's edit batch is
  auto-checkpointed as one unit; `diff` previews it, `rollback` undoes it
  (files the batch created are removed). In the REPL: `/diff`, `/rollback`.
- `aegis trajectory [stats|export|compress]`
- `aegis trace [list|show|export] [--session ID] [--status ok|error] [--json]` — inspect/export traces
  for turns, provider calls, tool calls, compaction, and related run activity
- `aegis eval [list|run SUITE.jsonl|show RUN_ID] [--json]` — run provider-free replay evals
  against stored sessions or traces

## Services
- `aegis gateway --channels …`
- `aegis daemon [status|install|start|stop|restart|remove]` — user systemd services
- `aegis serve [--port]` — OpenAI-compatible API
- `aegis rpc` — local JSON-RPC agent surface over stdio for IDE/platform bridges
- `aegis mcp [list|add|remove|test|serve|catalog|install|tools]`
- `aegis cron [list|add|rm|run]`, `aegis webhook [list|add|remove|serve]`
- `aegis dashboard`, `aegis acp`, `aegis pairing`, `aegis background`
- `aegis plugins [list|doctor|path|install|enable|disable|remove]`

ACP `session/prompt` responses include `sessionId`, `runId`, `traceId`, and
`turnId` when available, matching the dashboard/RPC breadcrumb model for editor
deep-links and replay.

`aegis batch FILE` runs one prompt per non-comment line. Each prompt records a
separate `batch` run with `batch_source`, `batch_index`, and `batch_total`
metadata for dashboard filtering and replay.

## Ops & security
- `aegis security audit`, `aegis debug share`, `aegis secrets bitwarden`
- `aegis hooks [list|test]`, `aegis kanban …`

## Session handoff

`/handoff <platform> <chat_id>` (REPL) moves the current session — full history —
to a messaging channel. The gateway adopts it the next time that chat sends a
message (the chat gets a ping immediately if the gateway is running).

## Cockpit slash commands

The REPL includes the product-facing shortcuts used by the terminal and dashboard
cockpit:

- `/reasoning off|none|summary|live|minimal|low|medium|high|xhigh`
- `/busy queue|steer|interrupt`
- `/resume <session-id-or-title>`
- `/branch`
- `/agents`
- `/trace [trace-id]`
- `/evals [run-id]`
