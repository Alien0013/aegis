from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any


def _write_approval():
    return importlib.import_module("aegis.write_approval")


def _config():
    from aegis.config import Config

    return Config.load()


def _set_gate(config: Any, subsystem: str, enabled: bool) -> None:
    config.data.setdefault(subsystem, {})["write_approval"] = enabled


def _pending_dir(subsystem: str) -> Path:
    return Path(os.environ["AEGIS_HOME"]) / "pending" / subsystem


def _skill_content(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n"


def _seed_skill(name: str, body: str) -> Path:
    from aegis import config as cfg

    skill_dir = cfg.skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _skill_content(name, "Use for gated write approval tests.", body),
        encoding="utf-8",
    )
    return skill_dir


def _assert_staged_skill_write(result: Any, wa: Any, config: Any, action: str, name: str) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert isinstance(result.data, dict)
    assert result.data["staged"] is True
    pending_id = result.data["pending_id"]
    assert pending_id

    record = wa.get_pending(wa.SKILLS, pending_id, config=config)
    assert record is not None
    assert record["subsystem"] == wa.SKILLS
    assert record["payload"]["action"] == action
    assert record["payload"]["name"] == name
    return record


def test_pending_store_stages_json_records_under_aegis_home():
    wa = _write_approval()
    config = _config()
    payload = {
        "action": "add",
        "target": "memory",
        "content": "The user prefers staged durable writes.",
    }

    record = wa.stage_write(
        wa.MEMORY,
        payload,
        "Remember the staged write preference.",
        origin="background_review",
        config=config,
    )

    pending_id = record["id"]
    path = _pending_dir(wa.MEMORY) / f"{pending_id}.json"
    assert path.is_file()

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["id"] == pending_id
    assert on_disk["subsystem"] == wa.MEMORY
    assert on_disk["action"] == "add"
    assert on_disk["payload"] == payload
    assert on_disk["summary"] == "Remember the staged write preference."
    assert on_disk["origin"] == "background_review"

    assert wa.pending_count(wa.MEMORY, config=config) == 1
    listed = wa.list_pending(wa.MEMORY, config=config)
    assert [item["id"] for item in listed] == [pending_id]

    fetched = wa.get_pending(wa.MEMORY, pending_id, config=config)
    assert fetched["payload"] == payload
    assert fetched["summary"] == record["summary"]
    assert fetched["origin"] == record["origin"]

    assert wa.discard_pending(wa.MEMORY, pending_id, config=config) is True
    assert wa.pending_count(wa.MEMORY, config=config) == 0
    assert wa.list_pending(wa.MEMORY, config=config) == []
    assert wa.get_pending(wa.MEMORY, pending_id, config=config) is None


def test_evaluate_gate_decisions_follow_config_and_memory_approver():
    wa = _write_approval()
    config = _config()

    assert wa.write_approval_enabled(wa.MEMORY, config=config) is False
    assert wa.write_approval_enabled(wa.SKILLS, config=config) is False
    allowed = wa.evaluate_gate(
        wa.MEMORY,
        inline_summary="remember parser preference",
        inline_detail="The user prefers parser-backed changes.",
        config=config,
    )
    assert allowed.action == "allow"
    assert allowed.allow is True
    assert allowed.allowed is True
    assert allowed.stage is False
    assert allowed.staged is False
    assert allowed.message == "write approval is disabled"

    _set_gate(config, wa.SKILLS, True)
    staged_skill = wa.evaluate_gate(
        wa.SKILLS,
        inline_summary="create a large skill",
        inline_detail="full skill body",
        config=config,
        origin="foreground",
    )
    assert staged_skill.action == "stage"
    assert staged_skill.stage is True
    assert staged_skill.staged is True

    _set_gate(config, wa.MEMORY, True)
    staged_memory = wa.evaluate_gate(
        wa.MEMORY,
        inline_summary="remember parser preference",
        inline_detail="The user prefers parser-backed changes.",
        config=config,
        origin="foreground",
    )
    assert staged_memory.action == "stage"

    approved_memory = wa.evaluate_gate(
        wa.MEMORY,
        inline_summary="remember parser preference",
        inline_detail="The user prefers parser-backed changes.",
        config=config,
        interactive_approver=lambda *_args, **_kwargs: True,
        origin="foreground",
    )
    assert approved_memory.action == "allow"

    blocked_memory = wa.evaluate_gate(
        wa.MEMORY,
        inline_summary="remember parser preference",
        inline_detail="The user prefers parser-backed changes.",
        config=config,
        interactive_approver=lambda *_args, **_kwargs: False,
        origin="foreground",
    )
    assert blocked_memory.action == "blocked"
    assert blocked_memory.blocked is True


def test_write_gate_normalizes_provenance_origin_like_hermes_background_review():
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.MEMORY, True)

    from aegis import provenance

    assert wa.current_origin() == "foreground"
    assert wa.is_background() is False

    with provenance.origin_scope("agent"):
        assert wa.current_origin() == "background_review"
        assert wa.is_background() is True
        staged = wa.evaluate_gate(
            wa.MEMORY,
            inline_summary="remember background fact",
            config=config,
            interactive_approver=lambda *_args, **_kwargs: True,
        )

    assert staged.action == "stage"
    assert "background_review" in staged.message


def test_skill_gist_accepts_payload_and_hermes_style_signature():
    wa = _write_approval()
    content = _skill_content(
        "gist-skill",
        "Use when summarizing pending skill writes.",
        "## Steps\n1. Summarize the pending change.",
    )

    payload_gist = wa.skill_gist({
        "action": "create",
        "name": "gist-skill",
        "content": content,
    })
    assert payload_gist.startswith("create 'gist-skill'")
    assert "Use when summarizing pending skill writes." in payload_gist

    signature_gist = wa.skill_gist(
        "patch",
        "gist-skill",
        file_path="SKILL.md",
        old_string="one\ntwo",
        new_string="one\ntwo\nthree",
    )
    assert signature_gist == "patch 'gist-skill' SKILL.md (+3/-2 lines)"


def test_memory_manager_stages_add_without_mutating_memory_file():
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.MEMORY, True)

    from aegis.memory import MemoryManager

    manager = MemoryManager(config)
    memory_path = Path(os.environ["AEGIS_HOME"]) / "memories" / "MEMORY.md"
    before = memory_path.read_text(encoding="utf-8")

    result = manager.handle_tool({
        "action": "add",
        "target": "memory",
        "content": "Stage this memory instead of saving it.",
    })

    assert not result.is_error, result.content
    assert isinstance(result.data, dict)
    assert result.data["staged"] is True
    pending_id = result.data["pending_id"]
    assert pending_id
    assert memory_path.read_text(encoding="utf-8") == before

    record = wa.get_pending(wa.MEMORY, pending_id, config=config)
    assert record is not None
    assert record["subsystem"] == wa.MEMORY
    assert record["payload"]["action"] == "add"
    assert record["payload"]["target"] == "memory"
    assert record["payload"]["content"] == "Stage this memory instead of saving it."


def test_skill_manage_stages_write_actions_without_mutating_skill_files(tmp_path):
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.SKILLS, True)

    from aegis import config as cfg
    from aegis.skills import SkillsLoader
    from aegis.tools.base import ToolContext
    from aegis.tools.skill_manage import SkillManageTool

    loader = SkillsLoader(config, cwd=tmp_path)
    ctx = ToolContext(cwd=tmp_path, config=config, skills=loader)
    tool = SkillManageTool()

    create_name = "gated-create"
    create_result = tool.run({
        "action": "create",
        "name": create_name,
        "content": _skill_content(
            create_name,
            "Use for staged skill creation.",
            "## Steps\n1. Stay pending until approved.",
        ),
    }, ctx)
    _assert_staged_skill_write(create_result, wa, config, "create", create_name)
    assert not (cfg.skills_dir() / create_name).exists()

    skill_dir = _seed_skill("gated-skill", "## Steps\n1. Replace OLD_MARKER before use.")
    loader.invalidate()
    skill_file = skill_dir / "SKILL.md"
    before_skill = skill_file.read_text(encoding="utf-8")

    patch_result = tool.run({
        "action": "patch",
        "name": "gated-skill",
        "old_string": "OLD_MARKER",
        "new_string": "NEW_MARKER",
    }, ctx)
    _assert_staged_skill_write(patch_result, wa, config, "patch", "gated-skill")
    assert skill_file.read_text(encoding="utf-8") == before_skill
    assert "NEW_MARKER" not in skill_file.read_text(encoding="utf-8")

    support_path = skill_dir / "references" / "note.md"
    write_file_result = tool.run({
        "action": "write_file",
        "name": "gated-skill",
        "file_path": "references/note.md",
        "content": "# Note\n\nThis should stay pending.",
    }, ctx)
    _assert_staged_skill_write(write_file_result, wa, config, "write_file", "gated-skill")
    assert not support_path.exists()

    delete_result = tool.run({"action": "delete", "name": "gated-skill"}, ctx)
    _assert_staged_skill_write(delete_result, wa, config, "delete", "gated-skill")
    assert skill_dir.is_dir()
    assert skill_file.read_text(encoding="utf-8") == before_skill
    assert not (cfg.sub("skills_archive") / "gated-skill").exists()
