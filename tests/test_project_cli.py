from __future__ import annotations

from pathlib import Path


def test_project_cli_create_list_show_use_and_clear(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["project", "create", "Client Portal", str(repo), "--use", "--description", "delivery workspace"]) == 0
    out = capsys.readouterr().out
    assert "Created project client-portal" in out
    assert str(repo) in out

    assert main(["project", "list"]) == 0
    out = capsys.readouterr().out
    assert "* client-portal" in out
    assert "Client Portal" in out

    assert main(["project", "show", "client-portal"]) == 0
    out = capsys.readouterr().out
    assert "client-portal" in out
    assert "about:   delivery workspace" in out
    assert "primary:" in out

    assert main(["project", "use"]) == 0
    out = capsys.readouterr().out
    assert "Cleared active project" in out

    assert main(["project", "list"]) == 0
    out = capsys.readouterr().out
    assert "* client-portal" not in out


def test_project_cli_path_switch_and_current_aliases(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    repo = tmp_path / "repo"
    repo.mkdir()

    assert main(["project", "create", "Aegis Parity", "--path", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Created project aegis-parity" in out
    assert f"primary: {repo}" in out

    assert main(["project", "switch", "aegis-parity"]) == 0
    out = capsys.readouterr().out
    assert "Active project: aegis-parity" in out

    assert main(["project", "current"]) == 0
    out = capsys.readouterr().out
    assert "active project:" in out
    assert "aegis-parity" in out
    assert f"primary: {repo}" in out


def test_project_cli_folder_primary_archive_restore_and_board(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    repo = tmp_path / "repo"
    docs = tmp_path / "docs"
    repo.mkdir()
    docs.mkdir()

    assert main(["project", "create", "AEGIS", str(repo), "--slug", "aegis", "--use"]) == 0
    capsys.readouterr()

    assert main(["project", "add-folder", "aegis", str(docs), "--label", "docs", "--primary"]) == 0
    out = capsys.readouterr().out
    assert "Added" in out

    assert main(["project", "show", "aegis"]) == 0
    out = capsys.readouterr().out
    assert f"primary: {docs}" in out
    assert f"* {docs} (docs)" in out

    assert main(["project", "set-primary", "aegis", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Set primary" in out

    assert main(["project", "bind-board", "aegis", "delivery-board"]) == 0
    out = capsys.readouterr().out
    assert "Bound aegis -> board delivery-board" in out

    assert main(["project", "rename", "aegis", "AEGIS Harness"]) == 0
    out = capsys.readouterr().out
    assert "Renamed aegis -> AEGIS Harness" in out

    assert main(["project", "remove-folder", "aegis", str(docs)]) == 0
    out = capsys.readouterr().out
    assert "Removed" in out

    assert main(["project", "archive", "aegis"]) == 0
    out = capsys.readouterr().out
    assert "Archived aegis" in out

    assert main(["project", "list"]) == 0
    out = capsys.readouterr().out
    assert "AEGIS Harness" not in out
    assert "folder(s)" not in out

    assert main(["project", "list", "--all"]) == 0
    out = capsys.readouterr().out
    assert "aegis" in out
    assert "(archived)" in out

    assert main(["project", "restore", "aegis"]) == 0
    out = capsys.readouterr().out
    assert "Restored aegis" in out


def test_project_cli_reports_unknown_project(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["project", "show", "missing"]) == 1
    err = capsys.readouterr().err
    assert "no such project: missing" in err


def test_project_store_selects_new_primary_when_primary_folder_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import projects

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    with projects.connect_closing() as conn:
        pid = projects.create_project(conn, name="Workspace", folders=[str(first), str(second)])
        assert projects.set_primary(conn, pid, str(second))
        assert projects.remove_folder(conn, pid, str(second))
        project = projects.get_project(conn, pid)

    assert project is not None
    assert project.primary_path == str(first)
    assert [Path(folder.path).name for folder in project.folders] == ["first"]
