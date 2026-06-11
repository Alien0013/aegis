"""Prompt context references shared by every entry surface.

AEGIS supports lightweight inline references in user prompts:

``@path``, ``@file:path[:10-20]``, ``@folder:path``, ``@diff``,
``@staged``, ``@git:<ref>``, ``@url:https://...``, and
``@mcp:<server>:<resource-uri>``.

The REPL had this behavior first; keeping it here makes SDK/API/gateway/cron
turns behave the same way without each surface growing its own parser.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import Message


_AT_RE = re.compile(r"@([^\s]+)")
_RANGE_RE = re.compile(r"^(.*?):(\d+)-(\d+)$")
_SENSITIVE_REF = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    "id_rsa",
    "id_ed25519",
    ".env",
    "credentials",
    ".netrc",
    "authorized_keys",
    ".git-credentials",
)


@dataclass
class ContextReference:
    raw: str
    kind: str
    target: str = ""
    warning: str = ""
    chars: int = 0


@dataclass
class ReferenceExpansion:
    original: str
    text: str
    references: list[ContextReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    injected_chars: int = 0

    @property
    def expanded(self) -> bool:
        return self.text != self.original


def expand_references(text: str, cwd: str | Path, config: Any = None) -> str:
    """Return ``text`` with supported ``@`` references appended as context."""

    return expand_reference_result(text, cwd, config=config).text


def expand_prompt(prompt: str | Message, cwd: str | Path, config: Any = None) -> str | Message:
    """Expand references while preserving Message images and role metadata."""

    if isinstance(prompt, Message):
        if prompt.role != "user" or not prompt.content:
            return prompt
        expanded = expand_references(prompt.content, cwd, config=config)
        if expanded == prompt.content:
            return prompt
        data = prompt.to_dict()
        data["content"] = expanded
        return Message.from_dict(data)
    return expand_references(str(prompt), cwd, config=config)


def expand_reference_result(text: str, cwd: str | Path, config: Any = None) -> ReferenceExpansion:
    if not text or not _enabled(config):
        return ReferenceExpansion(original=text, text=text)
    cwd_path = Path(cwd).expanduser()
    extras: list[str] = []
    refs: list[ContextReference] = []
    warnings: list[str] = []
    injected = 0
    max_total = _int_config(config, "context_references.max_chars", 50_000)
    max_file = _int_config(config, "context_references.max_file_chars", 20_000)
    max_git = _int_config(config, "context_references.max_git_chars", 20_000)
    max_url = _int_config(config, "context_references.max_url_chars", 20_000)
    max_folder = _int_config(config, "context_references.max_folder_entries", 200)

    for match in _AT_RE.finditer(text):
        raw = match.group(1).rstrip(",.;!?")
        block, ref = _expand_one(
            raw,
            cwd_path,
            config=config,
            max_file=max_file,
            max_git=max_git,
            max_url=max_url,
            max_folder=max_folder,
        )
        if ref:
            refs.append(ref)
            if ref.warning:
                warnings.append(ref.warning)
        if not block:
            continue
        if injected + len(block) > max_total:
            warning = f"reference @{raw} skipped: context attachment limit exceeded"
            refs.append(ContextReference(raw=raw, kind="limit", warning=warning))
            warnings.append(warning)
            continue
        extras.append(block)
        injected += len(block)

    if not extras and not warnings:
        return ReferenceExpansion(original=text, text=text, references=refs)
    expanded = text + "".join(extras)
    if warnings and _bool_config(config, "context_references.include_warnings", True):
        expanded += "\n\n<context-reference-warnings>\n"
        expanded += "\n".join(f"- {w}" for w in warnings[:20])
        expanded += "\n</context-reference-warnings>"
    return ReferenceExpansion(
        original=text,
        text=expanded,
        references=refs,
        warnings=warnings,
        injected_chars=injected,
    )


def _expand_one(
    raw: str,
    cwd: Path,
    *,
    config: Any = None,
    max_file: int,
    max_git: int,
    max_url: int,
    max_folder: int,
) -> tuple[str, ContextReference | None]:
    kind, sep, value = raw.partition(":")
    if raw == "diff":
        body = _git(cwd, "diff")[:max_git]
        return f"\n\n<git-diff>\n{body}\n</git-diff>", ContextReference(raw=raw, kind="diff", chars=len(body))
    if raw == "staged":
        body = _git(cwd, "diff", "--cached")[:max_git]
        return f"\n\n<git-staged>\n{body}\n</git-staged>", ContextReference(raw=raw, kind="staged", chars=len(body))
    if kind == "git" and sep and value:
        body = _git(cwd, "show", "--stat", value)[:max_git]
        return (
            f'\n\n<git-show ref="{_xml_attr(value)}">\n{body}\n</git-show>',
            ContextReference(raw=raw, kind="git", target=value, chars=len(body)),
        )
    if kind == "url" and sep and value.startswith(("http://", "https://")):
        body = _fetch_url(value, max_url)
        return (
            f'\n\n<url-content href="{_xml_attr(value)}">\n{body}\n</url-content>',
            ContextReference(raw=raw, kind="url", target=value, chars=len(body)),
        )
    if kind == "mcp" and sep and value:
        return _expand_mcp_resource(raw, value, max_file, config)

    target = value if kind in ("file", "folder") and sep else raw
    explicit = kind in ("file", "folder") and sep
    if _ref_sensitive(target):
        warning = f"reference @{raw} refused: sensitive path"
        return "", ContextReference(raw=raw, kind=kind if explicit else "path", target=target, warning=warning)

    start = end = None
    rng = _RANGE_RE.match(target)
    if rng:
        target, start, end = rng.group(1), int(rng.group(2)), int(rng.group(3))
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = cwd / path

    if kind == "folder" or path.is_dir():
        if not path.exists() or not path.is_dir():
            warning = f"reference @{raw}: folder not found" if explicit else ""
            return "", ContextReference(raw=raw, kind="folder", target=target, warning=warning)
        try:
            names = sorted(x.name + ("/" if x.is_dir() else "") for x in path.iterdir())[:max_folder]
        except OSError as exc:
            warning = f"reference @{raw}: {exc}"
            return "", ContextReference(raw=raw, kind="folder", target=target, warning=warning)
        body = "\n".join(names)
        return (
            f'\n\n<folder path="{_xml_attr(target)}">\n{body}\n</folder>',
            ContextReference(raw=raw, kind="folder", target=target, chars=len(body)),
        )

    if not path.is_file():
        warning = f"reference @{raw}: file not found" if explicit else ""
        return "", ContextReference(raw=raw, kind="file" if explicit else "path", target=target, warning=warning)
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warning = f"reference @{raw}: {exc}"
        return "", ContextReference(raw=raw, kind="file", target=target, warning=warning)
    if start is not None and end is not None:
        lines = body.splitlines()[start - 1:end]
        body = "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))
    body = body[:max_file]
    return (
        f'\n\n<file path="{_xml_attr(target)}">\n{body}\n</file>',
        ContextReference(raw=raw, kind="file", target=target, chars=len(body)),
    )


def _git(cwd: Path, *argv: str) -> str:
    try:
        out = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True,
                             timeout=15, check=False)
        return (out.stdout or out.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"(git failed: {exc})"


def _fetch_url(url: str, max_chars: int) -> str:
    try:
        from .net_safety import guard

        blocked = guard(url)
        if blocked:
            return blocked
        import httpx

        return httpx.get(url, timeout=15, follow_redirects=True).text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        return f"(fetch failed: {exc})"


def _expand_mcp_resource(
    raw: str,
    value: str,
    max_chars: int,
    config: Any,
) -> tuple[str, ContextReference | None]:
    server, sep, uri = value.partition(":")
    if not sep or not server or not uri:
        warning = f"reference @{raw}: use @mcp:<server>:<resource-uri>"
        return "", ContextReference(raw=raw, kind="mcp", target=value, warning=warning)
    try:
        from .config import Config
        from .mcp.client import build_manager

        manager = build_manager(config or Config.load())
        client = next((c for c in manager.clients if c.name == server), None)
        if client is None:
            warning = f"reference @{raw}: MCP server '{server}' not configured"
            return "", ContextReference(raw=raw, kind="mcp", target=value, warning=warning)
        try:
            client.connect()
            body = client.read_resource(uri)[:max_chars]
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        warning = f"reference @{raw}: MCP resource read failed: {exc}"
        return "", ContextReference(raw=raw, kind="mcp", target=value, warning=warning)
    return (
        f'\n\n<mcp-resource server="{_xml_attr(server)}" uri="{_xml_attr(uri)}">\n'
        f"{body}\n</mcp-resource>",
        ContextReference(raw=raw, kind="mcp", target=f"{server}:{uri}", chars=len(body)),
    )


def _enabled(config: Any) -> bool:
    return _bool_config(config, "context_references.enabled", True)


def _bool_config(config: Any, key: str, default: bool) -> bool:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return bool(getter(key, default))
    except Exception:  # noqa: BLE001
        return default


def _int_config(config: Any, key: str, default: int) -> int:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return max(0, int(getter(key, default) or default))
    except Exception:  # noqa: BLE001
        return default


def _ref_sensitive(raw: str) -> bool:
    low = raw.lower()
    return any(part in low for part in _SENSITIVE_REF)


def _xml_attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
