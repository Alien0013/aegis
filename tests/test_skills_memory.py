"""Skills engine (incl. self-improvement loop) and memory store."""

from __future__ import annotations


# --- skills -----------------------------------------------------------------
def test_bundled_skills_present():
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    names = {s.name for s in SkillsLoader(Config.load()).discover().values()}
    for s in ("web-research", "data-analysis", "debugging", "write-tests", "regex", "summarize"):
        assert s in names, s


def test_skill_create_view_improve_usage():
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    sl = SkillsLoader(Config.load())
    sl.create("my-skill", "does X. use for X.", "## Procedure\n1. do X")
    assert sl.activate("my-skill") is not None          # records a use
    assert sl.usage()["my-skill"]["count"] == 1
    sl.improve("my-skill", "remember to verify")
    assert "Learned Notes" in sl.discover()["my-skill"].path.read_text()


def test_skill_requires_gating(tmp_path):
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    d = tmp_path / "gated"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: gated\ndescription: needs a missing bin.\nrequires:\n  bins: [definitely-not-a-real-binary]\n---\nbody")
    cfg = Config.load()
    cfg.data["skills"]["paths"] = [str(tmp_path)]
    sl = SkillsLoader(cfg)
    assert "gated" in sl.discover()
    assert "gated" not in {s.name for s in sl.available()}


def test_skill_tier_precedence(tmp_path, monkeypatch):
    from aegis.config import Config, skills_dir
    from aegis.skills import SkillsLoader
    # personal tier
    personal = skills_dir() / "dup"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text("---\nname: dup\ndescription: personal.\n---\nP")
    # workspace tier (higher priority) under cwd/skills
    ws = tmp_path / "skills" / "dup"
    ws.mkdir(parents=True)
    (ws / "SKILL.md").write_text("---\nname: dup\ndescription: workspace.\n---\nW")
    sl = SkillsLoader(Config.load(), cwd=tmp_path)
    assert sl.discover()["dup"].description == "workspace."


def test_index_block_progressive():
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    block = SkillsLoader(Config.load()).index_block()
    assert "web-research" in block  # descriptions only (progressive disclosure)


def test_surface_inventory_counts_tools_and_skills():
    from aegis.config import Config
    from aegis.surface import skill_inventory, tool_inventory

    cfg = Config.load()
    cfg.data["tools"]["toolsets"] = ["core", "browser", "lsp", "mcp"]
    tools = tool_inventory(cfg)
    skills = skill_inventory(cfg)

    assert tools.enabled_count >= 20
    assert tools.total_count >= tools.enabled_count
    assert "bash" in tools.enabled_names      # core tool, no optional dep (browser needs playwright)
    assert skills.bundled_count >= 20
    assert "web-research" in skills.names


# --- memory -----------------------------------------------------------------
def test_memory_add_replace_remove_dedup():
    from aegis.memory import MemoryStore
    s = MemoryStore()
    s.add("memory", "uses pnpm")
    assert "already" in s.add("memory", "uses pnpm")     # dedup
    s.replace("memory", "pnpm", "uses bun")
    assert "bun" in s.raw("memory")
    s.remove("memory", "bun")
    assert "bun" not in s.raw("memory")


def test_memory_char_limit_drops_oldest():
    from aegis.constants import MEMORY_CHAR_LIMIT
    from aegis.memory import MemoryStore
    s = MemoryStore()
    for i in range(200):
        s.add("memory", f"fact number {i} " + "x" * 50)
    assert len(s.raw("memory")) <= MEMORY_CHAR_LIMIT + 200
    assert "fact number 199" in s.raw("memory")          # newest kept


def test_memory_history_append_recent():
    from aegis.memory import History
    h = History()
    for i in range(5):
        h.append("user", f"msg {i}", "sess")
    recent = h.recent(3)
    assert len(recent) == 3 and recent[-1]["content"] == "msg 4"


def test_memory_manager_snapshot_frozen():
    from aegis.config import Config
    from aegis.memory import MemoryManager
    mm = MemoryManager(Config.load())
    mm.store.add("memory", "alpha")
    block = mm.build_context_block()           # snapshot taken at construction (empty)
    assert "alpha" not in block
    mm.refresh_snapshot()
    assert "alpha" in mm.build_context_block()


def test_jsonl_memory_provider():
    from aegis.config import Config
    from aegis.memory_providers import build_memory_provider
    from aegis.types import Message
    p = build_memory_provider("jsonl", Config.load())
    p.sync_turn([Message.user("remember zeta"), Message.assistant("noted zeta")])
    assert "zeta" in p.system_prompt_block()
