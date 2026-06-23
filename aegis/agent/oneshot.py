"""Stateless one-shot LLM helpers for UI/CLI side tasks.

One-shots are small model calls that should not mutate a session transcript:
commit-message suggestions, short summaries, or rename ideas. They use the same
auxiliary routing as other internal helpers, with fallback to the main provider.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Config
from ..types import Message

PromptTemplate = Callable[[dict[str, Any]], tuple[str, str]]


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...(truncated)"


_COMMIT_INSTRUCTIONS = (
    "You write git commit messages. Given a diff of staged changes, write one "
    "concise Conventional Commits message. Use imperative mood, keep the "
    "subject under 72 characters, add a short body only when it adds useful "
    "context, and return only the commit message text."
)


def _commit_message_template(variables: dict[str, Any]) -> tuple[str, str]:
    diff = _truncate(str(variables.get("diff") or ""), 12_000)
    recent = _truncate(str(variables.get("recent_commits") or ""), 1_500)
    avoid = _truncate(str(variables.get("avoid") or ""), 1_000)
    parts: list[str] = []
    if recent.strip():
        parts.append("Recent commit subjects from this repo:\n" + recent)
    parts.append("Diff to describe:\n" + (diff or "(no textual diff available)"))
    if avoid.strip():
        parts.append("Avoid repeating this previous suggestion:\n" + avoid)
    return _COMMIT_INSTRUCTIONS, "\n\n".join(parts)


PROMPT_TEMPLATES: dict[str, PromptTemplate] = {
    "commit_message": _commit_message_template,
}


def render_template(name: str, variables: dict[str, Any] | None = None) -> tuple[str, str]:
    template = PROMPT_TEMPLATES.get(str(name or ""))
    if template is None:
        raise KeyError(f"unknown one-shot template: {name}")
    return template(variables or {})


def _strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def run_oneshot(
    *,
    config: Config | None = None,
    instructions: str = "",
    user_input: str = "",
    template: str | None = None,
    variables: dict[str, Any] | None = None,
    task: str = "title_generation",
    max_tokens: int = 1024,
    temperature: float | None = 0.3,  # reserved for provider compatibility
    timeout: float = 60.0,            # reserved for provider compatibility
    fallback_provider: Any = None,
) -> str:
    """Run a single stateless LLM request and return stripped text."""

    del temperature, timeout
    if template:
        instructions, user_input = render_template(template, variables)
    if not str(instructions or "").strip() and not str(user_input or "").strip():
        raise ValueError("run_oneshot requires a template or instructions/user_input")
    cfg = config or Config.load()
    from ..providers.registry import build_aux_provider

    provider = build_aux_provider(cfg, purpose=task, fallback_provider=fallback_provider)
    messages: list[Message] = []
    if str(instructions or "").strip():
        messages.append(Message.system(str(instructions)))
    messages.append(Message.user(str(user_input or "")))
    response = provider.complete(
        messages,
        tools=None,
        stream=False,
        max_tokens=max(1, int(max_tokens or 1024)),
    )
    return _strip_code_fence(str(getattr(response, "text", "") or "").strip())
