"""Dashboard file-browser compatibility routes."""

from __future__ import annotations

import base64
import inspect
import mimetypes
import os
import stat
from pathlib import Path
from typing import Any, cast

from .. import dashboard as dash
from ..dashboard_fastapi import JSONResponse, Request, _api_get, _api_post, _query_dict, _require_request

_TEXT_PREVIEW_MAX_BYTES = 512 * 1024
_TEXT_WRITE_MAX_BYTES = 2 * 1024 * 1024
_DATA_URL_MAX_BYTES = 2 * 1024 * 1024
_LANGUAGE_BY_EXT = {
    ".css": "css",
    ".csv": "csv",
    ".html": "html",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".rs": "rust",
    ".sh": "shell",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_HIDDEN_DIR_ENTRIES = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def _json(payload: dict, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


def _resolve_file_path(raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise ValueError("missing path")
    return Path(str(raw)).expanduser().resolve()


def _mime_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    sample = data[:4096]
    control = sum(1 for byte in sample if byte < 32 and byte not in {9, 10, 13})
    return bool(sample) and (control / len(sample)) > 0.20


def _regular_file(raw: str) -> tuple[Path | None, Any]:
    try:
        path = _resolve_file_path(raw)
    except ValueError as exc:
        return None, {"ok": False, "error": str(exc)}
    except Exception:  # noqa: BLE001
        return None, {"ok": False, "error": "bad path"}
    if dash._is_sensitive_path(path):
        return None, {"ok": False, "path": str(path), "error": "blocked: refusing to read a credential/key path"}
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, {"ok": False, "path": str(path), "error": "not found"}
    except PermissionError:
        return None, {"ok": False, "path": str(path), "error": "permission denied"}
    except OSError as exc:
        return None, {"ok": False, "path": str(path), "error": str(exc)}
    if not stat.S_ISREG(st.st_mode):
        return None, {"ok": False, "path": str(path), "error": "not a file"}
    return path, st


def _fs_listing(query: dict[str, list[str]]) -> dict:
    raw = (query.get("path", [""])[0] or "").strip()
    base = Path(raw).expanduser() if raw else Path.home()
    try:
        base = base.resolve()
    except Exception:  # noqa: BLE001
        base = Path.home().resolve()
    if not base.is_dir():
        return {"entries": [], "error": "ENOTDIR", "path": str(base)}
    entries = []
    try:
        with os.scandir(base) as scan:
            for entry in scan:
                if entry.name in _HIDDEN_DIR_ENTRIES:
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    entries.append({
                        "name": entry.name,
                        "path": str(base / entry.name),
                        "isDirectory": is_dir,
                        "is_directory": is_dir,
                    })
                except OSError:
                    continue
    except FileNotFoundError:
        return {"entries": [], "error": "ENOENT", "path": str(base)}
    except NotADirectoryError:
        return {"entries": [], "error": "ENOTDIR", "path": str(base)}
    except PermissionError:
        return {"entries": [], "error": "EACCES", "path": str(base)}
    def sort_key(item: dict) -> tuple[bool, str, str]:
        name = str(item.get("name") or "")
        return (not bool(item.get("isDirectory")), name.lower(), name)

    entries.sort(key=sort_key)
    return {"entries": entries, "path": str(base)}


def _read_text_payload(raw: str) -> tuple[dict, int]:
    path, meta = _regular_file(raw)
    if path is None:
        return meta, 400
    st = meta
    if st.st_size > _TEXT_WRITE_MAX_BYTES:
        return {"ok": False, "path": str(path), "error": "file too large"}, 413
    try:
        with path.open("rb") as handle:
            data = handle.read(min(st.st_size, _TEXT_PREVIEW_MAX_BYTES))
    except PermissionError:
        return {"ok": False, "path": str(path), "error": "permission denied"}, 403
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}, 400
    return {
        "ok": True,
        "binary": _looks_binary(data),
        "byteSize": st.st_size,
        "content": data.decode("utf-8", errors="replace"),
        "language": _LANGUAGE_BY_EXT.get(path.suffix.lower(), "text"),
        "mimeType": _mime_type(path),
        "path": str(path),
        "text": data.decode("utf-8", errors="replace"),
        "truncated": st.st_size > _TEXT_PREVIEW_MAX_BYTES,
    }, 200


def _read_data_url_payload(raw: str) -> tuple[dict, int]:
    path, meta = _regular_file(raw)
    if path is None:
        return meta, 400
    st = meta
    if st.st_size > _DATA_URL_MAX_BYTES:
        return {"ok": False, "path": str(path), "error": "file too large"}, 413
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except PermissionError:
        return {"ok": False, "path": str(path), "error": "permission denied"}, 403
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}, 400
    mime = _mime_type(path)
    data_url = f"data:{mime};base64,{data}"
    content = path.read_text(encoding="utf-8", errors="replace") if mime.startswith("text/") else ""
    return {
        "ok": True,
        "path": str(path),
        "size": st.st_size,
        "mime": mime,
        "mime_type": mime,
        "content": content,
        "data_url": data_url,
        "dataUrl": data_url,
    }, 200


def _write_text_payload(body: dict) -> tuple[dict, int]:
    raw = str(body.get("path") or "").strip()
    content = str(body.get("content") or body.get("text") or "")
    if len(content.encode("utf-8")) > _TEXT_WRITE_MAX_BYTES:
        return {"ok": False, "error": "content too large"}, 413
    try:
        target = _resolve_file_path(raw)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "bad path"}, 400
    if dash._is_sensitive_path(target):
        return {"ok": False, "path": str(target), "error": "blocked: refusing to write a credential/key path"}, 403
    if not target.parent.is_dir():
        return {"ok": False, "path": str(target), "error": "parent directory does not exist"}, 400
    try:
        st = target.stat()
    except FileNotFoundError:
        st = None
    except PermissionError:
        return {"ok": False, "path": str(target), "error": "permission denied"}, 403
    except OSError as exc:
        return {"ok": False, "path": str(target), "error": str(exc)}, 400
    if st is not None and not stat.S_ISREG(st.st_mode):
        return {"ok": False, "path": str(target), "error": "not a regular file"}, 400
    tmp = target.with_name(f".{target.name}.aegis-tmp-{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "path": str(target), "error": "permission denied"}, 403
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "path": str(target), "error": str(exc)}, 500
    return {"ok": True, "path": str(target), "byteSize": len(content.encode("utf-8"))}, 200


def register(app, config, chat_runner):
    @app.get("/api/files")
    async def api_files_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _json(dash._dashboard_files(_query_dict(request)))

    @app.get("/api/files/read")
    async def api_files_read(request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _read_data_url_payload(request.query_params.get("path", ""))
        return _json(payload, status_code=status)

    @app.post("/api/files/mkdir")
    async def api_files_mkdir(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload = _api_post("/api/files/mkdir", body if isinstance(body, dict) else {}, config, chat_runner, chat_fallback=False)
        return _json(payload if isinstance(payload, dict) else {"ok": False, "error": "bad request"}, status_code=200 if isinstance(payload, dict) and payload.get("ok") else 400)

    @app.post("/api/files/upload-stream")
    async def api_files_upload_stream(request: Request) -> JSONResponse:
        _require_request(request, config)
        form = await request.form()
        upload = form.get("file")
        read_upload: Any = getattr(upload, "read", None)
        if upload is None or not callable(read_upload):
            return _json({"ok": False, "error": "missing file"}, status_code=400)
        raw = str(form.get("path") or "").strip()
        if not raw:
            return _json({"ok": False, "error": "missing path"}, status_code=400)
        try:
            target = _resolve_file_path(raw)
        except Exception:  # noqa: BLE001
            return _json({"ok": False, "error": "bad path"}, status_code=400)
        if target.exists() and target.is_dir():
            target = target / Path(str(getattr(upload, "filename", "") or "upload.bin")).name
        if dash._is_sensitive_path(target):
            return _json({"ok": False, "path": str(target), "error": "blocked: refusing to write a credential/key path"}, status_code=403)
        maybe_data: Any = read_upload()
        data = cast(bytes, await maybe_data if inspect.isawaitable(maybe_data) else maybe_data)
        if len(data) > _DATA_URL_MAX_BYTES:
            return _json({"ok": False, "path": str(target), "error": "file too large"}, status_code=413)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except PermissionError:
            return _json({"ok": False, "path": str(target), "error": "permission denied"}, status_code=403)
        except OSError as exc:
            return _json({"ok": False, "path": str(target), "error": str(exc)}, status_code=500)
        return _json({"ok": True, "path": str(target), "size": target.stat().st_size})

    @app.get("/api/fs/list")
    async def api_fs_list(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _json(_fs_listing(_query_dict(request)))

    @app.get("/api/fs/read-text")
    async def api_fs_read_text(request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _read_text_payload(request.query_params.get("path", ""))
        return _json(payload, status_code=status)

    @app.post("/api/fs/write-text")
    async def api_fs_write_text(request: Request) -> JSONResponse:
        _require_request(request, config)
        body = await request.json()
        payload, status = _write_text_payload(body if isinstance(body, dict) else {})
        return _json(payload, status_code=status)

    @app.get("/api/fs/read-data-url")
    async def api_fs_read_data_url(request: Request) -> JSONResponse:
        _require_request(request, config)
        payload, status = _read_data_url_payload(request.query_params.get("path", ""))
        return _json(payload, status_code=status)

    @app.get("/api/fs/git-root")
    async def api_fs_git_root(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _json(_api_get("/api/fs/git-root", _query_dict(request), config))

    @app.get("/api/fs/default-cwd")
    async def api_fs_default_cwd(request: Request) -> JSONResponse:
        _require_request(request, config)
        return _json(_api_get("/api/fs/default-cwd", _query_dict(request), config))
