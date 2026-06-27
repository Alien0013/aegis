"""Compatibility setup surface for AEGIS mem0 memory provider."""

from __future__ import annotations

import json

from aegis.memory_providers import memory_provider_setup


def setup_payload() -> dict:
    """Return the native AEGIS setup payload for the mem0 provider."""

    return memory_provider_setup("mem0")


def main() -> int:
    print(json.dumps(setup_payload(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
