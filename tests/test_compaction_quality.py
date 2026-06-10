"""Compaction quality guards: token-budget tail, iterative summary fold,
deterministic failure fallback, aux-model input fitting."""

from __future__ import annotations

from aegis.types import Message


class FakeProvider:
    """Records the summarizer input; returns a canned summary."""
    context_length = 200_000

    def __init__(self, reply="SUMMARY"):
        self.reply = reply
        self.seen_user = None

    def complete(self, messages, tools=None, **kw):
        self.seen_user = messages[-1].content
        if self.reply is None:
            raise RuntimeError("summarizer down")
        return type("R", (), {"text": self.reply})()


def _convo(n_user_msgs, body="x"):
    msgs = [Message.system("sys")]
    for i in range(n_user_msgs):
        msgs.append(Message.user(f"{body}{i}"))
        msgs.append(Message.assistant(f"reply{i}"))
    return msgs


# --- token-budget tail -------------------------------------------------------
def test_tail_protected_by_tokens_not_message_count():
    from aegis.agent.compaction import compress

    # 60 tiny exchanges. With a token budget, MANY recent tiny messages are kept
    # (a fixed preserve_last=20 would keep exactly 20 regardless of size).
    msgs = _convo(60, body="tiny ")
    out = compress(msgs, FakeProvider(), preserve_first=2, tail_tokens=400)
    kept_tail = [m for m in out if m.role in ("user", "assistant")
                 and not m.content.startswith("[Earlier")]
    # the most recent exchange is always present
    assert any(m.content == "reply59" for m in out)
    # token budget keeps more than a fixed count of 20 would, since each msg is tiny
    assert len(kept_tail) > 20


def test_huge_recent_messages_dont_blow_budget():
    from aegis.agent.compaction import compress
    # one giant recent message: token budget keeps few messages (not a fixed 20 huge ones)
    msgs = [Message.system("s"), Message.user("start")]
    for i in range(25):
        msgs.append(Message.assistant("z" * 4000))
        msgs.append(Message.user(f"u{i}"))
    out = compress(msgs, FakeProvider(), preserve_first=2, tail_tokens=2000)
    assert any("[Earlier conversation summarized]" in (m.content or "") for m in out)


# --- iterative summary fold --------------------------------------------------
def test_prior_summary_is_folded_not_resummarized():
    from aegis.agent.compaction import compress, _SUMMARY_MARKER

    prior_body = "Primary request: build the parser. Completed: lexer."
    msgs = [Message.system("s"), Message.user("u0"), Message.assistant("a0"),
            Message.assistant(f"{_SUMMARY_MARKER}\n{prior_body}")]
    for i in range(10):
        msgs.append(Message.user(f"new material number {i} with enough words to count "))
        msgs.append(Message.assistant(f"reply {i} doing substantial work on the module "))
    fp = FakeProvider(reply="CONSOLIDATED")
    out = compress(msgs, fp, preserve_first=2, tail_tokens=40)
    # the prior summary was handed to the summarizer as PRIOR SUMMARY context...
    assert "PRIOR SUMMARY" in fp.seen_user and prior_body in fp.seen_user
    # ...and there is exactly ONE summary note in the result (no stacking)
    notes = [m for m in out if (m.content or "").startswith(_SUMMARY_MARKER)]
    assert len(notes) == 1 and "CONSOLIDATED" in notes[0].content


def test_middle_all_prior_summaries_keeps_latest_without_call():
    from aegis.agent.compaction import compress, _SUMMARY_MARKER
    msgs = [Message.system("s"), Message.user("u0"), Message.assistant("a0"),
            Message.assistant(f"{_SUMMARY_MARKER}\nold facts"),
            Message.user("recent"), Message.assistant("recent reply")]
    fp = FakeProvider()
    out = compress(msgs, fp, preserve_first=2, tail_tokens=50)
    assert fp.seen_user is None                      # no LLM call needed
    notes = [m for m in out if (m.content or "").startswith(_SUMMARY_MARKER)]
    assert len(notes) == 1 and "old facts" in notes[0].content


# --- deterministic failure fallback -----------------------------------------
def test_summary_failure_keeps_anchors():
    from aegis.agent.compaction import compress
    msgs = [Message.system("s"), Message.user("fix aegis/agent/loop.py please")]
    for i in range(8):
        msgs.append(Message.assistant(
            f"editing src/module_{i}.py and config.yaml with a fair amount of detail here"))
        msgs.append(Message.user(f"step {i} keep going with more words to fill the budget"))
    out = compress(msgs, FakeProvider(reply=None), preserve_first=1, tail_tokens=30)
    note = next(m for m in out if (m.content or "").startswith("[Earlier"))
    assert "deterministic anchors" in note.content
    assert "loop.py" in note.content or "config.yaml" in note.content   # anchors survived


# --- aux model input fitting -------------------------------------------------
def test_summarizer_input_capped_to_aux_window():
    from aegis.agent.compaction import compress

    class Tiny(FakeProvider):
        context_length = 8000          # small aux model -> ~8k tokens

    msgs = [Message.system("s"), Message.user("start")]
    for i in range(200):
        msgs.append(Message.assistant("word " * 200)); msgs.append(Message.user(f"u{i}"))
    fp = Tiny()
    compress(msgs, fp, preserve_first=2, tail_tokens=500)
    # input was capped well under a naive 60k-char dump (8000-6000 reserve)*4 ≈ 8k chars
    assert fp.seen_user is not None and len(fp.seen_user) <= 8000 * 4


def test_unknown_window_uses_fixed_cap():
    from aegis.agent.compaction import _summary_input_budget

    class NoCtx:
        context_length = 0
    assert _summary_input_budget(NoCtx()) == 60_000
