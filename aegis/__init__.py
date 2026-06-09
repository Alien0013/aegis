"""AEGIS — a self-improving, multi-provider, multi-channel terminal agent harness.

A Python agent harness with a deliberately small, auditable core: one bounded synchronous
agent loop, pluggable providers with API-key *and* OAuth auth, a capability-gated tool
system, persistent memory, a SKILL.md skills engine, and a multi-channel gateway.
"""

__version__ = "0.1.0"
APP_NAME = "aegis"

__all__ = ["__version__", "APP_NAME"]
