#!/usr/bin/env python3
"""Watch a JSON HTTP endpoint that returns a list of objects; print new items each run.

Usage:
    python watch_http_json.py --name <id> --url <endpoint> \\
        [--list-path data.items] [--id-field id] [--title-field title] \\
        [--url-field url] [--header 'Authorization: Bearer X'] [--limit 20]

``--list-path`` is a dotted path to the array inside the response (default: the response
is itself the array). Items are deduped by ``--id-field``. Nothing on no-change; non-zero
exit on fetch/parse error.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from _watermark import select_new


def fetch_json(url: str, headers: dict, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "aegis-watcher/1.0", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - user-provided URL
        return json.loads(r.read().decode("utf-8", "replace"))


def dig(data, path: str):
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--list-path", default="")
    ap.add_argument("--id-field", default="id")
    ap.add_argument("--title-field", default="title")
    ap.add_argument("--url-field", default="url")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    headers = {}
    for h in args.header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    try:
        data = fetch_json(args.url, headers)
        items = dig(data, args.list_path)
        if not isinstance(items, list):
            raise ValueError(f"expected a list at path '{args.list_path or '(root)'}', got {type(items).__name__}")
    except Exception as exc:  # noqa: BLE001
        print(f"watch_http_json: failed for {args.url}: {exc}", file=sys.stderr)
        return 1

    objs = [it for it in items if isinstance(it, dict)]
    new = select_new(args.name, objs, key=lambda it: it.get(args.id_field, json.dumps(it, sort_keys=True)))
    for it in new[: args.limit]:
        title = str(it.get(args.title_field) or it.get(args.id_field) or "(item)")
        url = it.get(args.url_field)
        print(f"## {title}")
        if url:
            print(url)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
