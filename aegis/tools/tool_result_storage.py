"""Tool result persistence helpers.

Large tool outputs should be preserved outside the model context instead of
being dropped or inline-truncated. Prefer writing through the active execution
environment so remote/container backends can read the file with normal file
tools; fall back to AEGIS' local tool-output directory when no environment is
available.
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSE = "</persisted-output>"
SPILL_MARKER = "truncated to protect context"
DEFAULT_STORAGE_DIR = "/tmp/aegis-results"
DEFAULT_PREVIEW_CHARS = 1500
METADATA_SUFFIX = ".metadata.json"
METADATA_SCHEMA_VERSION = 1


def generate_preview(content: str, max_chars: int = DEFAULT_PREVIEW_CHARS) -> tuple[str, bool]:
    """Return a preview and whether content was omitted."""
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max(1, max_chars)]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[: last_nl + 1]
    return truncated.rstrip(), True


def _content_bytes(content: str) -> bytes:
    return content.encode("utf-8", errors="surrogatepass")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(_content_bytes(content)).hexdigest()


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_name(value: str, default: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("_")
    return (clean or default)[:80]


def _metadata_path_for(file_path: str) -> str:
    return f"{file_path}{METADATA_SUFFIX}"


def _build_metadata(
    *,
    content: str,
    tool_name: str,
    tool_use_id: str,
    file_path: str,
    metadata_path: str,
    storage: str,
    reason: str,
    preview_chars: int,
    has_more: bool,
) -> dict[str, Any]:
    raw = _content_bytes(content)
    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "kind": "aegis.tool_result",
        "created_at": _utc_timestamp(),
        "tool_name": str(tool_name or ""),
        "tool_use_id": str(tool_use_id or ""),
        "storage": storage,
        "path": file_path,
        "metadata_path": metadata_path,
        "chars": len(content),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "preview_chars": int(preview_chars),
        "has_more": bool(has_more),
        "reason": str(reason or ""),
        "content_type": "text/plain; charset=utf-8",
    }


def _metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _resolve_env_storage_dir(env: Any) -> str:
    get_temp_dir = getattr(env, "get_temp_dir", None)
    if callable(get_temp_dir):
        try:
            temp_dir = str(get_temp_dir() or "").rstrip("/")
            if temp_dir:
                return f"{temp_dir or '/'}/aegis-results"
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not resolve environment temp dir: %s", exc)
    return DEFAULT_STORAGE_DIR


def _write_to_environment(content: str, remote_path: str, env: Any) -> bool:
    storage_dir = os.path.dirname(remote_path)
    command = f"mkdir -p {shlex.quote(storage_dir)} && cat > {shlex.quote(remote_path)}"
    execute = getattr(env, "execute", None)
    if not callable(execute):
        return False
    result = execute(command, timeout=30, stdin_data=content)
    try:
        return int(result.get("returncode", 1) or 0) == 0
    except Exception:  # noqa: BLE001
        return False


def _local_storage_dir(local_dir: str | os.PathLike[str] | None = None) -> str:
    if local_dir:
        return str(local_dir)
    from .. import config as cfg

    return cfg.sub("tool_outputs")


def _atomic_write_local(path: Path, content: str) -> None:
    mode = None
    try:
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = None

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogatepass", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _local_output_path(
    tool_name: str,
    tool_use_id: str,
    *,
    local_dir: str | os.PathLike[str] | None = None,
) -> Path:
    directory = _local_storage_dir(local_dir)
    return Path(
        directory,
        f"{_safe_name(tool_name, 'tool')}_{_safe_name(tool_use_id, 'call')}.txt",
    )


def _cleanup_old_local_outputs(directory: str, keep: set[str]) -> None:
    cutoff = time.time() - 7 * 86400
    for old in os.listdir(directory):
        path = os.path.join(directory, old)
        try:
            if os.path.realpath(path) in keep:
                continue
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            continue


def _write_local(content: str, tool_name: str, tool_use_id: str, *,
                 local_dir: str | os.PathLike[str] | None = None) -> str:
    directory = _local_storage_dir(local_dir)
    os.makedirs(directory, exist_ok=True)
    output_path = _local_output_path(tool_name, tool_use_id, local_dir=local_dir)
    _cleanup_old_local_outputs(directory, {os.path.realpath(output_path)})
    _atomic_write_local(output_path, content)
    return str(output_path)


def _write_local_bundle(
    content: str,
    metadata: dict[str, Any],
    *,
    local_dir: str | os.PathLike[str] | None = None,
) -> str:
    file_path = str(metadata["path"])
    expected_dir = _local_storage_dir(local_dir)
    if Path(os.path.abspath(file_path)).parent != Path(os.path.abspath(str(expected_dir))):
        raise ValueError("metadata path does not match local storage directory")
    os.makedirs(expected_dir, exist_ok=True)
    keep = {os.path.realpath(file_path), os.path.realpath(str(metadata["metadata_path"]))}
    _cleanup_old_local_outputs(expected_dir, keep)
    _atomic_write_local(Path(file_path), content)
    metadata_path = str(metadata["metadata_path"])
    _atomic_write_local(Path(metadata_path), _metadata_json(metadata))
    return file_path


def _size_label(chars: int) -> str:
    kib = chars / 1024
    if kib >= 1024:
        return f"{kib / 1024:.1f} MB"
    return f"{kib:.1f} KB"


def _build_message(
    *,
    preview: str,
    has_more: bool,
    original_size: int,
    byte_size: int,
    sha256: str,
    file_path: str | None,
    metadata_path: str | None,
    reason: str,
    failed: bool = False,
) -> str:
    if failed:
        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"This tool result was {SPILL_MARKER}, but the full output could not "
            f"be saved to disk ({original_size:,} chars).\n\n"
            f"Preview (first {len(preview)} chars):\n{preview}\n"
            f"{PERSISTED_OUTPUT_CLOSE}"
        )
    more = "\n..." if has_more else ""
    return (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"This tool result was {SPILL_MARKER}: {reason} "
        f"({original_size:,} chars, {_size_label(original_size)}).\n"
        f"Full output saved to: {file_path}\n"
        f"Metadata saved to: {metadata_path}\n"
        f"Content size: {byte_size:,} bytes / {original_size:,} chars\n"
        f"Content SHA-256: {sha256}\n"
        "Use read_file with offset and limit to inspect specific sections.\n\n"
        f"Preview (first {len(preview)} chars):\n{preview}{more}\n"
        f"{PERSISTED_OUTPUT_CLOSE}"
    )


def maybe_persist_tool_result(
    content: str,
    tool_name: str,
    tool_use_id: str,
    *,
    env: Any = None,
    threshold_chars: int | float | None = None,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    reason: str = "single-output limit exceeded",
    local_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Persist oversized tool output and return a bounded preview message."""
    text = str(content or "")
    threshold = threshold_chars if threshold_chars is not None else 100_000
    if threshold == float("inf") or len(text) <= int(threshold):
        return text

    preview, has_more = generate_preview(text, max_chars=preview_chars)
    byte_size = len(_content_bytes(text))
    sha256 = _sha256_text(text)
    safe_id = _safe_name(tool_use_id, "call")

    if env is not None:
        remote_path = f"{_resolve_env_storage_dir(env)}/{safe_id}.txt"
        remote_metadata_path = _metadata_path_for(remote_path)
        metadata = _build_metadata(
            content=text,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            file_path=remote_path,
            metadata_path=remote_metadata_path,
            storage="environment",
            reason=reason,
            preview_chars=preview_chars,
            has_more=has_more,
        )
        try:
            if (
                _write_to_environment(text, remote_path, env)
                and _write_to_environment(_metadata_json(metadata), remote_metadata_path, env)
            ):
                return _build_message(
                    preview=preview,
                    has_more=has_more,
                    original_size=len(text),
                    byte_size=byte_size,
                    sha256=sha256,
                    file_path=remote_path,
                    metadata_path=remote_metadata_path,
                    reason=reason,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("environment tool-result write failed for %s: %s", safe_id, exc)

    try:
        output_path = _local_output_path(tool_name, tool_use_id, local_dir=local_dir)
        metadata_path = _metadata_path_for(str(output_path))
        metadata = _build_metadata(
            content=text,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            file_path=str(output_path),
            metadata_path=metadata_path,
            storage="local",
            reason=reason,
            preview_chars=preview_chars,
            has_more=has_more,
        )
        path = _write_local_bundle(text, metadata, local_dir=local_dir)
        return _build_message(
            preview=preview,
            has_more=has_more,
            original_size=len(text),
            byte_size=byte_size,
            sha256=sha256,
            file_path=path,
            metadata_path=metadata_path,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("local tool-result write failed for %s: %s", safe_id, exc)
        return _build_message(
            preview=preview,
            has_more=has_more,
            original_size=len(text),
            byte_size=byte_size,
            sha256=sha256,
            file_path=None,
            metadata_path=None,
            reason=reason,
            failed=True,
        )


def _extract_field(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _parse_count(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def parse_persisted_output_reference(message: str) -> dict[str, Any] | None:
    """Parse an AEGIS persisted-output preview block into a recoverable reference."""
    text = str(message or "")
    if PERSISTED_OUTPUT_TAG not in text:
        return None
    file_path = _extract_field(r"^Full output saved to:\s*(.+)$", text)
    if not file_path:
        return None
    byte_count = None
    char_count = None
    size_match = re.search(
        r"^Content size:\s*([\d,]+)\s+bytes\s*/\s*([\d,]+)\s+chars\s*$",
        text,
        flags=re.MULTILINE,
    )
    if size_match is not None:
        byte_count = _parse_count(size_match.group(1))
        char_count = _parse_count(size_match.group(2))
    return {
        "file_path": file_path,
        "metadata_path": _extract_field(r"^Metadata saved to:\s*(.+)$", text),
        "sha256": _extract_field(r"^Content SHA-256:\s*([0-9a-fA-F]{64})\s*$", text),
        "bytes": byte_count,
        "chars": char_count,
    }


def _read_from_environment(path: str, env: Any) -> str:
    execute = getattr(env, "execute", None)
    if not callable(execute):
        raise FileNotFoundError(f"environment cannot read persisted tool result: {path}")
    result = execute(f"cat {shlex.quote(path)}", timeout=30)
    try:
        returncode = int(result.get("returncode", 1) or 0)
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(f"environment read returned an invalid status for {path}") from exc
    if returncode != 0:
        output = str(result.get("output") or "").strip()
        detail = f": {output}" if output else ""
        raise FileNotFoundError(f"could not read persisted tool result {path}{detail}")
    return str(result.get("output") or "")


def _read_reference_text(path: str, *, env: Any = None) -> str:
    if env is not None:
        return _read_from_environment(path, env)
    return Path(path).read_text(encoding="utf-8", errors="surrogatepass")


def _load_metadata(path: str | None, *, env: Any = None) -> dict[str, Any]:
    if not path:
        return {}
    raw = _read_reference_text(path, env=env)
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _expected_from_reference(
    reference: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    expected = dict(reference)
    for key in ("path", "metadata_path", "sha256", "bytes", "chars"):
        value = metadata.get(key)
        if value not in (None, ""):
            if key == "path":
                expected["file_path"] = value
            else:
                expected[key] = value
    return expected


def _verify_rehydrated_content(content: str, expected: dict[str, Any]) -> None:
    expected_sha = str(expected.get("sha256") or "").strip().lower()
    expected_bytes = expected.get("bytes")
    expected_chars = expected.get("chars")
    actual_bytes = len(_content_bytes(content))
    actual_chars = len(content)
    actual_sha = _sha256_text(content)
    problems: list[str] = []
    if isinstance(expected_bytes, int) and expected_bytes != actual_bytes:
        problems.append(f"bytes expected {expected_bytes}, got {actual_bytes}")
    if isinstance(expected_chars, int) and expected_chars != actual_chars:
        problems.append(f"chars expected {expected_chars}, got {actual_chars}")
    if expected_sha and expected_sha != actual_sha:
        problems.append(f"sha256 expected {expected_sha}, got {actual_sha}")
    if problems:
        raise ValueError("persisted tool result failed verification: " + "; ".join(problems))


def load_persisted_tool_result(
    message: str,
    *,
    env: Any = None,
    verify: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Load a persisted tool result preview block and return ``(full_text, metadata)``."""
    reference = parse_persisted_output_reference(message)
    if reference is None:
        return str(message or ""), {}
    metadata: dict[str, Any] = {}
    metadata_path = reference.get("metadata_path")
    if metadata_path:
        try:
            metadata = _load_metadata(str(metadata_path), env=env)
        except (OSError, json.JSONDecodeError, FileNotFoundError):
            metadata = {}
    expected = _expected_from_reference(reference, metadata)
    file_path = str(expected.get("file_path") or reference["file_path"])
    content = _read_reference_text(file_path, env=env)
    if verify:
        _verify_rehydrated_content(content, expected)
    if not metadata:
        metadata = {
            "path": file_path,
            "metadata_path": metadata_path,
            "sha256": reference.get("sha256"),
            "bytes": reference.get("bytes"),
            "chars": reference.get("chars"),
        }
    return content, metadata


def load_persisted_tool_result_path(
    path: str,
    *,
    env: Any = None,
    verify: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Load a persisted result by content path, verifying its sidecar when present."""
    file_path = str(path or "")
    metadata_path = _metadata_path_for(file_path)
    metadata: dict[str, Any] = {}
    try:
        metadata = _load_metadata(metadata_path, env=env)
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        metadata = {}
    expected = _expected_from_reference({"file_path": file_path}, metadata)
    content = _read_reference_text(str(expected.get("file_path") or file_path), env=env)
    if verify and metadata:
        _verify_rehydrated_content(content, expected)
    if not metadata:
        metadata = {"path": file_path, "metadata_path": metadata_path}
    return content, metadata


def rehydrate_persisted_tool_result(message: str, *, env: Any = None, verify: bool = True) -> str:
    """Return the full persisted content for a preview block, or the input if not persisted."""
    content, _metadata = load_persisted_tool_result(message, env=env, verify=verify)
    return content


def rehydrate_tool_result_message(message: Any, *, env: Any = None, verify: bool = True) -> bool:
    """Replace a tool message preview with its full persisted content when possible."""
    content = _message_content(message)
    if parse_persisted_output_reference(content) is None:
        return False
    full = rehydrate_persisted_tool_result(content, env=env, verify=verify)
    _set_message_content(message, full)
    return True


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _set_message_content(message: Any, content: str) -> None:
    if isinstance(message, dict):
        message["content"] = content
    else:
        message.content = content


def _message_tool_call_id(message: Any, index: int) -> str:
    if isinstance(message, dict):
        return str(message.get("tool_call_id") or message.get("id") or f"budget_{index}")
    return str(getattr(message, "tool_call_id", "") or getattr(message, "id", "") or f"budget_{index}")


def _message_name(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("name") or message.get("tool_name") or "tool")
    return str(getattr(message, "name", "") or "tool")


def enforce_turn_budget(
    messages: list[Any],
    *,
    env: Any = None,
    turn_budget_chars: int = 200_000,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    local_dir: str | os.PathLike[str] | None = None,
) -> list[Any]:
    """Persist largest non-persisted tool messages until the turn is under budget."""
    if not messages or turn_budget_chars <= 0:
        return messages

    candidates: list[tuple[int, int]] = []
    total = 0
    for idx, message in enumerate(messages):
        content = _message_content(message)
        size = len(content)
        total += size
        if content and PERSISTED_OUTPUT_TAG not in content and SPILL_MARKER not in content:
            candidates.append((idx, size))

    if total <= turn_budget_chars:
        return messages

    for idx, size in sorted(candidates, key=lambda item: item[1], reverse=True):
        if total <= turn_budget_chars:
            break
        message = messages[idx]
        original = _message_content(message)
        replacement = maybe_persist_tool_result(
            original,
            _message_name(message),
            _message_tool_call_id(message, idx),
            env=env,
            threshold_chars=0,
            preview_chars=preview_chars,
            reason=f"tool-batch budget exceeded ({turn_budget_chars:,} chars)",
            local_dir=local_dir,
        )
        if replacement != original:
            _set_message_content(message, replacement)
            total += len(replacement) - size
    return messages


__all__ = [
    "DEFAULT_PREVIEW_CHARS",
    "DEFAULT_STORAGE_DIR",
    "METADATA_SUFFIX",
    "PERSISTED_OUTPUT_CLOSE",
    "PERSISTED_OUTPUT_TAG",
    "SPILL_MARKER",
    "enforce_turn_budget",
    "generate_preview",
    "load_persisted_tool_result",
    "load_persisted_tool_result_path",
    "maybe_persist_tool_result",
    "parse_persisted_output_reference",
    "rehydrate_persisted_tool_result",
    "rehydrate_tool_result_message",
]
