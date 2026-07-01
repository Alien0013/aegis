"""Hermes agent_init context-engine parity coverage."""

from __future__ import annotations

import os

import pytest


class FakeProvider:
    name = "fake"
    model = "fake-model"
    context_length = 131_072
    api_mode = None
    auth = None

    def complete(self, *_args, **_kwargs):  # pragma: no cover - Agent init never calls it.
        raise AssertionError("context-engine parity tests must not call the provider")


def _config(*, context_engine: str = "default"):
    from aegis.config import Config

    cfg = Config.load()
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("skills", {})["include_bundled"] = False
    cfg.data.setdefault("agent", {})["context_engine"] = context_engine
    return cfg


def _is_default_context_engine(engine) -> bool:
    from aegis.agent.context_engine import DefaultContextEngine

    return isinstance(engine, DefaultContextEngine) and engine.name == "default"


@pytest.mark.parametrize("legacy_engine_name", ["default", "compressor"])
def test_context_engine_config_alias_selects_registered_engine(
    monkeypatch, legacy_engine_name
):
    from aegis.agent import context_engine as ce

    monkeypatch.setattr(ce, "_ENGINES", dict(ce._ENGINES))

    class AliasSelectedEngine:
        name = "alias-selected"

        def tools(self):
            return []

    ce.register("alias-selected", AliasSelectedEngine)
    cfg = _config(context_engine=legacy_engine_name)
    cfg.data["context"] = {"engine": "alias-selected"}

    assert ce.get_engine(cfg).name == "alias-selected"


@pytest.mark.parametrize(
    ("agent_context_engine", "context_engine"),
    [
        ("compressor", None),
        ("default", "compressor"),
    ],
)
def test_compressor_alias_resolves_to_aegis_default_engine(
    agent_context_engine, context_engine
):
    from aegis.agent import context_engine as ce

    cfg = _config(context_engine=agent_context_engine)
    if context_engine is not None:
        cfg.data["context"] = {"engine": context_engine}

    assert _is_default_context_engine(ce.get_engine(cfg))


def test_registered_engine_instances_are_deep_copied_per_get_engine_and_agent(
    monkeypatch, tmp_path
):
    from aegis.agent import context_engine as ce
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.tools.registry import ToolRegistry

    monkeypatch.setattr(ce, "_ENGINES", dict(ce._ENGINES))

    class StatefulEngine:
        name = "stateful"

        def __init__(self):
            self.mutable_state = []

        def __deepcopy__(self, memo):
            clone = type(self)()
            clone.mutable_state = list(self.mutable_state)
            return clone

        def tools(self):
            return []

    shared_engine = StatefulEngine()
    ce.register("stateful", shared_engine)
    cfg = _config(context_engine="stateful")

    first = ce.get_engine(cfg)
    second = ce.get_engine(cfg)
    first.mutable_state.append("first")

    assert first is not shared_engine
    assert second is not shared_engine
    assert second.mutable_state == []
    assert shared_engine.mutable_state == []

    agent_one = Agent(
        config=cfg,
        provider=FakeProvider(),
        session=Session.create("one"),
        registry=ToolRegistry(),
        cwd=tmp_path,
    )
    agent_two = Agent(
        config=cfg,
        provider=FakeProvider(),
        session=Session.create("two"),
        registry=ToolRegistry(),
        cwd=tmp_path,
    )
    agent_one._context_engine.mutable_state.append("agent-one")

    assert agent_one._context_engine is not agent_two._context_engine
    assert agent_two._context_engine.mutable_state == []
    assert shared_engine.mutable_state == []


def test_context_engine_tool_registration_skips_duplicates_and_tracks_new_names(
    monkeypatch, tmp_path
):
    from aegis.agent import context_engine as ce
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry

    monkeypatch.setattr(ce, "_ENGINES", dict(ce._ENGINES))

    class FakeTool(Tool):
        def __init__(self, name: str):
            self.name = name
            self.description = f"{name} test tool"
            self.parameters = {"type": "object", "properties": {}}
            self.toolset = "context_engine"
            self.source = "test"

        def run(self, _args, _ctx):
            return ToolResult.ok("ok")

    class ToolEngine:
        name = "tool-engine"

        def tools(self):
            return [
                FakeTool("context_lookup"),
                FakeTool("context_expand"),
            ]

    ce.register("tool-engine", ToolEngine)
    cfg = _config(context_engine="tool-engine")
    registry = ToolRegistry()
    existing = FakeTool("context_lookup")
    existing.description = "pre-existing context lookup"
    registry.register(existing)

    agent = Agent(
        config=cfg,
        provider=FakeProvider(),
        session=Session.create("tool registration"),
        registry=registry,
        cwd=tmp_path,
    )

    assert registry.get("context_lookup") is existing
    assert registry.get("context_expand") is not None
    assert registry.rejections() == []
    assert agent._context_engine_tool_names == {"context_expand"}


def test_on_session_start_receives_richer_metadata_when_hook_accepts_kwargs(
    monkeypatch, tmp_path, isolated_home
):
    from aegis.agent import context_engine as ce
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.tools.registry import ToolRegistry

    monkeypatch.setattr(ce, "_ENGINES", dict(ce._ENGINES))
    seen = []

    class MetadataEngine:
        name = "metadata-engine"

        def tools(self):
            return []

        def on_session_start(self, agent, **metadata):
            seen.append((agent.session.id, metadata))

    ce.register("metadata-engine", MetadataEngine)
    cfg = _config(context_engine="metadata-engine")
    session = Session.create("metadata")

    Agent(
        config=cfg,
        provider=FakeProvider(),
        session=session,
        registry=ToolRegistry(),
        cwd=tmp_path,
    )

    assert seen
    session_id, metadata = seen[0]
    assert session_id == session.id
    assert metadata["session_id"] == session.id
    assert metadata["aegis_home"] == isolated_home == os.environ["AEGIS_HOME"]
    assert metadata["platform"] == "cli"
    assert metadata["model"] == FakeProvider.model
    assert metadata["context_length"] == FakeProvider.context_length
    assert "conversation_id" in metadata
