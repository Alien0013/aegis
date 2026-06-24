#!/usr/bin/env python3
"""Watch a GitHub repo's issues / pulls / releases / commits; print new ones each run.

Usage:
    python watch_github.py --name <id> --repo owner/name \\
        --type issues|pulls|releases|commits [--limit 20]

Reads ``GITHUB_TOKEN`` from the env when present (higher rate limit, private repos).
Deduped by issue/PR/release id or commit sha. Nothing on no-change; non-zero on error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

from _watermark import select_new

_PATHS = {
    "issues": "issues?state=all&sort=created&direction=desc&per_page=30",
    "pulls": "pulls?state=all&sort=created&direction=desc&per_page=30",
    "releases": "releases?per_page=30",
    "commits": "commits?per_page=30",
}


def fetch(repo: str, kind: str, timeout: float = 20.0):
    url = f"https://api.github.com/repos/{repo}/{_PATHS[kind]}"
    headers = {"User-Agent": "aegis-watcher/1.0", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed api.github.com host
        return json.loads(r.read().decode("utf-8", "replace"))


def normalize(kind: str, raw: list) -> list[dict]:
    out: list[dict] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        if kind == "commits":
            sha = it.get("sha", "")
            msg = ((it.get("commit") or {}).get("message") or "").splitlines()[:1]
            out.append({"id": sha, "title": (msg[0] if msg else sha[:8]), "url": it.get("html_url", "")})
        elif kind == "releases":
            out.append({"id": str(it.get("id")), "title": it.get("name") or it.get("tag_name") or "release",
                        "url": it.get("html_url", "")})
        else:  # issues / pulls
            # the issues endpoint also returns PRs; keep them — caller chose the type
            out.append({"id": str(it.get("id")), "title": f"#{it.get('number')} {it.get('title', '')}".strip(),
                        "url": it.get("html_url", "")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--type", default="issues", choices=list(_PATHS))
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    try:
        items = normalize(args.type, fetch(args.repo, args.type))
    except Exception as exc:  # noqa: BLE001
        print(f"watch_github: failed for {args.repo} {args.type}: {exc}", file=sys.stderr)
        return 1

    new = select_new(args.name, items, key=lambda it: it["id"])
    for it in new[: args.limit]:
        print(f"## {it['title']}")
        if it.get("url"):
            print(it["url"])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
