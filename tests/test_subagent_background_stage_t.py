"""Stage T: Hermes-parity contract tests for background subagents."""

from __future__ import annotations

import copy
import queue
import threading

import pytest

from aegis.background import BgTask, BackgroundManager
from aegis.config import Config, DEFAULT_CONFIG
from aegis.session import Session
from aegis.surface import SurfaceRun
from aegis.tools.agentic import SubagentTool
from aegis.tools.base import ToolContext
from aegis.types import Message, Usage


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "aegis-home"
    monkeypatch.setenv("AEGIS_HOME", str(home))
    return home


def _config() -> Config:
    data = copy.deepcopy(DEFAULT_CONFIG)
    data["agent"]["max_spawn_depth"] = 3
    data["tools"]["toolsets"] = ["core", "browser"]
    return Config(data)


def test_top_level_background_default_spawn_many_receives_rich_session_meta(
    tmp_path, monkeypatch, isolated_home
):
    class RecordingManager:
        def spawn_many(self, config, prompts, **kwargs):
            self.config = config
            self.prompts = list(prompts)
            self.kwargs = kwargs
            return ["bg_stage_t_1", "bg_stage_t_2"]

    parent_session = Session.create("parent")
    parent_session.meta["runtime_controls"] = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "reasoning_effort": "high",
    }

    class Parent:
        session = parent_session
        platform = "telegram"
        chat_id = "chat-1"
        user_id = "user-1"
        user_name = "Ada"
        thread_id = "thread-1"
        message_id = "message-1"
        memory = None
        tool_context = object()

    manager = RecordingManager()
    monkeypatch.setattr("aegis.background.get_manager", lambda: manager)

    result = SubagentTool().run(
        {
            "tasks": ["review api surface", "review dashboard surface"],
            "agent_type": "review",
            "role": "orchestrator",
            "toolsets": ["browser", "lsp"],
        },
        ToolContext(cwd=tmp_path, config=_config(), agent=Parent()),
    )

    assert not result.is_error
    assert manager.prompts == ["review api surface", "review dashboard surface"]
    assert manager.kwargs["parent_session"] is parent_session
    assert manager.config.get("tools.toolsets") == ["browser"]

    meta = manager.kwargs["session_meta"]
    assert meta["parent_session_id"] == parent_session.id
    assert meta["agent_type"] == "review"
    assert meta["role"] == "orchestrator"
    assert meta["provider"] == "anthropic"
    assert meta["model"] == "claude-sonnet-4-20250514"
    assert meta["runtime_controls"]["provider"] == "anthropic"
    assert meta["runtime_controls"]["model"] == "claude-sonnet-4-20250514"
    assert meta["depth"] == 1
    assert meta["max_depth"] == 3
    assert meta["toolsets"] == ["browser"]


def test_background_manager_completion_event_carries_delegation_metadata(
    isolated_home, monkeypatch
):
    manager = BackgroundManager()
    queued = []
    monkeypatch.setattr(manager, "_queue_async_delegation_event", lambda task: queued.append(task.id))

    task = BgTask(
        id="bg_stage_t_done",
        prompt="review the queued patch",
        status="done",
        result="looks good",
        parent_session_id="sess_parent_stage_t",
        agent_type="review",
        role="orchestrator",
        model="claude-sonnet-4-20250514",
        created_at=90.0,
        started_at=100.0,
        finished_at=112.0,
        observability={
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_read_tokens": 6,
            "cache_write_tokens": 7,
            "prompt_tokens": 136,
            "total_tokens": 181,
            "usage": {
                "input_tokens": 123,
                "output_tokens": 45,
                "cache_read_tokens": 6,
                "cache_write_tokens": 7,
                "prompt_tokens": 136,
                "total_tokens": 181,
            },
            "cost_usd": 0.012345,
            "cost_status": "estimated",
            "cost_source": "official_docs_snapshot",
        },
    )

    manager._record_completion(task, _config())

    assert queued == ["bg_stage_t_done"]
    event = manager.completions()[-1]
    assert event["id"] == "bg_stage_t_done"
    assert event["delegation_id"] == "bg_stage_t_done"
    assert event["goal"] == "review the queued patch"
    assert event["status"] in {"done", "completed"}
    assert event["agent_type"] == "review"
    assert event["role"] == "orchestrator"
    assert event["model"] == "claude-sonnet-4-20250514"
    assert event["parent_session_id"] == "sess_parent_stage_t"
    assert event["session_key"] == "sess_parent_stage_t"
    assert event["duration_seconds"] == 12.0
    assert event["started_at"] == 100.0
    assert event["finished_at"] == 112.0
    assert event["usage"]["total_tokens"] == 181
    assert event["input_tokens"] == 123
    assert event["cost_usd"] == 0.012345
    assert event["cost_status"] == "estimated"


def test_background_manager_async_completion_event_carries_usage_and_cost(
    isolated_home, monkeypatch
):
    manager = BackgroundManager()
    task = BgTask(
        id="bg_stage_t_async_done",
        prompt="review the queued patch",
        status="done",
        result="looks good",
        parent_session_id="sess_parent_stage_t",
        agent_type="review",
        role="orchestrator",
        model="claude-sonnet-4-20250514",
        created_at=90.0,
        started_at=100.0,
        finished_at=112.0,
        observability={
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_read_tokens": 6,
            "cache_write_tokens": 7,
            "prompt_tokens": 136,
            "total_tokens": 181,
            "usage": {"total_tokens": 181},
            "cost_usd": 0.012345,
            "cost_status": "estimated",
            "cost_source": "official_docs_snapshot",
        },
    )
    from aegis.tools.process_registry import process_registry

    completions = queue.Queue()
    monkeypatch.setattr(process_registry, "completion_queue", completions)

    manager._queue_async_delegation_event(task)

    event = completions.get_nowait()
    assert event["type"] == "async_delegation"
    assert event["delegation_id"] == "bg_stage_t_async_done"
    assert event["status"] == "completed"
    assert event["usage"]["total_tokens"] == 181
    assert event["input_tokens"] == 123
    assert event["cost_usd"] == 0.012345
    assert event["cost_source"] == "official_docs_snapshot"


def test_background_run_persists_child_usage_and_cost(
    tmp_path, isolated_home, monkeypatch
):
    class Store:
        def save(self, _session):
            return None

    class FakeAgent:
        def __init__(self):
            self.provider = type(
                "Provider",
                (),
                {"name": "anthropic", "model": "claude-sonnet-4-6"},
            )()
            self._last_turn_cost = {
                "amount_usd": 0.0042,
                "cost_status": "estimated",
                "cost_source": "official_docs_snapshot",
                "pricing_source": "official_docs_snapshot",
                "cost_label": "~$0.0042",
            }
            self.cancel_event = threading.Event()

        def cancel(self):
            self.cancel_event.set()

    class FakeSurfaceRunner:
        def __init__(self, *_args, **_kwargs):
            self.store = Store()
            self.agent = FakeAgent()

        def load_or_create_session(self, session_id, title, surface, meta):
            session = Session.create(title)
            session.id = session_id
            session.meta.update(meta)
            session.meta["provider"] = "anthropic"
            session.meta["model"] = "claude-sonnet-4-6"
            session.meta["surface"] = surface
            return session

        def make_agent(self, **_kwargs):
            return self.agent

        def run_prompt(self, _prompt, *, session, agent, **_kwargs):
            return SurfaceRun(
                text="background ok",
                message=Message.assistant("background ok"),
                session=session,
                agent=agent,
                usage=Usage(123, 45, cache_read=6, cache_write=7),
                run_id="run_stage_t_background_usage",
            )

        def close(self):
            return None

    monkeypatch.setattr("aegis.surface.SurfaceRunner", FakeSurfaceRunner)
    manager = BackgroundManager()
    parent_session = Session.create("parent")
    done = threading.Event()
    seen: dict[str, BgTask] = {}

    def on_done(task: BgTask):
        seen["task"] = task
        done.set()

    task_id = manager.spawn(
        _config(),
        "measure background child usage",
        cwd=tmp_path,
        on_done=on_done,
        parent_session=parent_session,
        session_meta={
            "agent_type": "review",
            "role": "leaf",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        },
    )

    assert done.wait(2)
    task = seen["task"]
    assert task.id == task_id
    assert task.observability["input_tokens"] == 123
    assert task.observability["usage"]["total_tokens"] == 181
    assert task.observability["cost_usd"] == 0.0042

    row = next(item for item in manager.list() if item["id"] == task_id)
    assert row["usage"]["total_tokens"] == 181
    assert row["cost_usd"] == 0.0042

    event = next(item for item in manager.completions() if item["id"] == task_id)
    assert event["input_tokens"] == 123
    assert event["cost_status"] == "estimated"

    if manager._executor is not None:
        manager._executor.shutdown(wait=True, cancel_futures=False)


def test_background_manager_list_and_cancel_expose_active_subagent_state(isolated_home):
    manager = BackgroundManager()
    task = BgTask(
        id="bg_stage_t_active",
        prompt="keep reviewing until cancelled",
        status="running",
        parent_session_id="sess_parent_stage_t",
        agent_type="review",
        role="orchestrator",
        model="claude-sonnet-4-20250514",
        created_at=10.0,
        started_at=11.0,
    )

    class ActiveAgent:
        def __init__(self):
            self.cancel_event = threading.Event()
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    active_agent = ActiveAgent()
    with manager._lock:
        manager._tasks[task.id] = task
        manager._active_agents[task.id] = active_agent

    row = next(item for item in manager.list() if item["id"] == task.id)
    assert row["status"] == "running"
    assert row["agent_type"] == "review"
    assert row["role"] == "orchestrator"
    assert row["model"] == "claude-sonnet-4-20250514"
    assert row["parent_session_id"] == "sess_parent_stage_t"
    assert row["session_key"] == "sess_parent_stage_t"
    assert row["delegation_id"] == "bg_stage_t_active"
    assert row["interruptible"] is True

    cancelled = manager.cancel("bg_stage_t")

    assert cancelled == {
        "ok": True,
        "id": "bg_stage_t_active",
        "status": "cancelling",
        "cancel_requested": True,
    }
    assert active_agent.cancelled is True
    assert active_agent.cancel_event.is_set()
    row = next(item for item in manager.list() if item["id"] == task.id)
    assert row["status"] == "cancelling"
    assert row["cancel_requested"] is True
