
"""Credential-gated provider live-smoke preflight tests."""

from __future__ import annotations

import os

import pytest


REQUIRED_ENV_GROUPS = {
    "openai": (("OPENAI_API_KEY",),),
    "anthropic": (("ANTHROPIC_API_KEY",),),
    "google": (("GEMINI_API_KEY", "GOOGLE_API_KEY"),),
    "openrouter": (("OPENROUTER_API_KEY",),),
    "groq": (("GROQ_API_KEY",),),
    "deepseek": (("DEEPSEEK_API_KEY",),),
    "xai": (("XAI_API_KEY",),),
    "mistral": (("MISTRAL_API_KEY",),),
    "together": (("TOGETHER_API_KEY",),),
    "huggingface": (("HF_TOKEN", "HUGGINGFACE_API_KEY"),),
    "novita": (("NOVITA_API_KEY",),),
    "qwen": (("QWEN_API_KEY", "DASHSCOPE_API_KEY"),),
    "nvidia": (("NVIDIA_API_KEY",),),
    "dashscope": (("DASHSCOPE_API_KEY",),),
    "cerebras": (("CEREBRAS_API_KEY",),),
    "perplexity": (("PERPLEXITY_API_KEY",),),
    "fireworks": (("FIREWORKS_API_KEY",),),
    "sambanova": (("SAMBANOVA_API_KEY",),),
}


def _missing(groups: tuple[tuple[str, ...], ...]) -> list[str]:
    missing = []
    for alternatives in groups:
        if not any(os.getenv(name) for name in alternatives):
            missing.append(" or ".join(alternatives))
    return missing


def test_live_provider_preflight_env_contract():
    provider = os.getenv("AEGIS_LIVE_PROVIDER", "").strip()
    if not provider:
        pytest.skip("set AEGIS_LIVE_PROVIDER through a matrix live_proof_command to run a provider live preflight")
    assert provider in REQUIRED_ENV_GROUPS
    opt_in = f"AEGIS_LIVE_{provider.upper()}"
    assert os.getenv(opt_in) == "1", f"{opt_in}=1 is required for live provider preflight"
    missing = _missing(REQUIRED_ENV_GROUPS[provider])
    assert not missing, f"missing required environment for {provider}: {', '.join(missing)}"
