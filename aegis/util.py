"""Small shared utilities: atomic writes, paths, token estimates, time."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .constants import CHARS_PER_TOKEN


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_local() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: Path, content: str) -> None:
    """Write to a temp file in the same dir, fsync, then os.replace (atomic on POSIX)."""
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return default


def append_line(path: Path, line: str) -> None:
    """Append a single line with fsync; ensures durability for jsonl logs."""
    ensure_dir(path.parent)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
        f.flush()
        os.fsync(f.fileno())


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    s = _slug_re.sub("-", text.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "untitled"


_ENC = None
_ENC_TRIED = False


def estimate_tokens(text: str) -> int:
    """Token estimate — uses tiktoken when installed, else ~4 chars/token."""
    global _ENC, _ENC_TRIED
    if not text:
        return 0
    if not _ENC_TRIED:
        _ENC_TRIED = True
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001
            _ENC = None
    if _ENC is not None:
        try:
            return len(_ENC.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001
            pass
    return max(1, len(text) // CHARS_PER_TOKEN)


def truncate(text: str, max_chars: int, marker: str = "\n…[truncated]…\n") -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head - len(marker)
    if tail <= 0:
        return text[:max_chars] + marker
    return text[:head] + marker + text[-tail:]


def encode_image(path: Path) -> str:
    """Read an image file and return a base64 data URL for vision-capable models."""
    import base64
    import mimetypes

    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
