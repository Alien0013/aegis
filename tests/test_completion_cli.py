from __future__ import annotations


def test_completion_bash_uses_live_parser_commands(capsys):
    from aegis.cli.main import main

    assert main(["completion", "bash"]) == 0
    out = capsys.readouterr().out

    assert "project" in out
    assert "whatsapp-cloud" in out
    assert "bundles" in out
    assert "compgen" in out


def test_completion_bash_includes_command_actions(capsys):
    from aegis.cli.main import main

    assert main(["completion", "bash"]) == 0
    out = capsys.readouterr().out

    assert "sessions" in out
    assert "rename" in out
    assert "prune" in out
    assert "project" in out
    assert "create" in out
    assert "set-primary" in out


def test_completion_zsh_and_fish_include_subcommand_actions(capsys):
    from aegis.cli.main import main

    assert main(["completion", "zsh"]) == 0
    zsh = capsys.readouterr().out
    assert "sessions" in zsh
    assert "rename" in zsh
    assert "project" in zsh
    assert "set-primary" in zsh

    assert main(["completion", "fish"]) == 0
    fish = capsys.readouterr().out
    assert "complete -c aegis" in fish
    assert "__fish_seen_subcommand_from sessions" in fish
    assert "rename" in fish
    assert "__fish_seen_subcommand_from project" in fish
    assert "set-primary" in fish
