"""Stage S Hermes parity tests for session switch and resume metadata."""

from __future__ import annotations


class _Provider:
    name = "stage-s-provider"
    model = "stage-s-model"
    context_length = 200_000
    api_mode = None
    auth = None

    def describe(self) -> str:
        return "stage-s-provider"


class _MemoryRecorder:
    name = "stage-s-memory"

    def __init__(self) -> None:
        self.initialized_with = ""
        self.switches: list[dict] = []

    def initialize(self, session_id: str = "", **_kw) -> None:
        self.initialized_with = session_id

    def system_prompt_block(self) -> str:
        return ""

    def tools(self) -> list:
        return []

    def on_session_switch(
        self,
        *,
        old_session_id: str,
        new_session_id: str,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        reason: str = "",
        **_kw,
    ) -> None:
        self.switches.append(
            {
                "old_session_id": old_session_id,
                "new_session_id": new_session_id,
                "parent_session_id": parent_session_id,
                "reset": reset,
                "rewound": rewound,
                "reason": reason,
            }
        )


def _runtime_meta() -> dict:
    return {
        "runtime_controls": {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "reasoning_effort": "high",
            "service_tier": "priority",
        },
        "runtime": {
            "reasoning_effort": "high",
            "service_tier": "priority",
            "busy_mode": "queue",
        },
        "model": "claude-3-5-sonnet",
        "provider": "anthropic",
    }


def test_agent_switch_session_notifies_memory_and_aligns_tool_context(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.memory import MemoryManager
    from aegis.session import Session

    config = Config.load()
    memory_provider = _MemoryRecorder()
    memory = MemoryManager(config, external=memory_provider, load_external=False)
    parent = Session.create(title="parent")
    child = Session.create(title="child", parent_id=parent.id)
    agent = Agent(
        config=config,
        provider=_Provider(),
        session=parent,
        memory=memory,
        cwd=tmp_path,
    )

    assert memory_provider.initialized_with == parent.id
    assert agent.tool_context.session is parent
    assert agent.tool_context.task_id == parent.id

    agent.switch_session(child, reason="manual_resume", reset=True)

    assert agent.session is child
    assert agent.tool_context.session is child
    assert agent.tool_context.task_id == child.id
    assert agent._terminal_task_id == child.id
    assert memory._session_id == child.id
    assert memory_provider.switches == [
        {
            "old_session_id": parent.id,
            "new_session_id": child.id,
            "parent_session_id": parent.id,
            "reset": True,
            "rewound": False,
            "reason": "manual_resume",
        }
    ]


def test_sdk_branch_session_preserves_runtime_and_stage_s_branch_markers():
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore

    store = SessionStore()
    parent = Session.create(title="runtime parent")
    parent.meta.update(_runtime_meta())
    store.save(parent)
    client = AegisClient(
        config=Config.load(),
        store=store,
        provider_factory=lambda **_kw: _Provider(),
        include_mcp=False,
    )

    child = client.branch_session(parent.id, title="runtime branch")
    saved_child = store.load(child.id)

    assert saved_child is not None
    assert saved_child.parent_id == parent.id
    assert saved_child.title == "runtime branch"
    for key in ("runtime_controls", "runtime", "model", "provider"):
        assert saved_child.meta[key] == parent.meta[key]
    assert (saved_child.meta.get("_branched_from") or saved_child.meta.get("forked_from")) == parent.id
    assert "branch" in str(saved_child.meta.get("branch_reason") or "")
    assert saved_child.meta.get("creator_kind") == "branch"


def test_sdk_resume_parent_compression_id_returns_tip_with_runtime_metadata():
    from aegis.config import Config
    from aegis.sdk import AegisClient
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    parent = Session.create(title="long claude conversation")
    parent.messages = [Message.user("pre-compression turn")]
    parent.meta.update(_runtime_meta())
    parent.meta["end_reason"] = "compression"
    store.save(parent)

    tip = store.fork(parent)
    tip.title = "long claude conversation #2"
    tip.messages.append(Message.assistant("post-compression reply"))
    tip.meta["creator_kind"] = "compression"
    tip.meta["parent_end_reason"] = "compression"
    tip.meta["reason"] = "context_compaction"
    store.save(tip)
    client = AegisClient(
        config=Config.load(),
        store=store,
        provider_factory=lambda **_kw: _Provider(),
        include_mcp=False,
    )

    assert store.resolve_resume_session_id(parent.id) == tip.id
    resumed = client.resume(parent.id)

    assert resumed.id == tip.id
    for key in ("runtime_controls", "runtime", "model", "provider"):
        assert resumed.meta[key] == parent.meta[key]
    assert resumed.meta["provider"] == "anthropic"
    assert resumed.meta["model"].startswith("claude")


def test_terminal_picker_number_resume_resolves_compression_tip():
    from types import SimpleNamespace

    from aegis.cli import repl
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    parent = Session.create(title="picker parent")
    parent.messages = [Message.user("pre-compression")]
    parent.meta["end_reason"] = "compression"
    store.save(parent)

    tip = store.fork(parent)
    tip.title = "picker parent #2"
    tip.messages.append(Message.assistant("post-compression"))
    tip.meta["creator_kind"] = "compression"
    tip.meta["parent_end_reason"] = "compression"
    store.save(tip)

    agent = SimpleNamespace(_terminal_session_choices=[parent.id])

    resolved = repl._resolve_session_ref(store, agent, "1")

    assert resolved is not None
    assert resolved.id == tip.id
