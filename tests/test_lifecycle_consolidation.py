"""Self-improvement depth (Hermes parity): skill lifecycle state machine
(active → stale → archived, with reactivation), real skill consolidation
(merge into references/ + archive pointer), and deterministic memory dedup."""

import json

import pytest

from aegis import config as cfg


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    return tmp_path


def _make_skill(name, description, origin="agent"):
    from aegis import provenance
    d = cfg.skills_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n\nbody of {name}\n")
    provenance.record(name, origin)
    return d


def _set_last_used(name, iso):
    from aegis import curator
    data = curator._load_usage()
    data.setdefault(name, {"count": 1})["last_used"] = iso
    curator._save_usage(data)


# --- skill lifecycle state machine ----------------------------------------

def test_active_to_stale_to_archived_transitions(home):
    from aegis import curator
    _make_skill("old-skill", "an old agent skill")
    # 45 days idle: past stale (30), before archive (90) -> STALE.
    _set_last_used("old-skill", "2000-01-01T00:00:00+00:00")  # ancient

    # 45d: stale only
    res = curator.apply_transitions(dry_run=False, stale_after_days=30, archive_after_days=10_000)
    assert "old-skill" in res["stale"]
    assert curator.skill_state("old-skill") == curator.STATE_STALE
    assert res["counts"]["marked_stale"] == 1

    # now archive-eligible too
    res2 = curator.apply_transitions(dry_run=False, stale_after_days=30, archive_after_days=90)
    assert "old-skill" in res2["archived"]
    assert curator.skill_state("old-skill") == curator.STATE_ARCHIVED
    assert "old-skill" in curator.archived()


def test_stale_reactivates_when_used_again(home):
    from aegis import curator
    from aegis.util import now_iso
    _make_skill("reborn", "a skill that comes back")
    curator._set_state("reborn", curator.STATE_STALE)
    _set_last_used("reborn", now_iso())             # used just now -> not stale anymore

    res = curator.apply_transitions(dry_run=False, stale_after_days=30, archive_after_days=90)
    assert "reborn" in res["reactivated"]
    assert curator.skill_state("reborn") == curator.STATE_ACTIVE
    assert res["counts"]["reactivated"] == 1


def test_pinned_skill_never_transitions(home):
    from aegis import curator
    _make_skill("keeper", "pinned skill")
    curator.pin("keeper", True)
    _set_last_used("keeper", "2000-01-01T00:00:00+00:00")
    res = curator.apply_transitions(dry_run=False, stale_after_days=30, archive_after_days=90)
    assert "keeper" not in res["stale"] and "keeper" not in res["archived"]


# --- skill consolidation ---------------------------------------------------

def test_consolidation_candidates_orient_weaker_into_stronger(home):
    from aegis import curator
    _make_skill("deploy-web", "Deploy the website to production with checks")
    _make_skill("deploy-site", "Deploy the website to production with checks")
    from aegis.util import now_iso
    _set_last_used("deploy-web", now_iso())         # deploy-web is the more-used survivor
    data = curator._load_usage()
    data["deploy-web"]["count"] = 9
    curator._save_usage(data)

    cands = curator.consolidation_candidates()
    assert cands and cands[0]["into"] == "deploy-web" and cands[0]["from"] == "deploy-site"


def test_consolidate_merges_and_archives_with_pointer(home):
    from aegis import curator
    _make_skill("alpha", "overlapping skill alpha")
    _make_skill("beta", "overlapping skill beta")

    assert curator.consolidate("beta", "alpha") is True
    ref = cfg.skills_dir() / "alpha" / "references" / "consolidated-beta.md"
    assert ref.exists() and "beta" in ref.read_text()
    assert not (cfg.skills_dir() / "beta").exists()         # moved out of live skills
    assert curator.skill_state("beta") == curator.STATE_ARCHIVED
    pointer = curator._archive_dir() / "beta" / ".consolidated_into"
    assert pointer.read_text().strip() == "alpha"

    # guards
    assert curator.consolidate("missing", "alpha") is False
    assert curator.consolidate("alpha", "alpha") is False


# --- memory consolidation --------------------------------------------------

def test_memory_consolidate_drops_near_duplicates(home):
    from aegis.memory import MemoryStore
    ms = MemoryStore()
    ms.add("memory", "Deploy script is scripts/ship.sh")
    ms.add("memory", "Deploy script is scripts/ship.sh and it runs on prod")  # superset
    ms.add("memory", "User likes terse replies")
    res = ms.consolidate("memory")
    assert res["before"] == 3 and res["after"] == 2
    entries = ms.entries("memory")
    # the longer of the duplicate pair survives; the unrelated fact stays
    assert any("runs on prod" in e for e in entries)
    assert any("terse replies" in e for e in entries)


def test_memory_consolidate_noop_when_distinct(home):
    from aegis.memory import MemoryStore
    ms = MemoryStore()
    ms.add("memory", "Project uses Python 3.14")
    ms.add("memory", "User is in the Asia/Kolkata timezone")
    res = ms.consolidate("memory")
    assert res["removed"] == [] and res["after"] == 2


def test_usage_json_persists_state(home):
    """State is stored (not recomputed) so it survives across curator runs."""
    from aegis import curator
    _make_skill("statey", "a skill with state")
    curator._set_state("statey", curator.STATE_STALE)
    raw = json.loads((cfg.skills_dir() / "usage.json").read_text())
    assert raw["statey"]["state"] == "stale"


def test_seed_prevents_premature_archive_on_old_mtime(home):
    """A never-used skill whose directory mtime is ancient must NOT be archived the
    first time the curator sees it — its clock is seeded to first-sight (Hermes parity)."""
    import os
    from aegis import curator
    d = _make_skill("ancient", "old but valid, never loaded")
    os.utime(d / "SKILL.md", (0, 0))                 # epoch mtime
    os.utime(d, (0, 0))
    res = curator.apply_transitions(dry_run=False, stale_after_days=30, archive_after_days=90)
    assert "ancient" not in res["archived"] and res["counts"]["seeded"] >= 1
    raw = json.loads((cfg.skills_dir() / "usage.json").read_text())
    assert raw["ancient"]["created_at"]              # clock anchored to now


def test_skill_manage_consolidate_action(home):
    """The consolidate action is reachable by the agent/curator (not just a Python helper)."""
    from aegis.config import Config
    from aegis.skills import SkillsLoader
    from aegis.tools.base import ToolContext
    from aegis.tools.skill_manage import SkillManageTool
    conf = Config.load()
    _make_skill("ship-a", "build and ship the release")
    _make_skill("ship-b", "build and ship the release")
    ctx = ToolContext(cwd=cfg.skills_dir(), skills=SkillsLoader(conf), config=conf)
    out = SkillManageTool().run({"action": "consolidate", "name": "ship-b", "into": "ship-a"}, ctx)
    assert not out.is_error and "ship-a" in out.content
    assert (cfg.skills_dir() / "ship-a" / "references" / "consolidated-ship-b.md").exists()
    # missing args are rejected
    bad = SkillManageTool().run({"action": "consolidate", "name": "ship-a"}, ctx)
    assert bad.is_error
