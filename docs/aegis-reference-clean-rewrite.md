# AEGIS Reference-Guided Clean Rewrite

This file tracks the AEGIS-first rewrite and polish pass. A local reference
agent tree is used only as a behavioral and architectural comparison source.
Do not copy reference code, comments, prompts, prose, or file text into AEGIS.

## Goal

- Keep AEGIS as the owned product and codebase.
- Study the reference tree to identify feature gaps and behavior patterns.
- Rebuild missing behavior in original AEGIS code, tests, prompts, and docs.
- Preserve a local audit trail proving what was read, compared, changed, and
  tested.

## Rules

- Read relevant AEGIS files before editing AEGIS.
- Read the matching reference files before claiming a behavior is covered.
- Prefer AEGIS names, layout, UX, config, prompts, and docs.
- Avoid line-by-line translation from reference projects.
- Keep generated comparison artifacts under `docs/audit/`; that directory is
  intentionally ignored because it includes local source paths and raw file
  inventories.

## Mechanical Inventory

The local audit script is:

```bash
python scripts/audit_reference_compare.py --aegis-root . --reference-root <local-reference-root>
```

Latest source-like file count from the first clean pass:

| Tree | Source-Like Files |
| --- | ---: |
| AEGIS | 958 |
| Reference | 5,459 |

## Current Rewrite Slices

| Area | Status | Notes |
| --- | --- | --- |
| Repository audit pipeline | completed first pass | Neutral inventory tooling exists; generated artifacts are local-only. |
| Top-level metadata | completed first pass | Added AEGIS MIT license metadata and startup bootstrap tests. |
| Installer stage protocol | completed first pass | Added manifest and JSON stage results for scripted/bootstrap installs. |
| CLI and onboarding | queued | Compare installer, setup wizard, auth selection, and recovery flows. |
| Agent loop | queued | Compare iteration budget, verification gates, retry, guardrails, and compaction. |
| Memory and learning | queued | Compare memory store, compaction, curator, insights, and self-improvement loops. |
| Skills and tools | queued | Curate useful capabilities into AEGIS-owned skill/tool modules. |
| Gateway and background services | queued | Compare long-running service model, jobs, channel delivery, and observability. |
| Tests | active | Keep product branding guard green while expanding parity checks. |

## Verification Log

- `python -m py_compile scripts/audit_reference_compare.py`
- `python scripts/audit_reference_compare.py --aegis-root . --reference-root <local-reference-root>`
- `python -m pytest -q tests/test_tui_ink.py`
- `python -m pytest -q tests/test_bootstrap.py tests/test_product_surfaces.py::test_cli_parser_exposes_upgrade_commands tests/test_product_surfaces.py::test_cli_help_command_prints_parser_help tests/test_packaging.py::test_agent_compatibility_paths_delegate_to_native_aegis_modules tests/test_installers.py::test_setup_compatibility_scripts_delegate_to_aegis_surfaces`
- `python -m pytest -q tests/test_installers.py tests/test_product_surfaces.py::test_tracked_text_does_not_use_upstream_branding`
