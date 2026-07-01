from __future__ import annotations

import copy

from aegis.agent import governance
from aegis.agent.response_normalization import (
    extract_inline_reasoning,
    normalize_provider_response,
    sanitize_response_text,
)
from aegis.types import LLMResponse, Message, ToolCall


def _assert_no_tool_then_user(messages: list[Message]) -> None:
    for idx, (before, after) in enumerate(zip(messages, messages[1:])):
        assert not (before.role == "tool" and after.role == "user"), (
            f"role alternation violation: tool -> user at index {idx}"
        )


def test_closed_reasoning_tag_variants_are_stripped_case_insensitively() -> None:
    text = (
        "a <think>one</think>"
        " b <Thinking>two</thinking>"
        " c <REASONING>three</reasoning>"
        " d <thought>four</THOUGHT>"
        " e <reasoning_scratchpad>five</REASONING_SCRATCHPAD> f"
    )

    assert sanitize_response_text(text) == "a  b  c  d  e  f"


def test_unterminated_boundary_open_reasoning_block_is_dropped_to_end() -> None:
    text = "Visible first.\n  <reasoning>private plan\nstill private"

    assert sanitize_response_text(text) == "Visible first."


def test_midline_unterminated_open_tag_is_treated_as_prose_then_orphan_tag_removed() -> None:
    text = "Use the <think> element in examples"

    assert sanitize_response_text(text) == "Use the element in examples"


def test_orphan_reasoning_close_tags_are_removed() -> None:
    assert sanitize_response_text("Visible</think> answer</REASONING>") == "Visibleanswer"


def test_standalone_tool_call_xml_blocks_are_removed() -> None:
    text = (
        "before <tool_call>{\"name\":\"x\"}</tool_call> "
        "middle. <function name=\"lookup\">{\"q\":\"x\"}</function> after"
    )

    assert sanitize_response_text(text) == "before  middle. after"


def test_prose_function_tag_without_name_is_preserved() -> None:
    text = "Use <function> in documentation examples."

    assert sanitize_response_text(text) == text


def test_inline_reasoning_is_extracted_only_when_structured_reasoning_is_absent() -> None:
    response = LLMResponse(text="Hi <think>private</think> there")

    normalized = normalize_provider_response(response)

    assert normalized.text == "Hi  there"
    assert normalized.reasoning == "private"
    assert response.text == "Hi <think>private</think> there"
    assert response.reasoning == ""


def test_existing_structured_reasoning_and_thinking_blocks_are_preserved() -> None:
    blocks = [{"type": "thinking", "thinking": "signed thought", "signature": "sig"}]
    response = LLMResponse(
        text="Visible <think>inline</think>",
        reasoning="structured thought",
        thinking_blocks=blocks,
    )

    normalized = normalize_provider_response(response)

    assert normalized.text == "Visible"
    assert normalized.reasoning == "structured thought"
    assert normalized.thinking_blocks == blocks


def test_surrogates_are_cleaned_from_visible_text_reasoning_and_raw() -> None:
    response = LLMResponse(
        text="visible\ud800",
        reasoning="reason\udfff",
        raw={"nested": ["x\ud800"]},
    )

    normalized = normalize_provider_response(response)

    assert normalized.text == "visible\ufffd"
    assert normalized.reasoning == "reason\ufffd"
    assert normalized.raw == {"nested": ["x\ufffd"]}


def test_malformed_raw_tool_call_arguments_are_repaired_without_mutating_response() -> None:
    response = LLMResponse(
        tool_calls=[
            ToolCall(
                id="call_trailing",
                name="read_file",
                arguments={"__raw__": '{"path": "README.md",}'},
            ),
            ToolCall(
                id="call_truncated",
                name="list_dir",
                arguments={"__raw__": '{"path": "/tmp"'},
            ),
            ToolCall(
                id="call_control",
                name="write_file",
                arguments={"__raw__": '{"content": "hello\tthere",}'},
            ),
            ToolCall(
                id="call_none",
                name="noop",
                arguments={"__raw__": "None"},
            ),
        ]
    )

    normalized = normalize_provider_response(response)

    assert [call.arguments for call in normalized.tool_calls] == [
        {"path": "README.md"},
        {"path": "/tmp"},
        {"content": "hello\tthere"},
        {},
    ]
    assert normalized.tool_calls[0] is not response.tool_calls[0]
    assert response.tool_calls[0].arguments == {"__raw__": '{"path": "README.md",}'}


def test_secret_shapes_are_redacted_at_boundary() -> None:
    response = LLMResponse(
        text="Use OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
        reasoning="saw sk-abcdefghijklmnopqrstuvwxyz123456",
    )

    normalized = normalize_provider_response(response)

    assert normalized.text == "Use OPENAI_API_KEY=[REDACTED]"
    assert normalized.reasoning == "saw [REDACTED]"


def test_extract_inline_reasoning_combines_multiple_variants() -> None:
    text = "<reasoning>first</reasoning>\nvisible\n<think>second</think>"

    assert extract_inline_reasoning(text) == "first\n\nsecond"


def test_governance_closes_missing_tool_result_before_follow_on_user() -> None:
    blocks = [{"type": "thinking", "thinking": "signed private plan", "signature": "sig"}]
    response = LLMResponse(
        reasoning="structured provider reasoning",
        thinking_blocks=copy.deepcopy(blocks),
        tool_calls=[ToolCall("call_missing", "probe", {"path": "README.md"})],
    )
    original_response = copy.deepcopy(response)

    assistant = normalize_provider_response(response).to_message()
    repaired = governance.normalize([
        Message.user("start"),
        assistant,
        Message.user("new instruction"),
    ])

    assert [message.role for message in repaired] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert repaired[1].reasoning == "structured provider reasoning"
    assert repaired[1].thinking_blocks == blocks
    assert repaired[2].tool_call_id == "call_missing"
    assert repaired[2].content == "[no result: interrupted]"
    assert repaired[3].content == "Operation interrupted."
    _assert_no_tool_then_user(repaired)
    assert response == original_response


def test_governance_closes_real_tool_tail_before_follow_on_user_once() -> None:
    repaired = governance.normalize([
        Message.user("start"),
        Message.assistant(
            "",
            [ToolCall("call_done", "probe", {})],
        ),
        Message.tool("call_done", "probe", "tool finished before interruption"),
        Message.user("new instruction"),
    ])

    assert [message.role for message in repaired] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert repaired[2].content == "tool finished before interruption"
    assert repaired[3].content == "Operation interrupted."
    assert sum(1 for message in repaired if message.role == "assistant") == 2
    _assert_no_tool_then_user(repaired)


def test_governance_leaves_active_tool_tail_open_without_follow_on_user() -> None:
    repaired = governance.normalize([
        Message.user("start"),
        Message.assistant(
            "",
            [ToolCall("call_active", "probe", {})],
        ),
        Message.tool("call_active", "probe", "tool result for the next model call"),
    ])

    assert [message.role for message in repaired] == ["user", "assistant", "tool"]
    assert repaired[-1].content == "tool result for the next model call"
