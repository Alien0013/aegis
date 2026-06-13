"""Hermes-compatible cronjob tool surface."""

from __future__ import annotations

import json


def _ctx(tmp_path, *, agent=None):
    from aegis.config import Config
    from aegis.tools.base import ToolContext

    return ToolContext(cwd=tmp_path, config=Config.load(), agent=agent)


def _data(result):
    assert not result.is_error, result.content
    return json.loads(result.content)


def test_cronjob_create_list_update_delete(tmp_path):
    from aegis.cron import CronStore
    from aegis.tools.cronjob_tool import CronJobTool

    agent = type("Agent", (), {"platform": "telegram", "chat_id": "42"})()
    tool = CronJobTool()
    ctx = _ctx(tmp_path, agent=agent)

    created = _data(tool.run({
        "action": "create",
        "schedule": "30m",
        "prompt": "check server status",
        "name": "Server check",
    }, ctx))

    assert created["success"] is True
    assert created["name"] == "Server check"
    job_id = created["job_id"]
    stored = CronStore().get(job_id)
    assert stored is not None
    assert stored.name == "Server check"
    assert stored.deliver == "telegram:42"

    listing = _data(tool.run({"action": "list"}, ctx))
    assert listing["count"] == 1
    assert listing["jobs"][0]["job_id"] == job_id

    updated = _data(tool.run({
        "action": "update",
        "job_id": job_id,
        "schedule": "1h",
        "prompt": "check API status",
        "skills": ["ops", "ops", "logs"],
        "deliver": "local",
        "enabled": False,
    }, ctx))

    assert updated["job"]["schedule"] == "1h"
    assert updated["job"]["skills"] == ["ops", "logs"]
    assert updated["job"]["deliver"] == "local"
    assert updated["job"]["state"] == "paused"

    deleted = _data(tool.run({"action": "delete", "job_id": job_id}, ctx))
    assert deleted["success"] is True
    assert CronStore().list() == []


def test_cronjob_pause_resume_and_status(tmp_path, monkeypatch):
    from aegis import daemon
    from aegis.tools.cronjob_tool import CronJobTool

    monkeypatch.setattr(daemon, "cron_service_status", lambda: "active")
    tool = CronJobTool()
    ctx = _ctx(tmp_path)
    job_id = _data(tool.run({
        "action": "create",
        "schedule": "30m",
        "prompt": "check",
    }, ctx))["job_id"]

    paused = _data(tool.run({"action": "pause", "job_id": job_id}, ctx))
    assert paused["job"]["state"] == "paused"

    job_status = _data(tool.run({"action": "status", "job_id": job_id}, ctx))
    assert job_status["job"]["enabled"] is False

    resumed = _data(tool.run({"action": "resume", "job_id": job_id}, ctx))
    assert resumed["job"]["state"] == "scheduled"

    status = _data(tool.run({"action": "status"}, ctx))
    assert status["service"]["status"] == "active"
    assert status["jobs"]["total"] == 1
    assert status["jobs"]["enabled"] == 1


def test_cronjob_run_delegates_to_scheduler(tmp_path, monkeypatch):
    from aegis.tools import cronjob_tool as mod
    from aegis.tools.cronjob_tool import CronJobTool

    seen = {}

    def fake_run_job(config, job, **kwargs):
        seen["job_id"] = job.id
        seen["store"] = kwargs.get("store")
        seen["sink"] = kwargs.get("sink")
        return {"ok": True, "job_id": job.id, "reply": "ran"}

    monkeypatch.setattr(mod, "run_job", fake_run_job)

    tool = CronJobTool()
    ctx = _ctx(tmp_path)
    job_id = _data(tool.run({
        "action": "create",
        "schedule": "30m",
        "prompt": "check",
    }, ctx))["job_id"]

    result = _data(tool.run({"action": "run", "job_id": job_id}, ctx))

    assert result["success"] is True
    assert result["result"]["reply"] == "ran"
    assert seen["job_id"] == job_id
    assert seen["sink"] is None


def test_cronjob_service_action_uses_daemon_helpers(tmp_path, monkeypatch):
    from aegis import daemon
    from aegis.daemon import ServiceResult
    from aegis.tools.cronjob_tool import CronJobTool

    calls = []
    monkeypatch.setattr(daemon, "cron_service_status", lambda: "inactive")
    monkeypatch.setattr(
        daemon,
        "install_cron_service",
        lambda config, *, enable_now=True: calls.append(("install", enable_now))
        or ServiceResult(True, "installed"),
    )
    monkeypatch.setattr(
        daemon,
        "control_cron_service",
        lambda action: calls.append(("control", action)) or ServiceResult(True, action),
    )
    monkeypatch.setattr(
        daemon,
        "remove_cron_service",
        lambda: calls.append(("remove", None)) or ServiceResult(True, "removed"),
    )

    tool = CronJobTool()
    ctx = _ctx(tmp_path)

    status = _data(tool.run({"action": "service", "service_action": "status"}, ctx))
    assert status["status"] == "inactive"

    installed = _data(tool.run({
        "action": "service",
        "service_action": "install",
        "enable_now": False,
    }, ctx))
    assert installed["message"] == "installed"

    started = _data(tool.run({"action": "service", "service_action": "start"}, ctx))
    assert started["message"] == "start"

    removed = _data(tool.run({"action": "service", "service_action": "uninstall"}, ctx))
    assert removed["message"] == "removed"
    assert calls == [("install", False), ("control", "start"), ("remove", None)]


def test_registry_exposes_cronjob_without_replacing_schedule_task():
    from aegis.tools.registry import default_registry

    names = {tool.name for tool in default_registry().all()}
    assert "cronjob" in names
    assert "schedule_task" in names
