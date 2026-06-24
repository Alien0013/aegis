---
name: automation
description: Set up a watcher — poll an RSS/Atom feed, a JSON API, or a GitHub repo on a schedule and react only to what's new (watermark dedup). Use when the user says "watch X", "notify me when X changes", "keep an eye on X", or wants a recurring poll-and-notify automation.
version: 1.0.0
metadata:
  category: automation
  tags: [automation, watcher, cron, polling, rss, github, http, monitoring, notify]
---

## When to Use
The user wants something watched on an interval and to hear about it **only when it changes** — a release feed, a repo's issues/PRs, a status JSON endpoint, a blog's RSS. Not for a one-off "check this now" (just fetch it), and not for sub-second/event-stream needs (use a webhook channel instead).

## Mental model
A watcher is a small script that, each time it runs:
1. **Fetches** the external source,
2. **Compares** against a watermark of previously-seen IDs,
3. **Emits** only the new items to stdout (nothing on no-change),
4. **Persists** the updated watermark.

You then put that script on a schedule with the `cronjob` tool. The first run records a baseline and emits nothing — so adding a watcher never floods the user with a feed's whole backlog.

## Ready-made scripts
They live in this skill's `scripts/` directory and use only the Python standard library. Each keeps state under `$AEGIS_WATCHER_STATE_DIR` (default `$AEGIS_HOME/watcher-state/`), keyed by `--name`.

| Script | Watches | Dedup key |
|---|---|---|
| `watch_rss.py` | RSS 2.0 / Atom feed (`--url`) | `<guid>`/`<id>`/`<link>` |
| `watch_http_json.py` | any JSON endpoint returning a list (`--url`, `--list-path`, `--id-field`) | the id field |
| `watch_github.py` | GitHub `issues`/`pulls`/`releases`/`commits` (`--repo owner/name --type ...`) | issue/PR/release id or commit sha |

All three: baseline on first run, watermark capped at 500 ids, empty stdout on no-change, non-zero exit on fetch error. `watch_github.py` reads `GITHUB_TOKEN` if set.

## Procedure
1. **Pick the source and identify the script.** RSS → `watch_rss.py`; arbitrary JSON → `watch_http_json.py` (find the array path and a stable id field by fetching it once with `web_fetch`/`http_request`); GitHub → `watch_github.py`.
2. **Dry-run it once** with the `bash` tool to confirm it parses and to lay down the baseline:
   `python <skill>/scripts/watch_rss.py --name my-feed --url https://example.com/feed.xml`
   (First run prints nothing — that's correct. Run again to confirm no error.)
3. **Schedule it.** Choose the delivery shape:
   - **Notify-only** (just forward new items to the user) — run the script with no agent turn:
     `cronjob action=create name="watch my-feed" schedule="30m" no_agent=true deliver=origin script="<skill>/scripts/watch_rss.py --name my-feed --url https://example.com/feed.xml"`
   - **Act on new items** (the agent should do something — triage, summarize, file an issue) — let the script's output become the turn's context:
     `cronjob action=create name="triage repo issues" schedule="1h" deliver=origin script="<skill>/scripts/watch_github.py --name acme-issues --repo acme/widget --type issues" prompt="For each new issue above, label its severity and draft a one-line triage note."`
4. **Confirm the schedule is live.** `cronjob action=list`, and ensure the runner is active: `cronjob action=service service_action=status` (install with `aegis cron install` or run the `aegis gateway` if not).
5. **Tell the user** exactly what's watched, how often, where it delivers, and how to stop it (`cronjob action=delete job_id=...`).

## Pitfalls
- **Don't replay backlog.** The baseline-on-first-run is deliberate; never "seed" by clearing state unless the user wants the current backlog once.
- **Pick a stable id field** for JSON sources — a changing field (timestamp, position) makes everything look new every run.
- **Mind rate limits.** Set a sane interval (releases hourly/daily, not every minute); for GitHub set `GITHUB_TOKEN`.
- **`no_agent=true` for pure notify** — don't spend a model turn just to forward new items; reserve the agent for when it must *act*.
- The runner must be active (`aegis cron install` service or a running gateway) or jobs won't fire.

## Verification
You're done when: the script dry-runs cleanly and laid a baseline; the cron job appears in `cronjob action=list` with the right schedule and delivery; the runner service is active; and you've told the user what's watched, the cadence, the delivery target, and the stop command.
