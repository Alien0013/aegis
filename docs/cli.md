# CLI Reference

Run any command with `-h` for details. `aegis` alone opens the REPL.

## Chat
- `aegis [chat]` — interactive REPL
- `aegis chat -q "…"` — one-shot (`--model`, `--provider`, `--image`, `--resume`,
  `--continue`, `--worktree/-w`, `--yolo`)
- `aegis batch FILE` — run a prompt per line

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
