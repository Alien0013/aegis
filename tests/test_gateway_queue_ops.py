from __future__ import annotations

import json


def _queue(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.gateway.queue import OutboxQueue

    return OutboxQueue()


def test_failed_messages_appear_as_dead_letters_with_safe_text(tmp_path, monkeypatch):
    q = _queue(tmp_path, monkeypatch)
    text = "send this sk-proj-" + ("A" * 32) + " " + ("x" * 1200)
    q.enqueue("telegram", "chat1", text, metadata={"auth_token": "secret-value", "safe": "ok"})
    row = q.due()[0]

    q.mark_failed(row["id"], attempts=0, max_attempts=1)

    dead_letters = q.dead_letters()
    assert len(dead_letters) == 1
    item = dead_letters[0]
    assert item["id"] == row["id"]
    assert item["status"] == "failed"
    assert "[REDACTED]" in item["text"]
    assert "sk-proj" not in item["text"]
    assert item["text_truncated"] is True
    assert item["metadata"] == {"auth_token": "[REDACTED]", "safe": "ok"}
    json.dumps(item)


def test_retry_resets_dead_letter_to_pending(tmp_path, monkeypatch):
    q = _queue(tmp_path, monkeypatch)
    q.enqueue("telegram", "chat1", "try again")
    row_id = q.due()[0]["id"]
    q.mark_failed(row_id, attempts=4, max_attempts=5)
    assert q.dead_letters()[0]["attempts"] == 5

    retried = q.retry(row_id)

    assert retried["ok"] is True
    assert retried["id"] == row_id
    assert retried["status"] == "pending"
    assert retried["attempts"] == 0
    assert q.dead_letters() == []
    due = q.due()
    assert len(due) == 1
    assert due[0]["id"] == row_id
    assert due[0]["status"] == "pending"
    assert due[0]["attempts"] == 0


def test_discard_marks_dead_letter_discarded(tmp_path, monkeypatch):
    q = _queue(tmp_path, monkeypatch)
    q.enqueue("telegram", "chat1", "drop me")
    row_id = q.due()[0]["id"]
    q.mark_failed(row_id, attempts=4, max_attempts=5)

    discarded = q.discard(row_id)

    assert discarded["ok"] is True
    assert discarded["id"] == row_id
    assert discarded["status"] == "discarded"
    assert q.dead_letters() == []
    messages = q.list_messages(status="discarded")
    assert len(messages) == 1
    assert messages[0]["id"] == row_id
    assert messages[0]["status"] == "discarded"


def test_stats_counts_statuses(tmp_path, monkeypatch):
    q = _queue(tmp_path, monkeypatch)
    for text in ("pending", "sent", "failed", "discarded"):
        q.enqueue("telegram", "chat1", text)
    rows = q.due()
    by_text = {row["text"]: row["id"] for row in rows}
    q.mark_sent(by_text["sent"])
    q.mark_failed(by_text["failed"], attempts=4, max_attempts=5)
    q.discard(by_text["discarded"])

    stats = q.stats()

    assert stats["pending"] == 1
    assert stats["sent"] == 1
    assert stats["failed"] == 1
    assert stats["discarded"] == 1
    assert stats["total"] == 4
    assert stats["statuses"] == {
        "pending": 1,
        "sent": 1,
        "failed": 1,
        "discarded": 1,
    }
    json.dumps(stats)
