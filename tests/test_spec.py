"""Spec-driven development: persistent requirements→design→tasks artifacts."""

import textwrap

from aegis.config import Config
from aegis.spec import SpecStore, implementation_prompt, slugify


def test_slugify():
    assert slugify("Add Dark Mode!") == "add-dark-mode"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert slugify("***") == "spec"


def test_create_and_get(tmp_path):
    store = SpecStore(tmp_path)
    spec = store.create("Add Dark Mode")
    assert spec.slug == "add-dark-mode"
    assert (tmp_path / ".aegis/specs/add-dark-mode/spec.md").exists()
    again = store.get("add-dark-mode")
    assert again is not None and again.title == "Add Dark Mode"
    assert again.status == "draft"


def test_tasks_and_progress(tmp_path):
    store = SpecStore(tmp_path)
    body = textwrap.dedent("""
        # Feature

        ## Requirements
        - must work

        ## Tasks
        - [ ] write the parser
        - [x] add the config flag
        - [ ] wire the CLI
    """)
    spec = store.create("Feature", body)
    tasks = spec.tasks()
    assert len(tasks) == 3
    assert tasks[1] == (True, "add the config flag")
    assert spec.progress() == (1, 3)


def test_mark_task_ticks_checkbox(tmp_path):
    store = SpecStore(tmp_path)
    body = "# F\n\n## Tasks\n- [ ] one\n- [ ] two\n"
    store.create("F", body)
    store.mark_task("f", 1, True)
    spec = store.get("f")
    assert spec.tasks() == [(False, "one"), (True, "two")]
    assert spec.progress() == (1, 2)


def test_checkboxes_outside_tasks_section_ignored(tmp_path):
    store = SpecStore(tmp_path)
    body = "# F\n\n## Requirements\n- [ ] not a task\n\n## Tasks\n- [ ] real task\n"
    spec = store.create("F", body)
    assert spec.tasks() == [(False, "real task")]


def test_set_status_and_list(tmp_path):
    store = SpecStore(tmp_path)
    store.create("Alpha")
    store.create("Beta")
    store.set_status("alpha", "approved")
    specs = {s.slug: s for s in store.list()}
    assert specs["alpha"].status == "approved"
    assert set(specs) == {"alpha", "beta"}


def test_implementation_prompt_includes_body_and_progress(tmp_path):
    store = SpecStore(tmp_path)
    spec = store.create("X", "# X\n\n## Tasks\n- [x] done\n- [ ] todo\n")
    prompt = implementation_prompt(spec)
    assert "1/2 tasks done" in prompt
    assert "## Tasks" in prompt
    assert ".aegis/specs/x/spec.md" in prompt   # tells the agent where to tick tasks


def test_from_config_uses_spec_dir(tmp_path):
    cfg = Config.load()
    store = SpecStore.from_config(cfg, cwd=tmp_path)
    spec = store.create("Z")
    assert (tmp_path / ".aegis/specs/z/spec.md").exists()
    assert spec.path == tmp_path / ".aegis/specs/z"
