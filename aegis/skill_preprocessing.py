"""SKILL.md preprocessing helpers.

AEGIS mirrors AEGIS' conservative behavior here: template variables are
expanded by default, while inline shell snippets are disabled unless the user
opts in through ``skills.inline_shell``.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_TEMPLATE_RE = re.compile(r"\$\{(AEGIS_SKILL_DIR|AEGIS_SESSION_ID)\}")
_INLINE_SHELL_RE = re.compile(r"!`([^`\n]+)`")
_INLINE_SHELL_MAX_OUTPUT = 4000


def substitute_template_vars(
    content: str,
    skill_dir: Path | None,
    session_id: str | None,
) -> str:
    """Replace supported skill template variables when values are available."""
    if not content:
        return content

    skill_dir_str = str(skill_dir) if skill_dir else None

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "AEGIS_SKILL_DIR" and skill_dir_str:
            return skill_dir_str
        if token == "AEGIS_SESSION_ID" and session_id:
            return str(session_id)
        return match.group(0)

    return _SKILL_TEMPLATE_RE.sub(_replace, content)


def run_inline_shell(command: str, cwd: Path | None, timeout: int) -> str:
    """Run one inline shell snippet and return a bounded stdout/stderr string."""
    try:
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"[inline-shell timeout after {timeout}s: {command}]"
    except FileNotFoundError:
        return "[inline-shell error: bash not found]"
    except Exception as exc:  # noqa: BLE001
        return f"[inline-shell error: {exc}]"

    output = (completed.stdout or "").rstrip("\n")
    if not output and completed.stderr:
        output = completed.stderr.rstrip("\n")
    if len(output) > _INLINE_SHELL_MAX_OUTPUT:
        output = output[:_INLINE_SHELL_MAX_OUTPUT] + "...[truncated]"
    return output


def expand_inline_shell(content: str, skill_dir: Path | None, timeout: int) -> str:
    """Replace !`cmd` snippets with stdout, using the skill dir as cwd."""
    if "!`" not in content:
        return content

    def _replace(match: re.Match[str]) -> str:
        cmd = match.group(1).strip()
        return run_inline_shell(cmd, skill_dir, timeout) if cmd else ""

    return _INLINE_SHELL_RE.sub(_replace, content)


def preprocess_skill_content(
    content: str,
    skill_dir: Path | None,
    session_id: str | None = None,
    skills_cfg: dict | None = None,
) -> str:
    """Apply configured template and optional inline-shell preprocessing."""
    if not content:
        return content
    cfg = skills_cfg if isinstance(skills_cfg, dict) else {}
    if cfg.get("template_vars", True):
        content = substitute_template_vars(content, skill_dir, session_id)
    if cfg.get("inline_shell", False):
        timeout = int(cfg.get("inline_shell_timeout", 10) or 10)
        content = expand_inline_shell(content, skill_dir, timeout)
    return content
