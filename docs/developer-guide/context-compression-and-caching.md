# Context Compression and Caching

AEGIS keeps the model-facing core narrow and cache-friendly. Stable runtime instructions should not be rewritten mid-conversation when an optional feature can live in a tool, plugin, dashboard route, or skill.

## Layers

- Stable: product rules, safety boundaries, tool instructions, and system identity.
- Context: project rules, loaded skills, memory blocks, platform hints, and references.
- Volatile: the active user turn, recent messages, tool results, and queued steering.

## Contract

Context files and external references are bounded, scanned, and attributed. Compression must preserve task state, last meaningful user instruction, open todos, recent tool outcomes, and recovery breadcrumbs.
