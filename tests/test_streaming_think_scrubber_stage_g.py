from __future__ import annotations

import pytest

from aegis.agent.streaming_think_scrubber import StreamingThinkScrubber


def _drive(deltas: list[str]) -> str:
    scrubber = StreamingThinkScrubber()
    out = [scrubber.feed(delta) for delta in deltas]
    out.append(scrubber.flush())
    return "".join(out)


@pytest.mark.parametrize(
    ("tag", "close"),
    [
        ("think", "think"),
        ("thinking", "thinking"),
        ("reasoning", "reasoning"),
        ("thought", "thought"),
        ("REASONING_SCRATCHPAD", "REASONING_SCRATCHPAD"),
    ],
)
def test_closed_pairs_are_stripped_even_mid_line(tag: str, close: str) -> None:
    assert _drive([f"Hello <{tag}>private</{close}> world"]) == "Hello  world"


def test_closed_pairs_are_case_insensitive() -> None:
    assert _drive(["before <THINK>private</Think> after"]) == "before  after"


def test_boundary_open_suppresses_until_close_across_deltas() -> None:
    assert _drive(["Hello\n  <think>", "private", "</think>", "done"]) == "Hello\n  done"


def test_boundary_open_suppresses_until_stream_end() -> None:
    assert _drive(["<reasoning>", "private with no close"]) == ""


def test_mid_line_open_without_close_is_preserved_as_prose() -> None:
    text = "Use the <think> element in examples"
    assert _drive([text]) == text


def test_orphan_close_tags_are_stripped() -> None:
    assert _drive(["Hello</think> world</thinking>again"]) == "Helloworldagain"


def test_partial_open_tag_tail_is_held_until_resolved() -> None:
    assert _drive(["<", "think>private</think>visible"]) == "visible"


def test_partial_close_tag_tail_is_held_until_resolved() -> None:
    assert _drive(["<think>private</th", "ink>visible"]) == "visible"


def test_partial_tag_tail_flushes_when_not_a_tag() -> None:
    scrubber = StreamingThinkScrubber()
    assert scrubber.feed("visible<") == "visible"
    assert scrubber.flush() == "<"


def test_reset_clears_block_and_partial_tail_state() -> None:
    scrubber = StreamingThinkScrubber()
    assert scrubber.feed("<think>private") == ""
    assert scrubber.feed("</th") == ""

    scrubber.reset()

    assert scrubber.feed("visible") == "visible"
    assert scrubber.flush() == ""
