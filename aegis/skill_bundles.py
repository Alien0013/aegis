"""File-backed skill bundles.

Bundles live under ``~/.aegis/skill-bundles/*.yaml`` and map one slash/preload
name to multiple skills. Config-based ``skills.bundles`` still works; file
bundles provide the AEGIS-style list/save/delete lifecycle.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import config as cfg
from .util import atomic_write

_INVALID_CHARS = re.compile(r"[^a-z0-9-]")
_MULTI_HYPHEN = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    slug = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    slug = _INVALID_CHARS.sub("", slug)
    return _MULTI_HYPHEN.sub("-", slug).strip("-")


def bundles_dir() -> Path:
    path = cfg.sub("skill-bundles")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _coerce_skills(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _bundle_path(name: str) -> Path:
    slug = slugify(name)
    if not slug:
        raise ValueError("bundle name is required")
    return bundles_dir() / f"{slug}.yaml"


def load_bundles() -> dict[str, dict]:
    """Return ``slug -> bundle`` for every readable YAML bundle file."""
    out: dict[str, dict] = {}
    base = bundles_dir()
    for path in sorted([*base.glob("*.yaml"), *base.glob("*.yml")]):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or path.stem).strip()
        slug = slugify(name or path.stem)
        skills = _coerce_skills(data.get("skills"))
        if not slug or not skills:
            continue
        out[slug] = {
            "name": name or slug,
            "slug": slug,
            "description": str(data.get("description") or ""),
            "skills": skills,
            "instruction": str(data.get("instruction") or ""),
            "path": str(path),
        }
    return out


def list_bundles() -> list[dict]:
    return sorted(load_bundles().values(), key=lambda item: item["slug"])


def save_bundle(
    name: str,
    skills: list[str],
    *,
    description: str = "",
    instruction: str = "",
) -> dict:
    slug = slugify(name)
    members = _coerce_skills(skills)
    if not slug:
        raise ValueError("bundle name is required")
    if not members:
        raise ValueError("bundle needs at least one skill")
    data = {
        "name": slug,
        "description": str(description or ""),
        "skills": members,
    }
    if instruction:
        data["instruction"] = str(instruction)
    path = _bundle_path(slug)
    atomic_write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return {**data, "slug": slug, "path": str(path)}


def delete_bundle(name: str) -> bool:
    path = _bundle_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True
