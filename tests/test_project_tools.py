"""Hermes-compatible first-class project tools."""

from __future__ import annotations

import json
from types import SimpleNamespace


def test_project_tools_are_registered_in_project_toolset(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.tools.registry import default_registry

    tools = {tool.name: tool for tool in default_registry(include_plugins=False).all()}

    for name in ("project_list", "project_create", "project_switch"):
        assert name in tools
        assert tools[name].toolset == "project"
        assert tools[name].available()[0] is True


def test_project_tools_create_list_switch_and_reanchor_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.tools.base import ToolContext
    from aegis.tools.registry import default_registry

    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    cfg = Config.load()
    agent = SimpleNamespace(cwd=tmp_path, config=cfg, refresh_volatile=lambda: None)
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent, task_id="task_projects")
    tools = {tool.name: tool for tool in default_registry(include_plugins=False).all()}

    created = json.loads(tools["project_create"].run({"name": "Demo Project", "path": str(repo)}, ctx).content)
    assert created["success"] is True
    assert created["name"] == "Demo Project"
    assert created["slug"] == "demo-project"
    assert created["primary_path"] == str(repo.resolve())
    assert agent.cwd == repo.resolve()
    assert ctx.cwd == repo.resolve()

    listing = json.loads(tools["project_list"].run({}, ctx).content)
    assert listing["active_id"] == created["id"]
    assert [(p["slug"], p["active"]) for p in listing["projects"]] == [("demo-project", True)]

    second = json.loads(tools["project_create"].run({"name": "Other", "path": str(other)}, ctx).content)
    switched = json.loads(tools["project_switch"].run({"project": "demo-project"}, ctx).content)
    assert second["id"] != created["id"]
    assert switched["success"] is True
    assert switched["id"] == created["id"]
    assert agent.cwd == repo.resolve()
    assert ctx.cwd == repo.resolve()

    assert (tmp_path / "home" / "projects.db").exists()
