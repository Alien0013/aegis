# Providers & Auth

27 presets: `codex`, `anthropic`, `openai`, `google`, `openrouter`, `groq`, `deepseek`, `xai`,
`mistral`, `together`, `huggingface`, `novita`, `zai`, `kimi`, `minimax`, `nvidia`,
`dashscope`, `stepfun`, `cerebras`, `perplexity`, `fireworks`, `hyperbolic`, `sambanova`,
`ollama`, `lmstudio`, `vllm` — plus any OpenAI-compatible endpoint via
`model.base_url` / `custom_providers`.

```bash
aegis model list
aegis model set codex gpt-5.5
aegis model doctor
aegis model set openai gpt-4o
aegis auth status
```

`aegis model` and `aegis model doctor` print the resolved provider, transport,
context window, auth readiness, fallback chain, and prompt-routing rules. The
dashboard Models page uses the same provider resolver report, including custom
and plugin provider catalog rows.

## Auth

Use `codex` for ChatGPT/Codex subscription auth. It delegates turns to the local
`codex app-server`, so run `codex login` first. Use `openai` for OpenAI Platform
API-key auth through `OPENAI_API_KEY`.

Per API provider, AEGIS resolves: explicit `base_url` → API key → OAuth login.
API keys win when both are present because some OAuth tokens are identity-only.
OAuth uses PKCE S256 with localhost-callback or manual-paste flows, auto-refresh,
and `auth.json` (0600). **Anthropic, OpenAI API OAuth, Google** ship with OAuth configs:

```bash
aegis auth login anthropic     # paste code
aegis auth login openai        # OpenAI API OAuth (localhost:1455)
aegis auth login google        # Google sign-in
```

OpenAI OAuth login may succeed without the `model.request` scope required for model
inference. `aegis auth status` reports that state; use `OPENAI_API_KEY` for the
OpenAI API path, or use `codex` + `codex login` for ChatGPT subscription-backed
Codex inference.

A comma-separated env value is a **credential pool** that rotates on 429/401:
`OPENAI_API_KEY=sk-1,sk-2,sk-3`.

## Reasoning, routing, fallback

```yaml
agent:
  reasoning_effort: high        # Claude extended-thinking / OpenAI reasoning_effort
routing:
  - {match: "\\bdeploy\\b", provider: anthropic, model: claude-opus-4-6}
fallback_providers:
  - {provider: openrouter, model: anthropic/claude-sonnet-4.5}
```

After a fallback succeeds, AEGIS tries that active provider first on the next
model call. This keeps cache/state warm and avoids repeatedly hitting a known
failing primary during a degraded run. Provider-side response cancellation is
also delegated through the active provider.

AEGIS normalizes tool schemas before sending them to Chat Completions, Responses,
and Codex app-server dynamic tools. This keeps MCP/plugin schemas portable across
providers that reject annotation-only JSON Schema keywords or nullable type
unions.

When `responses.state.enabled` and `responses.state.store` are both true, the
OpenAI Responses transport records the latest provider response id per AEGIS
session and sends `previous_response_id` on later turns. Stored Responses calls
also receive local metadata (`session_id`, `trace_id`, `turn_id`, and `run_id`
when a surface run is active) so provider-side records can be correlated with
dashboard runs and traces. With `responses.state.truncate_previous_input` on,
AEGIS sends only the new local input after the stored response id, falling back
to full local history for older state rows that do not have a recorded message
offset. AEGIS only reuses a stored response id when the active provider and
model still match the stored state, so prompt routing or model switches start a
fresh provider-native chain instead of cross-wiring incompatible state.
During streaming Responses calls, AEGIS captures the active response id when
the provider emits it; terminal/TUI/gateway interrupts then issue a best-effort
provider-side cancel while still stopping locally.

If `responses.compaction.enabled` is true, AEGIS sends Responses
`context_management` as a provider-native compaction entry. The legacy
`compact_threshold` ratio is accepted as a shorthand and converted to a token
threshold; `compact_threshold_tokens` can be set directly when you want an exact
provider threshold.

## Auxiliary Routing

Internal summarization work uses `AuxRouter`, not ad hoc provider calls. Set a
small/cheap auxiliary model for compaction, session summaries, and trajectory
compression:

```yaml
auxiliary:
  provider: openai
  model: gpt-5.4-mini
  compaction:
    provider: openrouter
    model: google/gemini-2.5-flash
  session_summary:
    model: gpt-5.4-mini
```

Purpose overrides are optional. Supported keys are `provider`, `model`, and
`context_length` under `auxiliary.compaction`, `auxiliary.session_summary`, and
`auxiliary.trajectory_compression`. If no auxiliary route is configured, AEGIS
uses the live main provider selected for that turn; if an auxiliary route fails
to build, it falls back to that live provider.
