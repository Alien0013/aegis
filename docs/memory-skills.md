# Memory, Skills & the Learning Loop

## Memory

File-backed `MEMORY.md` / `USER.md` (`§`-delimited, char-capped) + `history.jsonl`. The
agent persists facts via the `memory` tool. Pluggable external backends:

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
aegis skills hub hermeshub       # import a whole hub (also: openclaw, anthropic)
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

## Recall

SQLite **FTS5** full-text search across sessions, plus summaries:

```bash
aegis sessions search "kubernetes"
aegis sessions summarize <id>
```

The `session_search` tool gives the agent cross-session recall in-conversation.
