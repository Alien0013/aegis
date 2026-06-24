#!/usr/bin/env python3
"""Watch an RSS 2.0 / Atom feed and print entries new since the last run.

Usage:
    python watch_rss.py --name <watcher-id> --url <feed-url> [--limit 20]

Prints each new entry as ``## <title>\\n<url>\\n\\n<summary>``; nothing on no-change.
Exits non-zero on a fetch/parse error. State is deduped by <guid>/<id>/<link>.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import xml.etree.ElementTree as ET

from _watermark import select_new


def fetch(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "aegis-watcher/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - user-provided feed URL
        return r.read()


def _tag(el) -> str:
    return el.tag.split("}")[-1].lower()


def _text(el, *names: str) -> str:
    for child in el:
        if _tag(child) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _link(el) -> str:
    # RSS: <link>url</link>; Atom: <link href="url" rel="alternate"/>
    for child in el:
        if _tag(child) != "link":
            continue
        if (child.text or "").strip():
            return child.text.strip()
        href = child.attrib.get("href")
        if href and child.attrib.get("rel", "alternate") in ("alternate", ""):
            return href
    return ""


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items: list[dict] = []
    for el in root.iter():
        t = _tag(el)
        if t not in ("item", "entry"):
            continue
        title = _text(el, "title") or "(untitled)"
        link = _link(el)
        guid = _text(el, "guid", "id") or link or title
        summary = _text(el, "description", "summary", "content")
        items.append({"id": guid, "title": title, "url": link, "summary": summary})
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    try:
        items = parse_feed(fetch(args.url))
    except Exception as exc:  # noqa: BLE001
        print(f"watch_rss: fetch/parse failed for {args.url}: {exc}", file=sys.stderr)
        return 1

    new = select_new(args.name, items, key=lambda it: it["id"])[: args.limit]
    for it in new:
        body = (it.get("summary") or "").strip()
        print(f"## {it['title']}")
        if it.get("url"):
            print(it["url"])
        if body:
            print(f"\n{body[:500]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
