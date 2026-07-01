from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aegis.session import Session, SessionStore
from aegis.types import Message


_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(offset: int) -> str:
    return (_BASE_TIME + timedelta(seconds=offset)).isoformat(timespec="seconds")


def _save_session(
    store: SessionStore,
    sid: str,
    *,
    parent_id: str | None = None,
    meta: dict | None = None,
    messages: tuple[str, ...] = ("message",),
    offset: int = 0,
) -> Session:
    session = Session(id=sid, title=sid, parent_id=parent_id)
    session.meta.update(meta or {})
    session.messages = [Message.user(text) for text in messages]
    store.save(session)
    timestamp = _iso(offset)
    with store._conn() as conn:
        conn.execute(
            "UPDATE sessions SET created_at=?, updated_at=? WHERE id=?",
            (timestamp, timestamp, sid),
        )
        conn.execute(
            "UPDATE messages SET created_at=? WHERE session_id=?",
            (timestamp, sid),
        )
    loaded = store.load(sid)
    assert loaded is not None
    return loaded


def _resolve_resume_id(store: SessionStore, sid: str) -> str:
    return store.resolve_resume_session_id(sid)


def test_resume_parent_with_messages_returns_live_compression_child_tip():
    store = SessionStore()
    _save_session(
        store,
        "parent",
        meta={"end_reason": "compression"},
        messages=("before compression",),
        offset=0,
    )
    _save_session(
        store,
        "live-child",
        parent_id="parent",
        meta={"creator_kind": "compression", "parent_end_reason": "compression"},
        messages=("after compression",),
        offset=10,
    )

    assert _resolve_resume_id(store, "parent") == "live-child"


def test_resume_walks_multi_depth_compression_chain_to_latest_real_tip():
    store = SessionStore()
    _save_session(
        store,
        "root",
        meta={"end_reason": "compression"},
        messages=("root turn",),
        offset=0,
    )
    _save_session(
        store,
        "middle",
        parent_id="root",
        meta={
            "creator_kind": "compression",
            "parent_end_reason": "compression",
            "end_reason": "compression",
        },
        messages=("middle turn",),
        offset=10,
    )
    _save_session(
        store,
        "tip",
        parent_id="middle",
        meta={"creator_kind": "compression", "parent_end_reason": "compression"},
        messages=("tip turn",),
        offset=20,
    )

    assert _resolve_resume_id(store, "root") == "tip"
    assert _resolve_resume_id(store, "middle") == "tip"


def test_manual_branch_children_do_not_hijack_resume():
    store = SessionStore()
    _save_session(store, "parent", messages=("parent turn",), offset=0)
    _save_session(
        store,
        "branch-reason-child",
        parent_id="parent",
        meta={"branch_reason": "manual_branch"},
        messages=("branch reason turn",),
        offset=30,
    )
    _save_session(
        store,
        "branched-from-child",
        parent_id="parent",
        meta={"_branched_from": "parent"},
        messages=("branched from turn",),
        offset=40,
    )

    assert _resolve_resume_id(store, "parent") == "parent"


def test_subagent_and_delegate_children_do_not_hijack_resume():
    store = SessionStore()
    _save_session(store, "parent", messages=("parent turn",), offset=0)
    _save_session(
        store,
        "creator-kind-subagent",
        parent_id="parent",
        meta={"creator_kind": "subagent"},
        messages=("subagent creator turn",),
        offset=10,
    )
    _save_session(
        store,
        "subagent-id-child",
        parent_id="parent",
        meta={"subagent_id": "sub_123"},
        messages=("subagent id turn",),
        offset=20,
    )
    _save_session(
        store,
        "delegate-child",
        parent_id="parent",
        meta={"_delegate_from": "parent"},
        messages=("delegate turn",),
        offset=30,
    )

    assert _resolve_resume_id(store, "parent") == "parent"


def test_resume_prefers_live_compression_child_over_stale_non_compression_sibling():
    store = SessionStore()
    _save_session(
        store,
        "parent",
        meta={"end_reason": "compression"},
        messages=("before compression",),
        offset=0,
    )
    _save_session(
        store,
        "live-child",
        parent_id="parent",
        meta={"creator_kind": "compression", "parent_end_reason": "compression"},
        messages=("live continuation",),
        offset=10,
    )
    _save_session(
        store,
        "stale-sibling",
        parent_id="parent",
        meta={"end_reason": "ws_orphan_reap"},
        messages=("stale sibling",),
        offset=60,
    )

    assert _resolve_resume_id(store, "parent") == "live-child"
