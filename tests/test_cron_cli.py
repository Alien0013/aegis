from __future__ import annotations


def test_cron_cli_create_edit_pause_resume_remove_aliases(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.cron import CronStore

    assert main(["cron", "create", "30m", "check", "server"]) == 0
    out = capsys.readouterr().out
    assert "added cron" in out
    job = CronStore().list()[0]
    assert job.schedule == "30m"
    assert job.prompt == "check server"
    job_id = job.id

    assert main(["cron", "pause", job_id]) == 0
    paused = CronStore().get(job_id)
    assert paused is not None
    assert paused.enabled is False
    assert "paused" in capsys.readouterr().out

    assert main(["cron", "resume", job_id]) == 0
    resumed = CronStore().get(job_id)
    assert resumed is not None
    assert resumed.enabled is True
    assert "resumed" in capsys.readouterr().out

    assert main(["cron", "edit", job_id, "1h", "check", "api"]) == 0
    edited = CronStore().get(job_id)
    assert edited is not None
    assert edited.schedule == "1h"
    assert edited.prompt == "check api"
    assert "updated" in capsys.readouterr().out

    assert main(["cron", "remove", job_id]) == 0
    assert CronStore().list() == []
    assert "removed" in capsys.readouterr().out


def test_cron_cli_tick_runs_due_jobs_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    import aegis.cron as cron_mod

    seen = {}

    def fake_tick(config, **kwargs):
        seen["config"] = config
        seen["kwargs"] = kwargs
        return 2

    monkeypatch.setattr(cron_mod, "tick", fake_tick)

    assert main(["cron", "tick"]) == 0
    assert seen["config"] is not None
    assert "ran 2 cron job(s)" in capsys.readouterr().out
