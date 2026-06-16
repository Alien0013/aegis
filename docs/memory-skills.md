# Memory, Skills & the Learning Loop

## Memory

Two file-backed stores in `~/.aegis/memories/` (`§`-delimited, char-capped) plus
`history.jsonl`:

- **`MEMORY.md`** — the agent's own notes (environment facts, project conventions, quirks).
- **`USER.md`** — the single user profile (name, preferences, workflow). This is the
  one and only profile file; the agent manages it via the `memory` tool — just tell it
  something and it remembers. (A `workspace/USER.md` from an older install is migrated
  into it automatically, once, and parked as `USER.md.migrated`.)

Both enter the system prompt as a **frozen snapshot** for prefix-cache stability; the
snapshot is rebuilt automatically on the next turn whenever the files change, so a fact
you give the agent is in context from your next message on — even within one long chat.

Pluggable external backends layer on top of the built-in files:

```yaml
memory:
  provider: honcho     # "" | honcho | mem0 | jsonl | http (openviking/supermemory/…)
```

## Skills

`SKILL.md` packages with progressive disclosure (only descriptions cost tokens until a
skill is loaded) and tiered precedence (workspace > personal > configured > bundled).
24 ship by default.

```bash
aegis skills                     # available skills
aegis skills hub anthropic       # import a whole hub
aegis skills install git:owner/repo
aegis skills new my-skill
```

Installs are security-scanned (trust gating; `--force` overrides).

## The closed learning loop

AEGIS learns from experience:

```bash
aegis learn review        # LLM reviews a session → memory + skill candidates (redacted)
aegis learn list
aegis learn apply <id>    # promote to MEMORY.md / a versioned SKILL.md
```

Set `learn.auto: true` to review automatically on exit. The agent also tracks skill
**usage**, can `skill improve` (append learned notes), and `aegis curator` audits/prunes.

Gateway chats can tune background memory-review notifications:

```yaml
display:
  memory_notifications: on   # off | on | verbose
```

`off` hides chat notices while the review still runs, `on` reports a generic
memory/user-profile update, and `verbose` includes compact add/replace/remove
previews.

## Recall

SQLite **FTS5** full-text search across sessions, plus summaries:

```bash
aegis sessions search "kubernetes"
aegis sessions summarize <id>
```

The `session_search` tool gives the agent cross-session recall in-conversation.
