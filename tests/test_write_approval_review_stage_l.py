from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def _review():
    return importlib.import_module("aegis.write_approval_review")


def _write_approval():
    return importlib.import_module("aegis.write_approval")


def _config():
    from aegis.config import Config

    return Config.load()


def _set_gate(config: Any, subsystem: str, enabled: bool) -> None:
    wa = _write_approval()
    config.data.setdefault(subsystem, {})[wa.CONFIG_KEY] = enabled


def _memory_path() -> Path:
    from aegis import config as cfg

    return cfg.memories_dir() / "MEMORY.md"


def _skill_content(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n"


def _seed_skill(name: str, body: str) -> Path:
    from aegis import config as cfg

    skill_dir = cfg.skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _skill_content(name, "Use for write approval review tests.", body),
        encoding="utf-8",
    )
    return skill_dir


def _lower(text: str | None) -> str:
    return str(text or "").lower()


def test_pending_list_and_reject_aliases_cover_memory_and_skills():
    review = _review()
    wa = _write_approval()
    config = _config()

    memory_one = wa.stage_write(
        wa.MEMORY,
        {"action": "add", "target": "memory", "content": "Remember alpha pending."},
        "Add alpha memory",
        origin="foreground",
        config=config,
    )
    memory_two = wa.stage_write(
        wa.MEMORY,
        {"action": "add", "target": "memory", "content": "Remember beta pending."},
        "Add beta memory",
        origin="background_review",
        config=config,
    )
    skill_one = wa.stage_write(
        wa.SKILLS,
        {"action": "create", "name": "pending-alpha", "content": _skill_content("pending-alpha", "Alpha.", "## Body")},
        "Create pending-alpha",
        origin="foreground",
        config=config,
    )
    skill_two = wa.stage_write(
        wa.SKILLS,
        {"action": "delete", "name": "pending-beta"},
        "Delete pending-beta",
        origin="background_review",
        config=config,
    )

    bare_memory = review.handle_pending_subcommand(wa.MEMORY, [], config=config)
    assert memory_one["id"] in bare_memory
    assert memory_two["id"] in bare_memory
    assert "Add alpha memory" in bare_memory
    assert "Add beta memory" in bare_memory
    assert f"{wa.MEMORY}.{wa.CONFIG_KEY}" in bare_memory
    assert "/memory approve <id>" in bare_memory
    assert "/memory reject <id>" in bare_memory

    skill_pending = review.handle_pending_subcommand(wa.SKILLS, ["pending"], config=config)
    assert skill_one["id"] in skill_pending
    assert skill_two["id"] in skill_pending
    assert "Create pending-alpha" in skill_pending
    assert "Delete pending-beta" in skill_pending
    assert "/skills approve <id>" in skill_pending
    assert "/skills reject <id>" in skill_pending
    assert "/skills diff <id>" in skill_pending

    rejected_memory = review.handle_pending_subcommand(
        wa.MEMORY,
        ["reject", memory_one["id"]],
        config=config,
    )
    assert "rejected" in _lower(rejected_memory)
    assert wa.get_pending(wa.MEMORY, memory_one["id"], config=config) is None
    assert wa.get_pending(wa.MEMORY, memory_two["id"], config=config) is not None

    denied_skill = review.handle_pending_subcommand(
        wa.SKILLS,
        ["deny", skill_one["id"]],
        config=config,
    )
    assert "rejected" in _lower(denied_skill) or "denied" in _lower(denied_skill)
    assert wa.get_pending(wa.SKILLS, skill_one["id"], config=config) is None
    assert wa.get_pending(wa.SKILLS, skill_two["id"], config=config) is not None

    dropped_memory = review.handle_pending_subcommand(wa.MEMORY, ["drop", "all"], config=config)
    assert "1" in dropped_memory
    assert wa.list_pending(wa.MEMORY, config=config) == []

    rejected_skills = review.handle_pending_subcommand(wa.SKILLS, ["reject", "all"], config=config)
    assert "1" in rejected_skills
    assert wa.list_pending(wa.SKILLS, config=config) == []


def test_memory_approve_applies_add_discards_after_success_and_reports_failures():
    review = _review()
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.MEMORY, True)

    from aegis.memory import MemoryManager

    manager = MemoryManager(config)
    pending = wa.stage_write(
        wa.MEMORY,
        {
            "action": "add",
            "target": "memory",
            "content": "The review command applies approved memory writes.",
        },
        "Add approved review memory",
        origin="background_review",
        config=config,
    )

    approved = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approve", pending["id"]],
        config=config,
        memory_manager=manager,
    )

    assert "approved" in _lower(approved)
    assert "The review command applies approved memory writes." in _memory_path().read_text(encoding="utf-8")
    assert wa.get_pending(wa.MEMORY, pending["id"], config=config) is None

    bad = wa.stage_write(
        wa.MEMORY,
        {"action": "add", "target": "memory"},
        "Invalid memory add",
        origin="background_review",
        config=config,
    )
    missing = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approve", "missing-memory-id"],
        config=config,
        memory_manager=manager,
    )
    assert "missing-memory-id" in missing
    assert "no pending" in _lower(missing)
    assert wa.get_pending(wa.MEMORY, bad["id"], config=config) is not None

    failed = review.handle_pending_subcommand(
        wa.MEMORY,
        ["apply", bad["id"]],
        config=config,
        memory_manager=manager,
    )
    assert "failed" in _lower(failed) or "0" in failed
    assert wa.get_pending(wa.MEMORY, bad["id"], config=config) is not None


def test_skill_approve_applies_patch_without_restaging_and_discards_record(tmp_path):
    review = _review()
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.SKILLS, True)

    from aegis.skills import SkillsLoader

    skill_dir = _seed_skill("review-patch", "## Steps\n1. Replace BEFORE_TOKEN on approval.")
    skill_file = skill_dir / "SKILL.md"
    loader = SkillsLoader(config, cwd=tmp_path)
    loader.invalidate()

    pending = wa.stage_write(
        wa.SKILLS,
        {
            "action": "patch",
            "name": "review-patch",
            "file_path": "SKILL.md",
            "old_string": "BEFORE_TOKEN",
            "new_string": "AFTER_TOKEN",
        },
        "Patch review-patch",
        origin="background_review",
        config=config,
    )

    approved = review.handle_pending_subcommand(
        wa.SKILLS,
        ["apply", pending["id"]],
        config=config,
        skills_loader=loader,
    )

    assert "approved" in _lower(approved)
    assert "AFTER_TOKEN" in skill_file.read_text(encoding="utf-8")
    assert "BEFORE_TOKEN" not in skill_file.read_text(encoding="utf-8")
    assert wa.list_pending(wa.SKILLS, config=config) == []


def test_skill_diff_returns_header_plus_create_content_and_patch_diff(tmp_path):
    review = _review()
    wa = _write_approval()
    config = _config()

    from aegis.skills import SkillsLoader

    loader = SkillsLoader(config, cwd=tmp_path)
    create_content = _skill_content(
        "diff-create",
        "Use for pending diff create tests.",
        "## Steps\n1. Show the proposed create content.",
    )
    created = wa.stage_write(
        wa.SKILLS,
        {"action": "create", "name": "diff-create", "content": create_content},
        "Create diff-create",
        origin="foreground",
        config=config,
    )

    create_diff = review.handle_pending_subcommand(
        wa.SKILLS,
        ["diff", created["id"]],
        config=config,
        skills_loader=loader,
    )
    assert f"# Pending skill write {created['id']}" in create_diff
    assert "Create diff-create" in create_diff
    assert "---" in create_diff
    assert "Show the proposed create content." in create_diff

    _seed_skill("diff-patch", "## Steps\n1. Replace OLD_DIFF on approval.")
    loader.invalidate()
    patched = wa.stage_write(
        wa.SKILLS,
        {
            "action": "patch",
            "name": "diff-patch",
            "file_path": "SKILL.md",
            "old_string": "OLD_DIFF",
            "new_string": "NEW_DIFF",
        },
        "Patch diff-patch",
        origin="foreground",
        config=config,
    )

    patch_diff = review.handle_pending_subcommand(
        wa.SKILLS,
        ["diff", patched["id"]],
        config=config,
        skills_loader=loader,
    )
    assert f"# Pending skill write {patched['id']}" in patch_diff
    assert "Patch diff-patch" in patch_diff
    assert "OLD_DIFF" in patch_diff
    assert "NEW_DIFF" in patch_diff


def test_approval_toggle_status_calls_set_mode_fn_and_rejects_invalid_values():
    review = _review()
    wa = _write_approval()
    config = _config()
    _set_gate(config, wa.MEMORY, False)
    calls: list[bool] = []

    def set_mode(enabled: bool) -> None:
        calls.append(enabled)
        _set_gate(config, wa.MEMORY, enabled)

    status = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approval", "status"],
        config=config,
        set_mode_fn=set_mode,
    )
    assert f"{wa.MEMORY}.{wa.CONFIG_KEY}" in status
    assert "off" in _lower(status)
    assert "/memory approval <on|off>" in status
    assert calls == []

    enabled = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approval", "on"],
        config=config,
        set_mode_fn=set_mode,
    )
    assert "on" in _lower(enabled)
    assert calls == [True]

    disabled = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approval", "off"],
        config=config,
        set_mode_fn=set_mode,
    )
    assert "off" in _lower(disabled)
    assert calls == [True, False]

    invalid = review.handle_pending_subcommand(
        wa.MEMORY,
        ["approval", "maybe"],
        config=config,
        set_mode_fn=set_mode,
    )
    assert "invalid" in _lower(invalid)
    assert "on" in _lower(invalid)
    assert "off" in _lower(invalid)
    assert calls == [True, False]
