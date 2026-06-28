# AEGIS Live QA Matrix

This matrix separates local fake/mock proof from credentialed smoke and cross-OS proof. It does not claim live platform readiness just because local tests pass.

Run the local maturity check:

```bash
aegis maturity --check
aegis maturity --json --check
```

The dashboard exposes the same target list at `/api/live-qa`.

## Policy

- A local adapter test proves protocol shape, redaction, attachments, delivery metadata, and fail-soft behavior.
- A credentialed smoke proves that a real external account, bot, webhook, provider, or OS runner worked.
- A manual OS runner proves installer behavior on Linux, Windows, macOS, Docker, or another environment not represented by the local host.
- AEGIS must not mark a live target ready unless a real credentialed smoke or OS runner result is recorded.

## Credentialed smoke targets

| Target family | Examples | Local proof | Live proof |
| --- | --- | --- | --- |
| Messaging gateway | API server, webhook, Telegram, Discord, Slack, Matrix, Signal, email, SMS, WhatsApp, WhatsApp Cloud, ntfy, Mattermost, Home Assistant, Feishu, WeCom, Weixin, DingTalk, BlueBubbles, QQBot, Yuanbao, Relay, Microsoft Graph webhook | `tests/test_gateway_adapter_contract.py` plus `tests/live/test_gateway_smoke.py` preflight | `AEGIS_LIVE_TARGET=<id> AEGIS_LIVE_<ID>=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py` with real platform credentials. |
| Providers | OpenAI, Anthropic, Google/Gemini, OpenRouter, Groq, DeepSeek, xAI, Mistral, Together, HuggingFace, Novita, Qwen, NVIDIA, DashScope, Cerebras, Perplexity, Fireworks, SambaNova | `tests/test_providers.py`, `docs/providers.md`, and `tests/live/test_provider_smoke.py` preflight | `AEGIS_LIVE_PROVIDER=<id> AEGIS_LIVE_<ID>=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py` with a real API key or OAuth account. |
| Desktop installers | Linux, Windows, macOS | `desktop` node tests and release smoke | signed/notarized or local OS runner install/open/update/uninstall proof. |
| Container install | Docker or CI image | `scripts/verify_all.sh` | clean-container install from a fresh checkout. |

## Recording evidence

A live result should record:

1. target id,
2. date/time,
3. command run,
4. sanitized status/output,
5. commit SHA,
6. account/platform type without credential values,
7. failure reason when blocked.

Credential values, tokens, passwords, and session cookies must never be copied into docs, logs, or generated reports.

## Why this exists

The product has broad local coverage, but local fake adapters cannot honestly prove real external delivery, bot permissions, provider billing state, OS signing, or network policy. The matrix makes that distinction first-class so local parity remains honest while live proof can be added over time.

## Quick operator checklist

```bash
# local readiness
aegis maturity --check
bash scripts/run_tests.sh tests/test_gateway_adapter_contract.py tests/test_providers.py

# example live style, only when credentials are configured
AEGIS_LIVE_TARGET=telegram AEGIS_LIVE_TELEGRAM=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py
AEGIS_LIVE_PROVIDER=openai AEGIS_LIVE_OPENAI=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py
```
