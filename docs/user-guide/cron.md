# Scheduled Work and Cron

Cron jobs are durable automation entries. A job can be prompt-driven, script-assisted, script-only, model/skill-specific, or delivery-targeted. Dry-runs and dashboard previews should describe what will happen before a job mutates state.

Run:

```bash
aegis cron list
aegis cron add "@daily" "summarize repository health"
aegis maturity --check
```
