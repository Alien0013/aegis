"""Semantic code index — embeddings retrieval over the repo (Cursor/Aider-style).

Chunks source files, embeds them with an OpenAI-compatible embeddings API, stores the
normalized vectors in a local sqlite index, and answers natural-language code queries by
cosine similarity. Incremental: only re-embeds files whose mtime changed. Degrades to the
structural repo map / ripgrep (handled by the caller) when no embeddings key is set.

Config (``embeddings.*``): base_url (default OpenAI), model (text-embedding-3-small),
api_key (or env EMBEDDINGS_API_KEY / OPENAI_API_KEY), chunk_lines.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import config as cfg
from . import repomap

_DEFAULT_BASE = "https://api.openai.com/v1"
_DEFAULT_MODEL = "text-embedding-3-small"
_CHUNK_LINES = 60
_BATCH = 96
_MAX_FILE_BYTES = 400_000


def _settings(config) -> tuple[str, str, str, int]:
    get = config.get if config is not None else (lambda _k, d=None: d)
    base = str(get("embeddings.base_url", "") or _DEFAULT_BASE).rstrip("/")
    model = str(get("embeddings.model", "") or _DEFAULT_MODEL)
    key = (str(get("embeddings.api_key", "") or "")
           or os.environ.get("EMBEDDINGS_API_KEY", "")
           or os.environ.get("OPENAI_API_KEY", ""))
    chunk_lines = int(get("embeddings.chunk_lines", _CHUNK_LINES) or _CHUNK_LINES)
    return base, model, key, chunk_lines


def embeddings_available(config) -> bool:
    """True when an embeddings API key is configured (else the caller falls back)."""
    return bool(_settings(config)[2])


def _embed(texts: list[str], config) -> list[list[float]]:
    """Embed a list of texts via an OpenAI-compatible /embeddings endpoint (batched)."""
    import httpx

    base, model, key, _ = _settings(config)
    if not key:
        raise RuntimeError("no embeddings api key configured (embeddings.api_key / OPENAI_API_KEY)")
    out: list[list[float]] = []
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(texts), _BATCH):
            batch = texts[i:i + _BATCH]
            r = client.post(f"{base}/embeddings", headers=headers,
                            json={"model": model, "input": batch})
            r.raise_for_status()
            out.extend(item["embedding"] for item in r.json()["data"])
    return out


def _index_path() -> Path:
    return cfg.sub("code_index.db")


def _connect() -> sqlite3.Connection:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS chunks (
                     path TEXT, start INTEGER, end INTEGER, text TEXT,
                     mtime REAL, vec BLOB)""")
    c.execute("CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path)")
    return c


def _chunk_file(text: str, chunk_lines: int) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    out: list[tuple[int, int, str]] = []
    for start in range(0, len(lines), chunk_lines):
        block = lines[start:start + chunk_lines]
        body = "\n".join(block).strip()
        if body:
            out.append((start + 1, start + len(block), body))
    return out


def _normalize(vec: list[float]) -> bytes:
    import numpy as np

    arr = np.asarray(vec, dtype="float32")
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tobytes()


def build(root: Path, config, *, force: bool = False) -> dict:
    """(Re)build the index for ``root``. Incremental: a file whose mtime matches the
    stored value is skipped unless ``force``. Returns counts."""
    if not embeddings_available(config):
        return {"ok": False, "reason": "no embeddings api key configured"}
    root = root.resolve()
    _, _, _, chunk_lines = _settings(config)
    files = repomap.list_source_files(root)
    conn = _connect()
    indexed = skipped = chunk_count = 0
    try:
        stored_mtime = dict(conn.execute("SELECT path, mtime FROM chunks GROUP BY path").fetchall())
        for path in files:
            try:
                st = path.stat()
                if st.st_size > _MAX_FILE_BYTES:
                    continue
                rel = str(path.relative_to(root))
            except (OSError, ValueError):
                continue
            if not force and abs(stored_mtime.get(rel, -1) - st.st_mtime) < 1e-6:
                skipped += 1
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks = _chunk_file(text, chunk_lines)
            if not chunks:
                continue
            vectors = _embed([f"{rel}\n{c[2]}" for c in chunks], config)
            conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
            conn.executemany(
                "INSERT INTO chunks (path, start, end, text, mtime, vec) VALUES (?,?,?,?,?,?)",
                [(rel, c[0], c[1], c[2], st.st_mtime, _normalize(v))
                 for c, v in zip(chunks, vectors)])
            conn.commit()
            indexed += 1
            chunk_count += len(chunks)
        # drop chunks for files that no longer exist
        live = {str(p.relative_to(root)) for p in files if _is_relative(p, root)}
        for (rel,) in conn.execute("SELECT DISTINCT path FROM chunks").fetchall():
            if rel not in live:
                conn.execute("DELETE FROM chunks WHERE path=?", (rel,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "indexed": indexed, "skipped": skipped, "chunks": chunk_count}


def _is_relative(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def search(root: Path, query: str, config, *, k: int = 8) -> list[dict]:
    """Top-``k`` code chunks most similar to ``query`` (cosine). Builds the index first
    if it's empty. Returns [{path, start, end, score, snippet}]."""
    import numpy as np

    if not embeddings_available(config):
        return []
    conn = _connect()
    try:
        if conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0:
            conn.close()
            build(root, config)
            conn = _connect()
        rows = conn.execute("SELECT path, start, end, text, vec FROM chunks").fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    qv = np.asarray(_embed([query], config)[0], dtype="float32")
    qn = np.linalg.norm(qv)
    if qn > 0:
        qv = qv / qn
    mat = np.frombuffer(b"".join(r[4] for r in rows), dtype="float32").reshape(len(rows), -1)
    scores = mat @ qv
    top = np.argsort(-scores)[:k]
    return [{"path": rows[i][0], "start": rows[i][1], "end": rows[i][2],
             "score": round(float(scores[i]), 3),
             "snippet": rows[i][3][:600]} for i in top]
