"""web_verify: the frontend verification loop — pure verdict logic + graceful degrade."""

from pathlib import Path

from aegis.agent.coding_context import coding_workspace_block
from aegis.config import Config
from aegis.tools.base import ToolContext
from aegis.tools.ui_verify import WebVerifyTool, evaluate


def test_clean_page_passes():
    v = evaluate(console_errors=[], page_errors=[], text="Welcome",
                 expect_text=None, selector_found=None, expect_selector=None,
                 allow_console_errors=False)
    assert v.passed is True


def test_console_errors_fail():
    v = evaluate(console_errors=["error: boom"], page_errors=[], text="x",
                 expect_text=None, selector_found=None, expect_selector=None,
                 allow_console_errors=False)
    assert v.passed is False and "console/page error" in v.reasons[0]


def test_allow_console_errors_overrides():
    v = evaluate(console_errors=["error: boom"], page_errors=[], text="x",
                 expect_text=None, selector_found=None, expect_selector=None,
                 allow_console_errors=True)
    assert v.passed is True


def test_missing_expected_text_fails():
    v = evaluate(console_errors=[], page_errors=[], text="Hello world",
                 expect_text="Dashboard", selector_found=None, expect_selector=None,
                 allow_console_errors=False)
    assert v.passed is False and "expected text" in v.reasons[0]


def test_expected_text_present_passes():
    v = evaluate(console_errors=[], page_errors=[], text="My Dashboard",
                 expect_text="dashboard", selector_found=None, expect_selector=None,
                 allow_console_errors=False)
    assert v.passed is True


def test_missing_selector_fails():
    v = evaluate(console_errors=[], page_errors=[], text="x",
                 expect_text=None, selector_found=False, expect_selector="#app",
                 allow_console_errors=False)
    assert v.passed is False and "selector" in v.reasons[0]


def test_requires_url():
    out = WebVerifyTool().run({}, ToolContext(cwd=Path("."), config=Config.load()))
    assert out.is_error and "url" in out.content


def test_web_hint_only_for_web_projects(tmp_path):
    # Python-only project: no web_verify hint.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    py_block = coding_workspace_block(tmp_path, Config.load())
    assert "web_verify" not in py_block
    # Add a web marker: the hint appears.
    (tmp_path / "package.json").write_text('{"name":"x"}')
    web_block = coding_workspace_block(tmp_path, Config.load())
    assert "web_verify" in web_block
