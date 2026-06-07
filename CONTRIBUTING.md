# Contributing to AEGIS

Thanks for helping! AEGIS aims to be a lean, auditable agent harness — keep changes
small, tested, and in the spirit of the existing code.

## Dev setup

```bash
git clone https://github.com/Alien0013/aegis && cd aegis
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 70+ offline tests, no network/keys needed
```

## Architecture (where things live)

```
aegis/
  agent/       loop, context (3-tier prompt), governance, compaction
  providers/   transports (chat_completions, anthropic) + auth (key/OAuth)
  tools/       base, permissions, registry, builtin + extended tools
  gateway/     runner + channel adapters
  memory.py  skills.py  session.py  learn.py  cron.py  marketplace.py  …
  cli/         main (subcommands) + repl (TUI)
```

## How to add things

- **A tool** — subclass `aegis.tools.base.Tool`, add it to `default_registry()`. Set
  `groups` for permission gating and a `toolset`.
- **A provider** — add a `ProviderSpec` to `aegis/providers/registry.py` (or
  `register_provider()` from a plugin). New wire protocol = new `ProviderTransport`.
- **A channel** — subclass `BasePlatformAdapter`, wire it in `gateway/channels.py`.
- **A memory backend** — implement `MemoryProvider`, add it to `build_memory_provider`.
- **A skill** — a `SKILL.md` package; bundle under `aegis/builtin_skills/` or ship via a hub.

## Guidelines

- Match the surrounding style (concise, typed, `from __future__ import annotations`).
- Add a test for new behavior. Run `pytest -q` — it must stay green.
- Keep optional heavy deps as lazy imports inside functions; declare extras in `pyproject.toml`.
- One logical change per PR; explain the *why*.

## Commit / PR

Conventional-ish subjects (`feat:`, `fix:`, `docs:`, `test:`). Fill in the PR template.
