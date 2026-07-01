from __future__ import annotations


def _skill_content(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n"


def _context(tmp_path):
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    from aegis.tools.base import ToolContext
    from aegis.tools.skill_manage import SkillManageTool

    config = Config.load()
    loader = SkillsLoader(config, cwd=tmp_path)
    return config, loader, ToolContext(cwd=tmp_path, config=config, skills=loader), SkillManageTool()


def _seed_skill(name: str, body: str):
    from aegis import config as cfg

    skill_dir = cfg.skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _skill_content(name, "Use for skill_manage parity tests.", body),
        encoding="utf-8",
    )
    return skill_dir


def test_skill_manage_edit_remove_file_and_hermes_file_content_alias(tmp_path):
    from aegis import config as cfg

    _config, _loader, ctx, tool = _context(tmp_path)
    created = tool.run({
        "action": "create",
        "name": "parity-skill",
        "description": "Use for parity create checks.",
        "body": "## Steps\n1. Replace OLD_MARKER before finishing.",
    }, ctx)
    assert not created.is_error

    duplicate = tool.run({
        "action": "create",
        "name": "parity-skill",
        "description": "Duplicate skill should not overwrite.",
        "body": "## Steps\n1. Do not replace the existing skill.",
    }, ctx)
    assert duplicate.is_error
    assert "already exists" in duplicate.content

    edited_content = _skill_content(
        "parity-skill",
        "Use for full skill rewrites.",
        "## Steps\n1. Replace OLD_MARKER after a full rewrite.",
    )
    edited = tool.run({"action": "edit", "name": "parity-skill", "content": edited_content}, ctx)
    assert not edited.is_error
    assert edited.data["_change"]["action"] == "edit"
    assert "full skill rewrites" in (cfg.skills_dir() / "parity-skill" / "SKILL.md").read_text()

    support = tool.run({
        "action": "write_file",
        "name": "parity-skill",
        "file_path": "references/deploy-note.md",
        "file_content": "# Deploy Note\n\nReusable provider quirk.",
    }, ctx)
    assert not support.is_error
    support_path = cfg.skills_dir() / "parity-skill" / "references" / "deploy-note.md"
    assert support_path.read_text(encoding="utf-8") == "# Deploy Note\n\nReusable provider quirk."

    removed = tool.run({
        "action": "remove_file",
        "name": "parity-skill",
        "file_path": "references/deploy-note.md",
    }, ctx)
    assert not removed.is_error
    assert not support_path.exists()
    assert not support_path.parent.exists()

    blocked_remove = tool.run({"action": "remove_file", "name": "parity-skill", "file_path": "SKILL.md"}, ctx)
    assert blocked_remove.is_error


def test_skill_manage_stages_edit_write_alias_and_remove_file_without_mutating(tmp_path):
    from aegis import config as cfg
    from aegis import write_approval as wa

    config, loader, ctx, tool = _context(tmp_path)
    config.data.setdefault(wa.SKILLS, {})["write_approval"] = True
    skill_dir = _seed_skill("gated-skill", "## Steps\n1. Replace OLD_MARKER before use.")
    loader.invalidate()
    skill_file = skill_dir / "SKILL.md"
    before_skill = skill_file.read_text(encoding="utf-8")

    edit_content = _skill_content(
        "gated-skill",
        "Use for staged full skill rewrites.",
        "## Steps\n1. This full rewrite must stay pending.",
    )
    edit_result = tool.run({"action": "edit", "name": "gated-skill", "content": edit_content}, ctx)
    assert not edit_result.is_error
    edit_pending_id = edit_result.data["pending_id"]
    edit_record = wa.get_pending(wa.SKILLS, edit_pending_id, config=config)
    assert edit_record["payload"]["action"] == "edit"
    assert skill_file.read_text(encoding="utf-8") == before_skill

    support_path = skill_dir / "references" / "note.md"
    write_file_result = tool.run({
        "action": "write_file",
        "name": "gated-skill",
        "file_path": "references/note.md",
        "file_content": "# Note\n\nThis should stay pending.",
    }, ctx)
    assert not write_file_result.is_error
    write_record = wa.get_pending(wa.SKILLS, write_file_result.data["pending_id"], config=config)
    assert write_record["payload"]["file_content"] == "# Note\n\nThis should stay pending."
    assert not support_path.exists()

    support_path.parent.mkdir(parents=True)
    support_path.write_text("# Note\n\nExisting support file.", encoding="utf-8")
    remove_file_result = tool.run({
        "action": "remove_file",
        "name": "gated-skill",
        "file_path": "references/note.md",
    }, ctx)
    assert not remove_file_result.is_error
    remove_record = wa.get_pending(wa.SKILLS, remove_file_result.data["pending_id"], config=config)
    assert remove_record["payload"]["action"] == "remove_file"
    assert support_path.exists()
    assert skill_file.read_text(encoding="utf-8") == before_skill
    assert not (cfg.sub("skills_archive") / "gated-skill").exists()


def test_apply_skill_pending_replays_without_restaging(tmp_path):
    from aegis import write_approval as wa
    from aegis.tools.skill_manage import apply_skill_pending

    config, loader, ctx, _tool = _context(tmp_path)
    config.data.setdefault(wa.SKILLS, {})["write_approval"] = True
    skill_dir = _seed_skill("pending-skill", "## Steps\n1. Before approval.")
    support_path = skill_dir / "references" / "note.md"
    loader.invalidate()

    edit_content = _skill_content(
        "pending-skill",
        "Use for approved full rewrites.",
        "## Steps\n1. After approval.",
    )
    edited = apply_skill_pending({"action": "edit", "name": "pending-skill", "content": edit_content}, ctx)
    assert not edited.is_error
    assert "After approval" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    written = apply_skill_pending({
        "action": "write_file",
        "name": "pending-skill",
        "file_path": "references/note.md",
        "file_content": "approved support file",
    }, ctx)
    assert not written.is_error
    assert support_path.read_text(encoding="utf-8") == "approved support file"

    removed = apply_skill_pending({
        "action": "remove_file",
        "name": "pending-skill",
        "file_path": "references/note.md",
    }, ctx)
    assert not removed.is_error
    assert not support_path.exists()
    assert wa.list_pending(wa.SKILLS, config=config) == []


def test_skill_manage_fuzzy_patch_and_support_file_overwrite(tmp_path):
    from aegis import config as cfg

    _config, _loader, ctx, tool = _context(tmp_path)
    created = tool.run({
        "action": "create",
        "name": "fuzzy-skill",
        "description": "Use for fuzzy skill patches.",
        "body": "## Steps\n1. Run    the deploy command.\n2. Verify output.",
    }, ctx)
    assert not created.is_error

    patched = tool.run({
        "action": "patch",
        "name": "fuzzy-skill",
        "old_string": "1. Run the deploy command.",
        "new_string": "1. Run the release command.",
    }, ctx)
    assert not patched.is_error, patched.content
    assert "matched via" in patched.data["message"]
    assert "Run    the deploy command" not in (cfg.skills_dir() / "fuzzy-skill" / "SKILL.md").read_text()

    support_path = cfg.skills_dir() / "fuzzy-skill" / "references" / "note.md"
    first = tool.run({
        "action": "write_file",
        "name": "fuzzy-skill",
        "file_path": "references/note.md",
        "content": "first",
    }, ctx)
    second = tool.run({
        "action": "write_file",
        "name": "fuzzy-skill",
        "file_path": "references/note.md",
        "content": "second",
    }, ctx)

    assert not first.is_error
    assert not second.is_error
    assert second.data["overwrite"] is True
    assert support_path.read_text(encoding="utf-8") == "second"


def test_background_review_must_view_skill_file_before_mutating(tmp_path):
    from aegis import provenance

    _config, loader, ctx, tool = _context(tmp_path)
    skill_dir = _seed_skill("review-guard", "## Steps\n1. Replace OLD_TOKEN.")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "note.md").write_text("support OLD_TOKEN", encoding="utf-8")
    loader.invalidate()

    with provenance.origin_scope("agent"):
        blocked = tool.run({
            "action": "patch",
            "name": "review-guard",
            "old_string": "OLD_TOKEN",
            "new_string": "NEW_TOKEN",
        }, ctx)
        assert blocked.is_error
        assert blocked.data["_read_before_write_required"] is True

        viewed = tool.run({"action": "view", "name": "review-guard"}, ctx)
        assert not viewed.is_error
        patched = tool.run({
            "action": "patch",
            "name": "review-guard",
            "old_string": "OLD_TOKEN",
            "new_string": "NEW_TOKEN",
        }, ctx)
        assert not patched.is_error

        blocked_support = tool.run({
            "action": "remove_file",
            "name": "review-guard",
            "file_path": "references/note.md",
        }, ctx)
        assert blocked_support.is_error
        assert blocked_support.data["_read_before_write_required"] is True

        viewed_support = tool.run({
            "action": "view",
            "name": "review-guard",
            "file_path": "references/note.md",
        }, ctx)
        assert not viewed_support.is_error
        removed = tool.run({
            "action": "remove_file",
            "name": "review-guard",
            "file_path": "references/note.md",
        }, ctx)
        assert not removed.is_error
