"""Prompt context references shared by every entry surface.

AEGIS supports lightweight inline references in user prompts:

``@path``, ``@file:path[:10-20]``, ``@file:"path with spaces":10``,
``@folder:path``, ``@diff``, ``@staged``, ``@git:<ref>``, ``@url:https://...``, and
``@mcp:<server>:<resource-uri>``.

The REPL had this behavior first; keeping it here makes SDK/API/gateway/cron
turns behave the same way without each surface growing its own parser.
"""

from __future__ import annotations

import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import Message


_QUOTED_REFERENCE_VALUE = r'(?:`[^`\n]+`|"[^"\n]+"|\'[^\'\n]+\')'
_AT_RE = re.compile(
    rf"(?<![\w/])@(?:(?P<simple>diff|staged)\b|"
    rf"(?P<kind>file|folder|git|url|mcp):"
    rf"(?P<value>{_QUOTED_REFERENCE_VALUE}(?::\d+(?:-\d+)?)?|\S+)|"
    rf"(?P<bare>{_QUOTED_REFERENCE_VALUE}|\S+))"
)
_RANGE_RE = re.compile(r"^(.*?):(\d+)(?:-(\d+))?$")
_TRAILING_PUNCTUATION = ",.;!?"
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
_TEXT_SUFFIXES = {
    ".css", ".csv", ".go", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".log", ".md", ".py", ".rs", ".sh", ".toml", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
}


@dataclass(frozen=True)
class _ReferenceToken:
    raw: str
    start: int
    end: int


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

    consumed: list[_ReferenceToken] = []
    for token in _iter_reference_tokens(text):
        raw = token.raw
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
            if block or ref.warning:
                consumed.append(token)
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
    base_text = (
        _remove_reference_tokens(text, consumed)
        if consumed and _bool_config(config, "context_references.remove_tokens", True)
        else text
    )
    expanded = base_text + "".join(extras)
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


def _iter_reference_tokens(text: str) -> list[_ReferenceToken]:
    tokens: list[_ReferenceToken] = []
    for match in _AT_RE.finditer(text):
        simple = match.group("simple")
        if simple:
            tokens.append(_ReferenceToken(raw=simple, start=match.start(), end=match.end()))
            continue
        kind = match.group("kind")
        if kind:
            value = _strip_trailing_punctuation(match.group("value") or "")
            tokens.append(_ReferenceToken(raw=f"{kind}:{value}", start=match.start(), end=match.end()))
            continue
        bare = _strip_trailing_punctuation(match.group("bare") or "")
        if bare:
            tokens.append(_ReferenceToken(raw=bare, start=match.start(), end=match.end()))
    return tokens


def _strip_trailing_punctuation(value: str) -> str:
    if len(value) >= 2 and value[0] in "`\"'" and value[0] == value[-1]:
        return value
    stripped = value.rstrip(_TRAILING_PUNCTUATION)
    while stripped.endswith((")", "]", "}")):
        closer = stripped[-1]
        opener = {")": "(", "]": "[", "}": "{"}[closer]
        if stripped.count(closer) > stripped.count(opener):
            stripped = stripped[:-1]
            continue
        break
    return stripped


def _strip_reference_wrappers(value: str) -> str:
    if len(value) >= 2 and value[0] in "`\"'" and value[0] == value[-1]:
        return value[1:-1]
    return value


def _parse_target_range(target: str) -> tuple[str, int | None, int | None]:
    quoted = re.match(
        r'^(?P<quote>`|"|\')(?P<path>.+?)(?P=quote)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$',
        target,
    )
    if quoted:
        start = quoted.group("start")
        end = quoted.group("end")
        return (
            quoted.group("path"),
            int(start) if start is not None else None,
            int(end or start) if start is not None else None,
        )
    rng = _RANGE_RE.match(target)
    if rng:
        start = int(rng.group(2))
        return _strip_reference_wrappers(rng.group(1)), start, int(rng.group(3) or start)
    return _strip_reference_wrappers(target), None, None


def _remove_reference_tokens(text: str, tokens: list[_ReferenceToken]) -> str:
    pieces: list[str] = []
    cursor = 0
    for token in tokens:
        pieces.append(text[cursor:token.start])
        cursor = token.end
    pieces.append(text[cursor:])
    stripped = "".join(pieces)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    stripped = re.sub(r"\s+([,.;:!?])", r"\1", stripped)
    return stripped.strip()


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
        body, warning = _truncate_reference_body(
            _git(cwd, "diff"),
            raw,
            label="git diff",
            max_chars=max_git,
            config_key="context_references.max_git_chars",
        )
        return (
            f"\n\n<git-diff>\n{body}\n</git-diff>",
            ContextReference(raw=raw, kind="diff", chars=len(body), warning=warning),
        )
    if raw == "staged":
        body, warning = _truncate_reference_body(
            _git(cwd, "diff", "--cached"),
            raw,
            label="staged diff",
            max_chars=max_git,
            config_key="context_references.max_git_chars",
        )
        return (
            f"\n\n<git-staged>\n{body}\n</git-staged>",
            ContextReference(raw=raw, kind="staged", chars=len(body), warning=warning),
        )
    if kind == "git" and sep and value:
        value = _strip_reference_wrappers(value)
        body, warning = _truncate_reference_body(
            _git(cwd, "show", "--stat", value),
            raw,
            label="git show",
            max_chars=max_git,
            config_key="context_references.max_git_chars",
        )
        return (
            f'\n\n<git-show ref="{_xml_attr(value)}">\n{body}\n</git-show>',
            ContextReference(raw=raw, kind="git", target=value, chars=len(body), warning=warning),
        )
    if kind == "url" and sep and _strip_reference_wrappers(value).startswith(("http://", "https://")):
        value = _strip_reference_wrappers(value)
        body, warning = _truncate_reference_body(
            _fetch_url(value, config),
            raw,
            label="URL content",
            max_chars=max_url,
            config_key="context_references.max_url_chars",
        )
        return (
            f'\n\n<url-content href="{_xml_attr(value)}">\n{body}\n</url-content>',
            ContextReference(raw=raw, kind="url", target=value, chars=len(body), warning=warning),
        )
    if kind == "mcp" and sep and value:
        return _expand_mcp_resource(raw, value, max_file, config)

    target = value if kind in ("file", "folder") and sep else raw
    explicit = kind in ("file", "folder") and sep
    target, start, end = _parse_target_range(target)
    if _ref_sensitive(target):
        warning = f"reference @{raw} refused: sensitive path"
        return "", ContextReference(raw=raw, kind=kind if explicit else "path", target=target, warning=warning)

    path, warning = _resolve_reference_path(target, cwd, raw, config)
    if warning:
        return "", ContextReference(raw=raw, kind=kind if explicit else "path", target=target, warning=warning)

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
    if _is_binary_file(path):
        body = _binary_reference_block(path)
        return (
            f'\n\n<file path="{_xml_attr(target)}" binary="true">\n{body}\n</file>',
            ContextReference(raw=raw, kind="file", target=target, chars=len(body)),
        )
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warning = f"reference @{raw}: {exc}"
        return "", ContextReference(raw=raw, kind="file", target=target, warning=warning)
    if start is not None and end is not None:
        lines = body.splitlines()[start - 1:end]
        body = "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))
    body, warning = _truncate_reference_body(
        body,
        raw,
        label="file content",
        max_chars=max_file,
        config_key="context_references.max_file_chars",
    )
    return (
        f'\n\n<file path="{_xml_attr(target)}">\n{body}\n</file>',
        ContextReference(raw=raw, kind="file", target=target, chars=len(body), warning=warning),
    )


def _truncate_reference_body(
    body: str,
    raw: str,
    *,
    label: str,
    max_chars: int,
    config_key: str,
) -> tuple[str, str]:
    if len(body) <= max_chars:
        return body, ""
    warning = (
        f"reference @{raw}: {label} truncated: {len(body)} chars exceeds "
        f"limit of {max_chars}; increase {config_key} or narrow the reference"
    )
    return body[:max_chars], warning


def _git(cwd: Path, *argv: str) -> str:
    try:
        out = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True,
                             timeout=15, check=False)
        return (out.stdout or out.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"(git failed: {exc})"


def _resolve_reference_path(
    target: str,
    cwd: Path,
    raw: str,
    config: Any,
) -> tuple[Path, str]:
    try:
        path = Path(os.path.expanduser(target))
        if not path.is_absolute():
            path = cwd / path
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        return Path(target), f"reference @{raw}: {exc}"

    if not _bool_config(config, "context_references.allow_outside_cwd", False):
        try:
            resolved.relative_to(cwd.expanduser().resolve())
        except ValueError:
            return resolved, f"reference @{raw} refused: path outside workspace"
    return resolved, ""


def _is_binary_file(path: Path) -> bool:
    mime, _ = mimetypes.guess_type(path.name)
    if mime and not mime.startswith("text/") and path.suffix.lower() not in _TEXT_SUFFIXES:
        return True
    try:
        return b"\x00" in path.read_bytes()[:4096]
    except OSError:
        return False


def _binary_reference_block(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return (
        f"Binary file not inlined as text ({mime}, {size:,} bytes). "
        f"It is available on disk at {path}. Use tools to inspect, convert, or render it."
    )


def _fetch_url(url: str, config: Any = None) -> str:
    from . import net_safety

    try:
        response = net_safety.request("GET", url, config, timeout=15)
        response.raise_for_status()
        return response.text
    except net_safety.BlockedURL as exc:
        return str(exc)
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
            body, warning = _truncate_reference_body(
                client.read_resource(uri),
                raw,
                label="MCP resource",
                max_chars=max_chars,
                config_key="context_references.max_file_chars",
            )
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        warning = f"reference @{raw}: MCP resource read failed: {exc}"
        return "", ContextReference(raw=raw, kind="mcp", target=value, warning=warning)
    return (
        f'\n\n<mcp-resource server="{_xml_attr(server)}" uri="{_xml_attr(uri)}">\n'
        f"{body}\n</mcp-resource>",
        ContextReference(raw=raw, kind="mcp", target=f"{server}:{uri}", chars=len(body), warning=warning),
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
    low = _strip_reference_wrappers(raw).lower()
    return any(part in low for part in _SENSITIVE_REF)


def _xml_attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
