# Providers & Auth

26 presets: `anthropic`, `openai`, `google`, `openrouter`, `groq`, `deepseek`, `xai`,
`mistral`, `together`, `huggingface`, `novita`, `zai`, `kimi`, `minimax`, `nvidia`,
`dashscope`, `stepfun`, `cerebras`, `perplexity`, `fireworks`, `hyperbolic`, `sambanova`,
`nous`, `ollama`, `lmstudio`, `vllm` — plus any OpenAI-compatible endpoint via
`model.base_url` / `custom_providers`.

```bash
aegis model list
aegis model set openai gpt-4o
aegis auth status
```

## Auth

Per provider, AEGIS resolves: explicit `base_url` → API key → OAuth login. API keys
win when both are present because some OAuth tokens are identity-only. OAuth uses PKCE
S256 with localhost-callback or manual-paste flows, auto-refresh, and `auth.json`
(0600). **Anthropic, OpenAI, Google** ship with OAuth configs:

```bash
aegis auth login anthropic     # paste code
aegis auth login openai        # ChatGPT login (localhost:1455)
aegis auth login google        # Google sign-in
```

OpenAI OAuth login may succeed without the `model.request` scope required for model
inference. `aegis auth status` reports that state; use `OPENAI_API_KEY` for the
reliable OpenAI path.

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
