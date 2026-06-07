# Gateway & Channels

One agent serving many surfaces.

```bash
aegis gateway --channels telegram,discord,slack,signal,matrix,email,webhook
```

| Channel | Needs |
|---|---|
| `telegram` | `TELEGRAM_BOT_TOKEN` |
| `discord` | `DISCORD_BOT_TOKEN` (`pip install discord.py`) |
| `slack` | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` (`pip install slack_bolt`) |
| `signal` | the `signal-cli` binary + `SIGNAL_CLI_ACCOUNT` |
| `matrix` | `MATRIX_HOMESERVER/USER/PASSWORD` (`pip install matrix-nio`) |
| `email` | `EMAIL_IMAP_HOST/SMTP_HOST/ADDRESS/PASSWORD` |
| `webhook` | a bridge that POSTs to `:18790/in` (e.g. a WhatsApp/Baileys bridge) |

## Features

- **Voice memos** — audio attachments are transcribed before the agent sees them.
- **Durable delivery** — a SQLite outbox queues replies and **retries with backoff**;
  pending messages survive restarts.
- **Authorization** — unknown users must pair: `aegis pairing approve <platform> <code>`.
  Group **mention gating** via `gateway.require_mention`.
- **Session isolation** — `gateway.session_mode`: `main | per_channel | per_channel_peer
  | per_peer`.

## Scheduled delivery

```bash
aegis cron add "@daily" "summarize today's commits"
aegis cron run        # runs jobs; the gateway also ticks them
```
