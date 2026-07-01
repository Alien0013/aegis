from __future__ import annotations

from aegis.agent import governance
from aegis.types import Message, ToolCall


def test_tool_call_ids_are_repaired_and_matching_results_preserved():
    msgs = [
        Message.user("run the tools"),
        Message.assistant(
            "",
            [
                ToolCall("", "", "raw"),
                ToolCall("dup", "read_file", ["README.md"]),
                ToolCall("dup", "bash", None),
            ],
        ),
        Message.tool("", "", "missing id result"),
        Message.tool("dup", "read_file", "read result"),
        Message.tool("dup", "bash", "bash result"),
    ]

    out = governance.normalize(msgs)
    assistant = out[1]
    calls = assistant.tool_calls
    ids = [tc.id for tc in calls]

    assert all(ids)
    assert len(set(ids)) == len(ids)
    assert [tc.name for tc in calls] == ["unknown_tool", "read_file", "bash"]
    assert [tc.arguments for tc in calls] == [
        {"value": "raw"},
        {"value": ["README.md"]},
        {},
    ]

    results = out[2:5]
    assert [(m.role, m.tool_call_id, m.name, m.content) for m in results] == [
        ("tool", ids[0], "unknown_tool", "missing id result"),
        ("tool", ids[1], "read_file", "read result"),
        ("tool", ids[2], "bash", "bash result"),
    ]


def test_invalid_roles_drop_adjacent_users_merge_and_thinking_only_stays():
    thinking_only = Message(
        role="assistant",
        content="",
        reasoning="private reasoning",
        thinking_blocks=[{"type": "thinking", "thinking": "private reasoning", "signature": "sig"}],
    )
    msgs = [
        Message.user("first"),
        Message(role="developer", content="invalid"),
        Message.user("second"),
        thinking_only,
        Message.user("third"),
        Message.user("fourth"),
    ]

    out = governance.normalize(msgs)

    assert [m.role for m in out] == ["user", "assistant", "user"]
    assert out[0].content == "first\n\nsecond"
    assert out[1] is thinking_only
    assert out[1].reasoning == "private reasoning"
    assert out[1].thinking_blocks == [
        {"type": "thinking", "thinking": "private reasoning", "signature": "sig"}
    ]
    assert out[2].content == "third\n\nfourth"


def test_old_tool_result_after_user_boundary_is_orphaned():
    msgs = [
        Message.user("start"),
        Message.assistant("", [ToolCall("c1", "read_file", {"path": "a"})]),
        Message.user("interrupt with a new task"),
        Message.tool("c1", "read_file", "late stale result"),
    ]

    out = governance.normalize(msgs)

    assert [(m.role, m.tool_call_id, m.content) for m in out] == [
        ("user", None, "start"),
        ("assistant", None, ""),
        ("tool", "c1", "[no result: interrupted]"),
        ("user", None, "interrupt with a new task"),
    ]
