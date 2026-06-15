"""Kanban tool: let the agent manage its own task board (the same board behind
``aegis kanban`` and the dashboard). One action-dispatched tool so the model can plan,
fan out dependent work, track multi-step tasks across turns, and hand off to other workers.
"""

from __future__ import annotations

import json

from .base import Tool, ToolContext, ToolResult

_STATUSES = ("triage", "todo", "scheduled", "ready", "in_progress",
             "blocked", "review", "done", "archived")


def _coerce_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"note": raw}
        except json.JSONDecodeError:
            return {"note": raw}
    return {}


class KanbanTool(Tool):
    name = "kanban"
    description = (
        "Manage your task board (persists across turns/sessions, shared with other workers). "
        "actions: list | create | show | move | complete | block | unblock | comment | "
        "heartbeat | link | runs. "
        "create accepts parents=[ids] (dependent cards start gated in 'todo' and auto-promote "
        "to 'ready' when every parent is done), assignee, tenant, workspace (scratch|dir:<path>|"
        "worktree). complete takes text=summary + metadata={changed_files,tests_run,...} and "
        "optional created_cards=[ids you created] (verified against the board). show returns "
        "parent handoffs, prior run attempts (for retries), and the comment thread. Use this "
        "for multi-step or multi-specialist work you want durable and auditable."
    )
    groups = ["automation"]
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["list", "create", "show", "move", "complete", "block",
                                "unblock", "comment", "heartbeat", "link", "runs"]},
            "id": {"type": "string", "description": "Card id (show/move/complete/block/...)."},
            "title": {"type": "string", "description": "Card title (create)."},
            "body": {"type": "string", "description": "Card details (create)."},
            "status": {"type": "string", "enum": list(_STATUSES),
                       "description": "Target column (move)."},
            "text": {"type": "string",
                     "description": "Comment text / block reason / complete summary / heartbeat note."},
            "metadata": {"type": "object",
                         "description": "Machine-readable handoff facts (complete)."},
            "priority": {"type": "integer", "description": "0-3, higher = sooner (create)."},
            "assignee": {"type": "string", "description": "Profile/lane to own the card (create)."},
            "parents": {"type": "array", "items": {"type": "string"},
                        "description": "Parent card ids this card depends on (create)."},
            "parent": {"type": "string", "description": "Parent id (link)."},
            "child": {"type": "string", "description": "Child id (link)."},
            "tenant": {"type": "string", "description": "Tenant namespace (create)."},
            "workspace": {"type": "string",
                          "description": "scratch | dir:<path> | worktree (create)."},
            "created_cards": {"type": "array", "items": {"type": "string"},
                              "description": "Ids you created this run, to claim on complete."},
            "filter_status": {"type": "string", "enum": list(_STATUSES),
                              "description": "Only list this column (list)."},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..kanban import KanbanStore, _fmt_task as format_task
        store = KanbanStore()
        action = args.get("action")
        worker = self._worker(ctx)
        try:
            if action == "list":
                tasks = store.list(status=args.get("filter_status"))
                if not tasks:
                    return ToolResult.ok("(board empty)", display="kanban: empty")
                body = "\n".join(format_task(t) for t in tasks)
                return ToolResult.ok(body, display=f"kanban: {len(tasks)} card(s)")

            if action == "create":
                if not args.get("title"):
                    return ToolResult.error("create needs a title")
                t = store.create(
                    args["title"].strip(), args.get("body", ""),
                    int(args.get("priority", 0) or 0),
                    assignee=args.get("assignee", "") or "",
                    parents=list(args.get("parents") or []),
                    tenant=args.get("tenant", "") or "",
                    workspace=args.get("workspace", "scratch") or "scratch",
                    created_by=worker,
                )
                gated = " (gated: todo)" if t.status == "todo" else ""
                return ToolResult.ok(f"created {t.id}: {t.title}{gated}",
                                     display=f"kanban + {t.title[:40]}")

            if action == "show":
                tid = args.get("id", "")
                t = store.show(tid)
                if t is None:
                    return ToolResult.error(f"no card {tid!r}")
                return ToolResult.ok(self._render_show(store, t),
                                     display=f"kanban: {t.title[:40]}")

            if action == "move":
                if args.get("status") not in _STATUSES:
                    return ToolResult.error(f"status must be one of {', '.join(_STATUSES)}")
                ok = store._set_status(args.get("id", ""), args["status"])
                return (ToolResult.ok(f"moved {args.get('id')} → {args['status']}")
                        if ok else ToolResult.error(f"no card {args.get('id')!r}"))

            if action == "complete":
                tid = args.get("id", "")
                created = list(args.get("created_cards") or [])
                if created:
                    valid, bad = store.verify_created_cards(created, worker)
                    if bad:
                        return ToolResult.error(
                            "created_cards rejected (not on the board or created by another "
                            f"worker): {', '.join(bad)}. Only list ids you actually created.")
                ok = store.complete(tid, summary=args.get("text", "") or "",
                                    metadata=_coerce_meta(args.get("metadata")))
                if not ok:
                    return ToolResult.error(f"no card {tid!r}")
                extra = f" (claimed {len(created)} child card(s))" if created else ""
                return ToolResult.ok(f"completed {tid}{extra}")

            if action == "block":
                ok = store.block(args.get("id", ""), args.get("text", ""))
                return ToolResult.ok(f"blocked {args.get('id')}") if ok else \
                    ToolResult.error(f"no card {args.get('id')!r}")

            if action == "unblock":
                ok = store.unblock(args.get("id", ""))
                return ToolResult.ok(f"unblocked {args.get('id')} → ready") if ok else \
                    ToolResult.error(f"could not unblock {args.get('id')!r}")

            if action == "comment":
                if not args.get("text"):
                    return ToolResult.error("comment needs text")
                store.comment(args.get("id", ""), args["text"], author=worker)
                return ToolResult.ok(f"commented on {args.get('id')}")

            if action == "heartbeat":
                ok = store.heartbeat(args.get("id", ""), args.get("text", "") or "")
                return ToolResult.ok(f"heartbeat {args.get('id')}") if ok else \
                    ToolResult.error(f"no card {args.get('id')!r}")

            if action == "link":
                parent, child = args.get("parent", ""), args.get("child", "")
                if not parent or not child:
                    return ToolResult.error("link needs parent and child ids")
                ok = store.link(parent, child)
                return ToolResult.ok(f"linked {parent} → {child}") if ok else \
                    ToolResult.error("could not link (one id not found)")

            if action == "runs":
                runs = store.runs(args.get("id", ""))
                if not runs:
                    return ToolResult.ok("(no runs yet)", display="kanban: runs")
                body = "\n".join(
                    f"#{r.id} @{r.profile or '?'} {r.outcome or r.status}"
                    + (f" — {r.summary}" if r.summary else "")
                    + (f" [error: {r.error}]" if r.error else "")
                    for r in runs)
                return ToolResult.ok(body, display=f"kanban: {len(runs)} run(s)")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"kanban error: {e}")
        return ToolResult.error(f"unknown action {action!r}")

    @staticmethod
    def _worker(ctx: ToolContext) -> str:
        meta = getattr(ctx, "meta", None) or {}
        return str(meta.get("kanban_worker") or "agent")

    @staticmethod
    def _render_show(store, t) -> str:
        lines = [f"{t.id} [{t.status}] P{t.priority}"
                 + (f" @{t.assignee}" if t.assignee else "")
                 + (f" tenant={t.tenant}" if t.tenant else ""),
                 t.title, "", t.body or "(no details)"]
        parents = store.parents(t.id)
        if parents:
            lines.append("\nparent handoffs:")
            for pid in parents:
                p = store.show(pid)
                if not p:
                    continue
                done = [r for r in store.runs(pid) if r.outcome == "completed"]
                lines.append(f"  {pid[:13]} [{p.status}] {p.title}"
                             + (f" — {done[-1].summary}" if done else ""))
        runs = store.runs(t.id)
        if runs:
            lines.append("\nprior attempts (you may be a retry — don't repeat failures):")
            for r in runs:
                lines.append(f"  #{r.id} {r.outcome or r.status}"
                             + (f" — {r.summary}" if r.summary else "")
                             + (f" [error: {r.error}]" if r.error else ""))
        comments = store.comments(t.id)
        if comments:
            lines.append("\ncomments:")
            for cm in comments:
                who = f"{cm.author}: " if cm.author else ""
                lines.append(f"  {who}{cm.text}")
        return "\n".join(lines)


def kanban_tools() -> list[Tool]:
    return [KanbanTool()]
