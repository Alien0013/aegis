"""Hardening: the dashboard's read-only file browser must not serve secret/credential
files as content, and uploads must not overwrite credential/SSH/cloud paths — even for
a token-holder (which matters once the dashboard is bound to a non-loopback host)."""

from pathlib import Path

import pytest

from aegis.dashboard import _dashboard_file_read, _is_sensitive_path


@pytest.mark.parametrize("rel", [
    ".env", ".env.local", "config.pem", "server.key", "id_rsa", "id_ed25519",
    ".ssh/authorized_keys", ".aws/credentials", ".gnupg/secring.gpg", "auth.json",
    ".netrc", "store.keystore",
])
def test_sensitive_paths_detected(tmp_path, rel):
    assert _is_sensitive_path(tmp_path / rel) is True


@pytest.mark.parametrize("rel", ["main.py", "README.md", "src/app.ts", "notes.txt"])
def test_ordinary_paths_allowed(tmp_path, rel):
    assert _is_sensitive_path(tmp_path / rel) is False


def test_file_read_blocks_secret_content(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-supersecret\n")
    out = _dashboard_file_read({"path": [str(env)]})
    assert "content" not in out
    assert "blocked" in out["error"].lower()
    # an ordinary file still reads fine
    ok = tmp_path / "notes.txt"
    ok.write_text("hello")
    assert _dashboard_file_read({"path": [str(ok)]})["content"] == "hello"


def test_upload_rejects_sensitive_destination():
    # the upload handler reuses _is_sensitive_path; verify the predicate it relies on
    assert _is_sensitive_path(Path.home() / ".ssh" / "authorized_keys") is True
    assert _is_sensitive_path(Path.home() / "project" / "data.csv") is False
