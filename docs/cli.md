# CLI Reference

Run any command with `-h` for details. `aegis` alone opens the REPL.

## Chat
- `aegis [chat]` — interactive REPL
- `aegis chat -q "…"` — one-shot (`--model`, `--provider`, `--image`, `--resume`,
  `--continue`, `--worktree/-w`, `--yolo`)
- `aegis batch FILE` — run a prompt per line

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
- `aegis model [list|set <provider> [<model>]]`
- `aegis auth [status|login <p>|logout <p>]`
- `aegis config [get|set|path|dump|check|migrate]`
- `aegis doctor [--fix]`, `aegis update [--check|--branch]`, `aegis uninstall [--purge]`
- `aegis completion bash|zsh|fish`

## Tools, skills, memory, learning
- `aegis tools [list|status]`
- `aegis skills [list|view|new|install|search|remove|hub] …`
- `aegis memory [show|add|clear]`
- `aegis learn [review|list|apply|reject]`
- `aegis curator [status|review|prune|archive|restore]`

## Sessions & recall
- `aegis sessions [list|show|rm|summarize|search]`
- `aegis checkpoints [list|rollback|clear]`
- `aegis trajectory [stats|export|compress]`

## Services
- `aegis gateway --channels …`
- `aegis daemon [status|install|start|stop|restart|remove]` — user systemd services
- `aegis serve [--port]` — OpenAI-compatible API
- `aegis mcp [list|add|remove|test|serve]`
- `aegis cron [list|add|rm|run]`, `aegis webhook [list|add|remove|serve]`
- `aegis dashboard`, `aegis acp`, `aegis pairing`, `aegis background`

## Ops & security
- `aegis security audit`, `aegis debug share`, `aegis secrets bitwarden`
- `aegis hooks [list|test]`, `aegis kanban …`
