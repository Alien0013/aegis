"""Focused Hermes parity sidecar for the guardrails file lane."""

from __future__ import annotations

import importlib
import json


HERMES_IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "mcp_filesystem_read_file",
        "mcp_filesystem_read_text_file",
        "mcp_filesystem_read_multiple_files",
        "mcp_filesystem_list_directory",
        "mcp_filesystem_list_directory_with_sizes",
        "mcp_filesystem_directory_tree",
        "mcp_filesystem_get_file_info",
        "mcp_filesystem_search_files",
    }
)

HERMES_MUTATING_TOOL_NAMES = frozenset(
    {
        "terminal",
        "execute_code",
        "write_file",
        "patch",
        "todo",
        "memory",
        "skill_manage",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "send_message",
        "cronjob",
        "delegate_task",
        "process",
    }
)


def _guardrails_module():
    return importlib.import_module("aegis.agent.guardrails")


def test_guardrail_config_from_mapping_matches_hermes_contract():
    guardrails = _guardrails_module()
    config_cls = getattr(guardrails, "ToolCallGuardrailConfig", None)

    assert config_cls is not None

    defaults = config_cls.from_mapping(None)
    assert defaults.warnings_enabled is True
    assert defaults.hard_stop_enabled is False
    assert defaults.exact_failure_warn_after == 2
    assert defaults.exact_failure_block_after == 5
    assert defaults.same_tool_failure_warn_after == 3
    assert defaults.same_tool_failure_halt_after == 8
    assert defaults.no_progress_warn_after == 2
    assert defaults.no_progress_block_after == 5
    assert defaults.idempotent_tools == HERMES_IDEMPOTENT_TOOL_NAMES
    assert defaults.mutating_tools == HERMES_MUTATING_TOOL_NAMES

    mapped = config_cls.from_mapping(
        {
            "warnings_enabled": "off",
            "hard_stop_enabled": "yes",
            "warn_after": {
                "exact_failure": "4",
                "same_tool_failure": 5,
                "idempotent_no_progress": "6",
            },
            "hard_stop_after": {
                "exact_failure": "7",
                "same_tool_failure": 8,
                "idempotent_no_progress": "9",
            },
        }
    )
    assert mapped.warnings_enabled is False
    assert mapped.hard_stop_enabled is True
    assert mapped.exact_failure_warn_after == 4
    assert mapped.same_tool_failure_warn_after == 5
    assert mapped.no_progress_warn_after == 6
    assert mapped.exact_failure_block_after == 7
    assert mapped.same_tool_failure_halt_after == 8
    assert mapped.no_progress_block_after == 9

    legacy = config_cls.from_mapping(
        {
            "warnings_enabled": 1,
            "hard_stop_enabled": 0,
            "exact_failure_warn_after": "10",
            "same_tool_failure_warn_after": "11",
            "no_progress_warn_after": "12",
            "exact_failure_block_after": "13",
            "same_tool_failure_halt_after": "14",
            "no_progress_block_after": "15",
        }
    )
    assert legacy.warnings_enabled is True
    assert legacy.hard_stop_enabled is False
    assert legacy.exact_failure_warn_after == 10
    assert legacy.same_tool_failure_warn_after == 11
    assert legacy.no_progress_warn_after == 12
    assert legacy.exact_failure_block_after == 13
    assert legacy.same_tool_failure_halt_after == 14
    assert legacy.no_progress_block_after == 15

    invalid = config_cls.from_mapping(
        {
            "warnings_enabled": "maybe",
            "hard_stop_enabled": object(),
            "warn_after": {"exact_failure": 0},
            "hard_stop_after": {"same_tool_failure": -1},
        }
    )
    assert invalid == defaults


def test_guardrail_decision_metadata_is_structured_and_non_reversible():
    guardrails = _guardrails_module()
    config_cls = getattr(guardrails, "ToolCallGuardrailConfig", None)
    controller_cls = getattr(guardrails, "ToolCallGuardrailController", None)

    assert config_cls is not None
    assert controller_cls is not None

    controller = controller_cls(config_cls())
    args = {"command": "exit 2", "env": {"B": 2, "A": 1}}

    assert controller.after_call("terminal", args, '{"exit_code": 2}').action == "allow"
    warning = controller.after_call("terminal", args, '{"exit_code": 2}')

    metadata = warning.to_metadata()
    assert metadata["action"] == "warn"
    assert metadata["code"] == "repeated_exact_failure_warning"
    assert metadata["tool_name"] == "terminal"
    assert metadata["count"] == 2
    assert metadata["message"] == warning.message
    assert metadata["signature"]["tool_name"] == "terminal"
    assert len(metadata["signature"]["args_hash"]) == 64
    assert "exit 2" not in json.dumps(metadata)
    assert warning.allows_execution is True
    assert warning.should_halt is False

    synthetic = guardrails.toolguard_synthetic_result(warning)
    assert json.loads(synthetic)["guardrail"] == metadata
    assert guardrails.append_toolguard_guidance("raw result", warning).startswith("raw result\n\n[")


def test_classify_tool_failure_matches_terminal_memory_and_display_fallbacks():
    guardrails = _guardrails_module()
    classify_tool_failure = getattr(guardrails, "classify_tool_failure", None)

    assert classify_tool_failure is not None

    assert classify_tool_failure("terminal", '{"exit_code": 2}') == (True, " [exit 2]")
    assert classify_tool_failure("terminal", '{"exit_code": 0}') == (False, "")
    assert classify_tool_failure("terminal", "plain terminal output") == (False, "")
    assert classify_tool_failure(
        "memory",
        '{"success": false, "error": "memory would exceed the limit"}',
    ) == (True, " [full]")
    assert classify_tool_failure("read_file", "Error: permission denied") == (True, " [error]")
    assert classify_tool_failure("read_file", '{"failed": true}') == (True, " [error]")
    assert classify_tool_failure("write_file", '{"bytes_written": 12}') == (False, "")
    assert classify_tool_failure("patch", '{"success": true}') == (False, "")


def test_guardrail_tool_name_sets_match_hermes_exactly():
    guardrails = _guardrails_module()

    idempotent = getattr(
        guardrails,
        "IDEMPOTENT_TOOL_NAMES",
        getattr(guardrails, "IDEMPOTENT_TOOLS", None),
    )
    mutating = getattr(
        guardrails,
        "MUTATING_TOOL_NAMES",
        getattr(guardrails, "MUTATING_TOOLS", None),
    )

    assert idempotent == HERMES_IDEMPOTENT_TOOL_NAMES
    assert mutating == HERMES_MUTATING_TOOL_NAMES
