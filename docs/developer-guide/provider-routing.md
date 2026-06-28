# Provider Routing

Provider routing is a product contract, not a string switch. AEGIS routes model calls through a provider registry with capability metadata, authentication readiness, credential pools, fallback behavior, and auxiliary task routing.

## Contract

- Provider capability matrix describes chat, tools, vision, streaming, and auth shape.
- Auth status is redacted and never echoes keys.
- Fallback routing explains why a provider was skipped or exhausted.
- Auxiliary models for compression, vision, or summaries can be configured separately.
- Live provider smoke tests are opt-in and credential-gated.
