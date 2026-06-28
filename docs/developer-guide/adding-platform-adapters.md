# Adding Platform Adapters

A platform adapter connects an external messaging or event surface to the shared AEGIS runtime without creating a second agent core.

## Contract

1. Normalize inbound events into a common message shape.
2. Enforce allowlists, pairing, and channel authorization before invoking the agent.
3. Resolve a stable session key per chat, thread, topic, account, or webhook route.
4. Preserve attachment metadata while avoiding credential or PII leaks.
5. Deliver outbound text, media, approval prompts, and errors through the adapter outbox.
6. Record sanitized logs, retries, idempotency keys, and dead-letter state.

## Required tests

- Fake adapter contract for inbound/outbound shape.
- Secret non-echo test for tokens and submitted credentials.
- Session isolation test for two chats or threads.
- Optional live smoke gated by `AEGIS_LIVE_<CHANNEL>=1` and real credentials.

## Live proof

A live proof must include the command, commit SHA, sanitized output, platform type, and failure reason if blocked. Never store bot tokens or account cookies in the repo.
