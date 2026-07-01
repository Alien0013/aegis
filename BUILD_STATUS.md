# AEGIS Build Status

Last updated: 2026-07-01

This is the master restart ledger for building AEGIS toward Hermes-agent
full-harness parity. Use this file before any future patching so the work stays
trackable by domain, percentage, evidence, and next patch lane.

## Mission

Build AEGIS into an owned, production-grade agent harness using Hermes-agent as
the behavior reference. Implementation, prompts, docs, names, tests, and UX stay
AEGIS-native.

This is not a "light" clone target. Each pass should move a complete core
harness behavior slice toward Hermes-level parity, with AEGIS-owned code,
focused regressions, and broad guardrails.

The full harness target includes the engine plus the surfaces around it:
agent loop, streaming, tool calling, approvals, sandboxing, config, state,
memory, compaction, skills, MCP/plugins, subagents, provider/auth routing,
gateway/server/channel adapters, CLI, TUI, dashboard/web UI, desktop/apps,
docs/packaging, and testable local runtime behavior.

## Hard Rules

- Map the full harness before patching. Do not jump into a feature without
  knowing which Hermes domain it belongs to and which AEGIS files own it.
- Read Hermes reference code directly before implementing each stage.
- Do not copy Hermes code or text. Match behavior with AEGIS-owned code.
- Do not touch Synth/synth paths.
- Do not port Nous portal or proprietary Nous product surfaces. Parity target is
  the local open harness behavior and AEGIS-owned equivalents.
- Dashboard, desktop, TUI, web, and packaging are in the full-harness map. They
  still need explicit lane selection before edits, because they have separate
  build/test risk.
- Preserve Claude/Anthropic thinking and cache behavior.
- Preserve existing user/worktree changes. Do not revert unrelated dirty files.
- Every completed stage needs focused tests or a documented reason why it cannot
  be tested yet.

## Reference Root

Hermes reference tree:

```text
/home/alienai/.hermes/hermes-agent
```

When avoiding interaction with the live/running Hermes checkout, use an
isolated read-only mirror for reference reads. Current mirror for this wave:

```text
/tmp/aegis-hermes-agent-reference
```

Regenerate that mirror if `/tmp` is cleared. Do not run the Hermes CLI, Hermes
tests, or Hermes server/session processes during AEGIS parity verification.

AEGIS tree:

```text
/home/alienai/Documents/personal/aegis
```

## Full-Harness Dashboard

### GitHub Baseline

Hermes GitHub remote:

```text
https://github.com/NousResearch/hermes-agent.git
```

Current GitHub `main` counted from `origin/main`:

```text
a653bb0cbeaaefc1e275b2e3408c3968011d1304
```

Local Hermes checkout used for line-specific code reading so far:

```text
0198713c3364f7a16603fa684e78671b1392941d
```

The local checkout is behind current GitHub `main`; line references in the
reading log are tied to the local checkout until the reference tree is
deliberately updated and re-read.

AEGIS numbers below use the current working tree file set from
`git ls-files --cached --others --exclude-standard`, so recently generated
source/tests count even before commit. Ignored caches, `.venv`, build outputs,
and `node_modules` excluded by Git are not counted.

Hermes current GitHub `main` inventory:

| Metric | Count |
| --- | ---: |
| Tracked files, all types | 5,890 |
| Text-counted files contributing LOC | 5,796 |
| Tracked text LOC | 2,130,181 |

### Top-Line Percentages

| View | Hermes files | Hermes LOC | AEGIS files | AEGIS LOC | File ratio | LOC ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full repo text, current GitHub baseline | 5,796 | 2,130,181 | 1,181 | 374,943 | 20.4% | 17.6% |
| Python all, local reference checkout | 2,713 | 1,273,751 | 508 | 207,439 | 18.7% | 16.3% |
| Python excluding tests, local reference checkout | 842 | 604,760 | 282 | 129,830 | 33.5% | 21.5% |
| Core runtime Python lens | 560 | 464,359 | 267 | 123,391 | 47.7% | 26.6% |

Interpretation:

- Raw full-harness size parity is about 17.6% by LOC.
- Core runtime Python size parity is about 26.6% by LOC.
- The remaining gap is still large: roughly 82.4% of full-repo LOC and 73% of
  core-runtime Python LOC, before judging behavior quality.
- Size is not behavior. A small AEGIS file can cover a Hermes behavior, and a
  large UI bundle can add LOC without closing engine parity.

### Domain Map

Rows are domain lenses, not additive totals. Some files intentionally appear in
more than one lens when behavior is cross-cutting, especially plugins, MCP, and
dashboard server code.

| Domain | Hermes files | Hermes LOC | AEGIS files | AEGIS LOC | LOC ratio | Parity confidence | Next lane |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Core agent runtime | 151 | 104,269 | 39 | 19,977 | 19.2% | 55% | Re-audit loop/prologue/finalization against latest Hermes checkout |
| Tools + sandbox + file ops | 112 | 89,698 | 43 | 16,334 | 18.2% | 45% | Finish write approval, then broader tool coverage beyond file/shell |
| Memory + learning + skills engine | 1,194 | 392,148 | 368 | 91,898 | 23.4% | 35% | Pending write approvals, background learning queues, skill breadth |
| Providers + auth + credentials | 20 | 19,964 | 12 | 7,597 | 38.1% | 45% | Provider edge cases, auth import/remove parity, Claude preservation |
| MCP + plugin protocol | 305 | 123,269 | 10 | 7,088 | 5.8% | 35% | Separate MCP core from plugin breadth; fill plugin loader/toolset gaps |
| Gateway + server + channels | 78 | 80,823 | 29 | 26,770 | 33.1% | 35% | Gateway replay/idempotency/channel lifecycle audit |
| CLI + terminal UX | 197 | 168,336 | 30 | 10,701 | 6.4% | 20% | Command breadth, pending-write review CLI, config/auth parity |
| TUI | 389 | 90,211 | 23 | 37,599 | 41.7% | 15% | Separate bundled/dist LOC from real source; audit setup/status flows |
| Dashboard + web UI | 816 | 300,755 | 101 | 33,601 | 11.2% | 10% | Dashboard/web parity later lane, including server routes and static UI |
| Desktop + apps | 819 | 163,053 | 156 | 25,336 | 15.5% | 10% | Desktop/app shell parity later lane |
| ACP/LSP/project adapters | 24 | 9,716 | 9 | 753 | 7.8% | 10% | ACP/project adapter audit and LSP behavior parity |
| Tests + eval harness | 1,872 | 668,138 | 211 | 70,789 | 10.6% | 25% | Expand domain regression suites, not only happy-path smoke tests |
| Docs + packaging + ops | 111 | 45,652 | 76 | 14,152 | 31.0% | 20% | Packaging/docs parity later, avoid release churn during engine work |

### Hermes Full-Harness Inventory

This is Hume the 2nd's independent no-edit map of Hermes `origin/main`
(`a653bb0cbeaaefc1e275b2e3408c3968011d1304`). It uses tracked files only;
binary assets count as files but contribute `0` LOC.

| Domain | Hermes dirs / files / LOC | Surface type |
| --- | --- | --- |
| Core agent | `run_agent.py`, `agent/conversation_loop.py`, `agent/lsp/`, `agent/pet/`, core `agent/*.py`; 96 files / 66,162 LOC | Behavior source |
| Tools | `tools/`, `model_tools.py`, `toolsets.py`, `toolset_distributions.py`; 94 files / 67,314 LOC | Behavior source |
| Memory/skills | `skills/`, `optional-skills/`, `plugins/memory/`, memory/skill agent + CLI files; 949 files / 312,350 LOC | Mixed behavior source and content corpus |
| MCP/plugins | `optional-mcps/`, plugins remainder, `mcp_serve.py`, MCP tools/CLI/plugin files; 134 files / 46,513 LOC | Extension behavior source |
| Providers/auth | provider adapters, `providers/`, model-provider/dashboard-auth plugins, auth/model/provider CLI files, credential/secrets files; 150 files / 67,193 LOC | Behavior source |
| Gateway/server/platform channels | `gateway/`, `acp_adapter/`, `acp_registry/`, `cron/`, platform/cron plugins, web server/pairing/send/webhook CLI files; 198 files / 180,253 LOC | Server/channel behavior and product surface |
| CLI | `cli.py`, `hermes_cli/` remainder, `locales/`, `hermes`; 145 files / 107,038 LOC | Product surface |
| TUI | `ui-tui/`, `tui_gateway/`; 283 files / 74,547 LOC | Product surface with bridge behavior |
| Dashboard/web UI | `web/`; 123 files / 43,549 LOC | Product surface |
| Desktop/apps | `apps/desktop/`, `apps/bootstrap-installer/`, `apps/shared/`; 672 files / 139,042 LOC | Product surface |
| Tests/evals | `tests/`, `*.test.*`, `__tests__/`, `scripts/tests/`, desktop/web/TUI/plugin tests, app eval script; 2,174 files / 711,989 LOC | Verification harness |
| Docs/packaging | `website/`, `docs/`, `.github/`, `docker/`, `nix/`, packaging, root metadata/locks/readmes/install scripts; 872 files / 314,218 LOC | Docs/distribution surface |

AEGIS owner directories by domain:

| Domain | AEGIS owner paths |
| --- | --- |
| Core agent | `aegis/agent/`, `aegis/session.py`, `aegis/background.py`, `aegis/checkpoints.py`, `aegis/tracing.py`, `aegis/usage_log.py` |
| Tools | `aegis/tools/`, `aegis/checkpoints.py`, `aegis/lsp/` |
| Memory/skills | `aegis/memory.py`, `aegis/memory_providers.py`, `aegis/learn.py`, `aegis/skills.py`, `aegis/curator.py`, `aegis/builtin_skills/`, `skills/`, `optional-skills/`, `plugins/` |
| MCP/plugins | `aegis/mcp/`, `aegis/tools/registry.py`, `aegis/tools/tool_result_storage.py`, `plugins/` |
| Providers/auth | `aegis/providers/`, `aegis/credentials.py`, `aegis/providers/auth.py` |
| Gateway/server/platform channels | `aegis/gateway/`, `aegis/platforms/`, `aegis/server.py`, `aegis/dashboard_fastapi.py`, `aegis/webhook.py`, `aegis/cron.py` |
| CLI | `aegis/cli/`, `hermes_cli/`, `install.sh`, `setup/` |
| TUI | `aegis/tui_ink/`, `ui-tui/`, `aegis/tui_gateway.py`, `aegis/cli/tui.py` |
| Dashboard/web UI | `aegis/dashboard.py`, `aegis/dashboard_fastapi.py`, `aegis/dashboard_routes/`, `aegis/static/`, `web/`, `site-next/`, `website/` |
| Desktop/apps | `desktop/`, `apps/`, `assets/`, `aegis/desktop_app/` |
| Tests/evals | `tests/`, `benchmarks/`, `examples/` |
| Docs/packaging | `docs/`, `.github/`, `pyproject.toml`, `README.md`, `RELEASING.md`, install/setup files |

### Current Active Work

| Lane | Owner | Files | Status |
| --- | --- | --- | --- |
| Full-harness map rebuild | main agent + Hume the 2nd | `BUILD_STATUS.md` | done for restart view |
| Durable write approval core | main agent | `aegis/write_approval.py`, `aegis/memory.py`, `aegis/config.py`, `aegis/tools/builtin.py` | Hermes `tools/write_approval.py` first-pass file contract done; focused staging/review tests green |
| Skill write approval integration | Archimedes the 2nd | `aegis/tools/skill_manage.py` | patched; compile/ruff and focused compatibility tests green |
| Write approval regressions | Helmholtz the 2nd | `tests/test_write_approval_stage_l.py` | landed; local and worker runs green |
| Pending write review/apply surface | main agent + Carson the 2nd | `aegis/write_approval_review.py`, `aegis/cli/main.py`, `aegis/cli/repl.py`, `tests/test_write_approval_review_stage_l.py` | done for first pass; approve/reject/diff/toggle tests green |
| Memory tool parity slice | Mill the 2nd + main agent | `aegis/memory.py`, `aegis/tools/builtin.py`, `tests/test_memory_behavior.py` | Hermes `tools/memory_tool.py` first-pass file contract done; batch/schema/replay tests green |
| Skill manager parity slice | Gibbs the 2nd + main agent | `aegis/tools/skill_manage.py`, `tests/test_skill_manage_parity.py`, `tests/test_skills_memory.py` | Hermes `tools/skill_manager_tool.py` first-pass file contract done; edit/remove_file/file_content/fuzzy/read-before-write/overwrite tests green |
| Skills prompt/catalog + preprocessing slice | main agent + Volta/Confucius the 2nd | `aegis/skills.py`, `aegis/skill_preprocessing.py`, `aegis/agent/agent.py`, `aegis/config.py`, `tests/test_skills_memory.py`, `tests/test_skill_preprocessing_parity.py` | Hermes `agent/prompt_builder.py` skills prompt cache/index, `agent/skill_utils.py`, `agent/skill_preprocessing.py`, and `agent/system_prompt.py` relevant ranges read first; category `DESCRIPTION.md` summaries, signature/snapshot invalidation, external-dir precedence, process-wide rendered prompt LRU, Hermes-compatible template vars, session-id propagation, and opt-in inline shell activation/preload behavior patched; focused skills tests green |
| System prompt active-profile safety slice | main agent | `aegis/agent/agent.py`, `tests/test_smoke.py` | Hermes `agent/system_prompt.py` full file read first; AEGIS runtime prompt now names the active profile/home and warns against cross-profile skills/plugins/cron/memory/config writes without explicit user direction |
| Agent init prompt-control/retry/Ollama/context-engine slice | main agent + Kepler/Galileo/Boyle the 2nd | `aegis/agent/context.py`, `aegis/agent/agent.py`, `aegis/agent/loop.py`, `aegis/agent/context_engine.py`, `aegis/config.py`, `aegis/model_meta.py`, `aegis/providers/base.py`, `aegis/providers/chat_completions.py`, `tests/test_agent_init_prompt_controls.py`, `tests/test_ollama_num_ctx.py`, `tests/test_context_engine_parity.py` | Hermes `agent/agent_init.py` full file plus `agent/system_prompt.py`, `agent/model_metadata.py`, `agent/transports/chat_completions.py`, and `plugins/context_engine/__init__.py` relevant ranges read first; AEGIS now honors prompt-control config, `agent.api_max_retries`, Ollama `num_ctx`, and context-engine alias/plugin/copy/tool lifecycle behavior |
| Agent init provider headers/request-overrides slice | main agent + Euler/Dewey the 2nd | `aegis/providers/base.py`, `aegis/providers/chat_completions.py`, `aegis/providers/responses.py`, `aegis/providers/anthropic.py`, `aegis/providers/registry.py`, `aegis/providers/auth.py`, `aegis/config.py`, `tests/test_provider_request_overrides.py`, `tests/test_providers.py` | Hermes `agent/agent_init.py`, `agent/transports/codex.py`, `agent/anthropic_adapter.py`, and provider header tests read first; AEGIS now carries `model.default_headers`, provider/custom `request_overrides`, custom-provider `extra_body`, Responses/Anthropic overrides, xAI Responses cache routing, Anthropic beta/TTL preservation, and canonical Codex `ChatGPT-Account-ID` casing |
| Tool executor deferred bridge slice | main agent | `aegis/agent/loop.py`, `tests/test_stage_u_deferred_bridge.py` | Hermes `agent/tool_executor.py` bridge unwrap contract patched: deferred `tool_call` resolves to underlying tool before middleware/policy/events/result naming |
| Tool dispatch helper landed-path slice | main agent | `aegis/agent/verification.py`, `aegis/agent/loop.py`, `tests/test_self_verify.py` | Hermes `agent/tool_dispatch_helpers.py` landed mutation metadata mapped into verify-after-edit tracking |
| Model tools dispatch/definition slice | main agent + Lovelace the 2nd + Hubble the 2nd | `aegis/tools/schema_validation.py`, `aegis/tools/registry.py`, `aegis/tools/async_bridge.py`, `aegis/tools/backends.py`, `aegis/tools/code_exec.py`, `aegis/mcp/server.py`, `aegis/agent/loop.py`, `aegis/agent/agent.py`, `aegis/config.py`, `aegis/plugins.py`, `aegis/hooks.py`, deferred/schema/executor/product tests | Hermes `model_tools.py` schema-driven argument coercion, sanitized tool-exception framing, automatic MCP/plugin progressive-disclosure threshold, memoized quiet schema-list invalidation, structured tool plugin hook/transform dispatch, and persistent sync-to-async tool loop bridge patched into AEGIS dispatch/tool-definition flow |
| Codex app-server provider/runtime slice | main agent + Heisenberg the 2nd | `aegis/providers/codex_app_server.py`, `aegis/providers/codex_runtime_migration.py`, `aegis/mcp/aegis_tools_mcp_server.py`, `aegis/cli/codex_runtime_plugin_migration.py`, `aegis/mcp/server.py`, `aegis/agent/loop.py`, `aegis/config.py`, `tests/test_codex_app_server_parity.py`, `tests/test_stage_z_codex_app_server.py`, `tests/test_codex_runtime_mcp_callback_migration.py`, provider/runtime tests | Hermes `agent/transports/codex_app_server.py`, `codex_app_server_session.py`, `codex_event_projector.py`, `hermes_tools_mcp_server.py`, and `hermes_cli/codex_runtime_plugin_migration.py` read first; event projection, token-usage metadata, approval mapping, MCP elicitation, file-change approval summaries, turn-aborted marker, post-tool quiet watchdog, dynamic inline tool request queueing, canonical session-history splicing, and first-pass live `aegis-tools` MCP server/config bootstrap patched into AEGIS provider runtime. Native Codex plugin discovery is implemented as a migration helper, but provider startup keeps `discover_plugins=False` by default to avoid extra subprocess work. |
| Toolset resolution slice | main agent + Arendt the 2nd + Turing the 2nd + Ohm the 2nd + Pauli the 2nd + Kant the 2nd | `aegis/tools/registry.py`, leaf tool owner files, `tests/test_toolset_resolution.py` | Hermes `toolsets.py` default/all/star alias, core-protected dynamic toolsets, static distribution aliases, platform bundle aliases, display aliases, disabled-toolset filtering, recursive expansion, safe/minimal/image concrete leaf membership, `toolset_distributions.py` sampling/list/get/validate/print helpers, and dynamic `hermes-<platform>` expansion patched into registry filtering |
| Deferred tool search slice | main agent + Sagan the 2nd | `aegis/tools/devtools.py`, `tests/test_stage_u_deferred_bridge.py`, `tests/test_deferred_tools.py` | Hermes `tools/tool_search.py` ranking, limit, parameter-name matching, source metadata, scoped catalog search, and JSON result content patched into AEGIS bridge search |
| Provider schema sanitizer metadata slice | main agent | `aegis/providers/schema.py`, `tests/test_stage_u_schema_sanitizer.py`, provider wire tests | Hermes `tools/schema_sanitizer.py` metadata behavior patched: `examples`, annotations, and validation hints are preserved while provider-boundary dialect identifiers are stripped |
| Message sanitization + turn repair slice | Zeno the 2nd + Raman the 2nd + main agent | `aegis/agent/response_normalization.py`, `aegis/agent/governance.py`, `tests/test_response_normalization_stage_h.py`, `tests/test_turn_finalization_stage_r.py` | Hermes `agent/message_sanitization.py` malformed raw tool-call repair and interrupted tool-tail closure mapped into AEGIS normalization/governance while preserving Claude/Anthropic thinking blocks |
| Tool guardrail structured slice | Socrates the 2nd + Erdos the 2nd + main agent | `aegis/agent/guardrails.py`, `tests/test_guardrails_parity.py`, `tests/test_resilience.py`, `tests/test_upgrades_batch.py` | Hermes `agent/tool_guardrails.py` + `agent/tool_result_classification.py` structured config/signature/decision/controller metadata, exact tool-name sets, terminal/memory/display failure classification, canonical JSON no-progress hashing, and landed file-mutation detection patched |
| MCP startup discovery slice | main agent | `aegis/mcp/startup.py`, `aegis/mcp/client.py`, `aegis/mcp/__init__.py`, `aegis/config.py`, `tests/test_stage_z_mcp_startup.py` | Hermes `hermes_cli/mcp_startup.py` bounded process-shared background discovery helper mapped into AEGIS MCP startup primitives |
| MCP runtime toolset slice | James the 2nd + Kierkegaard the 2nd + main agent | `aegis/mcp/client.py`, `aegis/tools/registry.py`, `aegis/agent/loop.py`, MCP catalog/lifecycle/executor tests | Hermes `tools/mcp_tool.py` per-server MCP toolsets, utility refresh, initialize capability gates, exact MCP provenance, and `supports_parallel_tool_calls` executor behavior mapped into AEGIS MCP runtime |
| MCP CLI lifecycle/security slice | Leibniz the 2nd + Lagrange the 2nd + Russell the 2nd + Meitner the 2nd + main agent | `aegis/cli/main.py`, `tests/test_mcp_cli.py` | Hermes `hermes_cli/mcp_config.py` + `hermes_cli/mcp_security.py` `configure`, add-time offline save/preset/env/security validation, suspicious existing-entry rejection, tool-checklist, list, remove, test, OAuth login/reauth behavior mapped into AEGIS CLI |
| Todo/session planning slice | main agent | `aegis/tools/builtin.py`, `aegis/agent/agent.py`, `tests/test_tools.py` | Hermes `tools/todo_tool.py` read/write/merge/bounded todo behavior and active-list prompt preservation mapped into AEGIS `todo_write`/`todo` surface |
| Tool output limits slice | main agent | `aegis/tools/builtin.py`, `aegis/cli/main.py`, `tests/test_tools.py`, `tests/test_config_cli.py` | Hermes `tools/tool_output_limits.py` `tool_output.max_bytes/max_lines/max_line_length` config names accepted and mapped to AEGIS output/read limits |
| Gateway command browser slice | Plato the 2nd | `aegis/gateway/runner.py`, `tests/test_gateway_commands.py` | Hermes `gateway/slash_commands.py` `/commands` browse/pagination behavior patched and fully gateway-command tested |
| CLI slash prefix dispatch slice | Anscombe the 2nd | `aegis/cli/repl.py`, `tests/test_cli_slash_dispatch.py` | Hermes `hermes_cli/commands.py` unique-prefix dispatch behavior patched and focused CLI/product tests green |

File-by-file Hermes parity pass:

| Hermes file | Hermes LOC | AEGIS owner file(s) | AEGIS LOC | First-pass result | Verification |
| --- | ---: | --- | ---: | --- | --- |
| `tools/write_approval.py` | 493 | `aegis/write_approval.py`; review surface in `aegis/write_approval_review.py` | 376 core / 639 focused tests | done: gate-off default preserved, staged pending JSON includes `action`, foreground/background origin inference, `GateDecision.allow/stage/message` aliases, memory inline/stage/block path, skill staging gist/diff helpers | `11 passed` write-approval suite; `6 passed` adjacent memory/skills suite; compile/ruff/diff-check clean |
| `hermes_cli/write_approval_commands.py` | 209 | `aegis/write_approval_review.py`; CLI/REPL/gateway wiring in `aegis/cli/main.py`, `aegis/cli/repl.py`, `aegis/gateway/runner.py` | 241 handler / 340 review tests / 2 gateway tests | done: bare state+pending, pending list with `[auto]`, approve/apply all-or-id, reject/deny/drop all-or-id, skills diff, approval/mode on/off/status, slash-command usage text, gateway memory approve via on-disk store, gateway skills pending/diff/approve create | `11 passed` review suite; `2 passed` gateway review controls; compile/ruff clean |
| `tools/memory_tool.py` | 1,146 | `aegis/memory.py`; model-visible schema in `aegis/tools/builtin.py` | 1,201 memory core / 1,433 builtin host / 1,194 focused memory test LOC | done: atomic `operations` batch (`aegis/memory.py:279`), batch gate/stage/replay payloads (`aegis/memory.py:975`), batch `handle_tool` path (`aegis/memory.py:1076`), recoverable missing `old_text`, gateway/on-disk approval replay, model-visible `operations` schema (`aegis/tools/builtin.py:1304`) | `49 passed` memory behavior/lifecycle; `3 passed` memory write-approval replay/gateway controls; compile/ruff/diff-check clean |
| `tools/skill_manager_tool.py` | 1,542 | `aegis/tools/skill_manage.py` | 942 skill manager / 263 focused parity test LOC | done for first pass: `edit`, `remove_file`, `file_content`, duplicate-create refusal, approval staging/replay, fuzzy patch recovery, background-review read-before-write marks (`aegis/tools/skill_manage.py:29`, `:94`), support-file overwrite, delete/archive validation, schema/action parity (`aegis/tools/skill_manage.py:865`) | `9 passed` skill-manager parity slice; included in `69 passed` combined write/memory/skills suite; compile/ruff/diff-check clean |
| `agent/prompt_builder.py` skills prompt cache/index + `agent/skill_utils.py` relevant discovery helpers + `agent/skill_preprocessing.py` | 1,971 + 767 + 144 | `aegis/skills.py`; `aegis/skill_preprocessing.py`; session-id wiring in `aegis/agent/agent.py`; config defaults in `aegis/config.py`; focused tests in `tests/test_skills_memory.py`, `tests/test_skill_preprocessing_parity.py` | 1,116 skills engine / 103 preprocessing / 88 preprocessing tests / 1,075 skills-memory tests | updated prompt/catalog/preprocessing slice: AEGIS now scans category-level `DESCRIPTION.md` files alongside `SKILL.md`, includes them in the discovery signature, persists `category_descriptions` in the disk prompt snapshot, invalidates rendered prompts when descriptions change, renders category summaries before visible skills, preserves higher-precedence local category summaries over external configured skill paths, and adds a process-wide 8-entry rendered skills-index LRU keyed by discovery signature, config filters, compact categories, runtime env/bin gates, and category metadata. Existing focus-mode category demotion still keeps all skill names visible. Latest preprocessing pass keeps AEGIS-native placeholders while accepting Hermes-style `${HERMES_SKILL_DIR}`/`${HERMES_SESSION_ID}`, passes the active session id into activation/slash/preload skill bodies, leaves unresolved placeholders visible, preserves default-off inline shell expansion, and runs opt-in inline snippets with the skill directory as cwd. | `4 passed` focused category-description slice; `5 passed` focused preprocessing slice; `49 passed, 59 deselected` integrated skill slice; py_compile/ruff/diff-check clean |
| `agent/system_prompt.py` | 536 | Runtime/system prompt assembly in `aegis/agent/agent.py`; prompt metadata tests in `tests/test_smoke.py` | 1,755 agent / 639 smoke tests | updated active-profile safety slice: AEGIS already had Hermes-style stable/context/volatile tiers, stored nonvolatile prompt reuse, prompt-part metadata, skills fingerprints, and provider-wire volatile context. Latest pass adds a stable runtime prompt line naming the active runtime profile and profile home, plus a profile-isolation warning not to modify another profile's skills, plugins, cron jobs, memories, or config without explicit user direction. This is harness safety only; Nous portal/proprietary product surfaces remain out of scope. | `2 passed` focused active-profile prompt tests; `8 passed, 24 deselected` prompt/cache smoke slice; py_compile/ruff/diff-check clean |
| `agent/agent_init.py` prompt controls/API retry/Ollama `num_ctx` + `agent/system_prompt.py` control callsites + `agent/model_metadata.py` Ollama helpers + `agent/transports/chat_completions.py` overrides | 1,888 + relevant system prompt `64-110`, `160-250`, `332-408` + metadata `499`, `1234` + transport `450`, `564` | Prompt-control assembly in `aegis/agent/context.py`; init/config/Ollama wiring in `aegis/agent/agent.py`, `aegis/config.py`, and `aegis/model_meta.py`; retry loop in `aegis/agent/loop.py`; request override passthrough in `aegis/providers/base.py` and `aegis/providers/chat_completions.py`; focused tests in `tests/test_agent_init_prompt_controls.py`, `tests/test_ollama_num_ctx.py` | 498 context / 1,847 agent / 2,728 loop / 1,202 config / 365 model metadata / 217 provider base / 319 chat transport / 228 prompt-control tests / 212 Ollama tests | updated prompt-control/retry/Ollama slice: AEGIS now splits the monolithic agentic prompt into tool-use enforcement, verification, task-completion, and parallel-tool-call blocks; defaults preserve AEGIS' existing behavior while `agent.tool_use_enforcement` supports `auto`/true/false/list-style model substring control, `agent.task_completion_guidance=false` removes finish-the-job guidance, `agent.parallel_tool_call_guidance=false` removes batching guidance, and `agent.environment_probe=false` suppresses provider-wire environment context. `platform_hints` now supports Hermes-style bare-string append, `{append: ...}`, and `{replace: ...}` without leaking overrides across platforms. AEGIS also ports Hermes' init-time `agent.api_max_retries` total-attempt control: default 3, min 1, invalid config fallback 3, retry only for classifier `retry` recoveries, with retrying trace spans and `api_retry` events. Latest Ollama pass ports the Hermes local-endpoint helper, `/api/show` `num_ctx` detection, explicit Modelfile `num_ctx` priority over GGUF `model_info.*context_length`, `model.context_length` capping for auto-detected values, explicit `model.ollama_num_ctx` override behavior, provider-level `request_overrides.extra_body.options.num_ctx`, and chat-completions payload merge without leaking an `extra_body` field (`aegis/agent/context.py:73`, `:187`, `:215`, `:241`, `:418`, `:440`, `:452`; `aegis/agent/agent.py:115`, `:154`, `:305`, `:312`, `:650`; `aegis/agent/loop.py:1786`, `:2161`; `aegis/config.py:396`, `:411`, `:415`, `:748`; `aegis/model_meta.py:116`, `:255`; `aegis/providers/base.py:130`; `aegis/providers/chat_completions.py:36`, `:116`, `:143`; `tests/test_agent_init_prompt_controls.py:71`, `:168`; `tests/test_ollama_num_ctx.py:45`, `:119`, `:147`, `:168`). Remaining `agent_init.py` init lane: cosmetic startup/status banners and context warning prints. | `14 passed` focused prompt/retry/Ollama suite; `28 passed` retry/reliability/overflow/thinking/Ollama/prompt suite; `5 passed, 27 deselected` smoke prompt slice; py_compile/ruff/diff-check clean |
| `agent/agent_init.py` context-engine selection/lifecycle + `plugins/context_engine/__init__.py` loader | relevant agent-init `1440-1760` + plugin loader 285 | Context-engine resolver in `aegis/agent/context_engine.py`; init tool registration/session-start hook in `aegis/agent/agent.py`; focused tests in `tests/test_context_engine_parity.py` | 305 context-engine / 1,847 agent / 227 focused tests | updated context-engine slice: AEGIS now accepts Hermes-style `context.engine` when `agent.context_engine` is default/compressor, treats `compressor` as the built-in default engine alias, deep-copies registered engine instances to prevent mutable state leaking between agents, loads context-engine plugin directories from configured/user/project roots, supports plugin-style `register(ctx)` and class fallback loading, skips duplicate context-engine tool names without poisoning the registry rejection log, records `_context_engine_tool_names` only for newly visible tools, and passes richer `on_session_start` metadata (`session_id`, `aegis_home`, `platform`, `model`, `context_length`, `conversation_id`) while preserving older one-argument hooks (`aegis/agent/context_engine.py:103`, `:130`, `:146`, `:167`, `:188`, `:231`, `:246`, `:267`; `aegis/agent/agent.py:282`, `:286`, `:366`; `tests/test_context_engine_parity.py:37`, `:58`, `:77`, `:135`, `:187`). Remaining `agent_init.py` init lane: cosmetic startup/status banners and context warning prints. | `7 passed` focused context-engine parity suite; `11 passed` combined context-engine/terminal-compress/compaction slice; py_compile/ruff/diff-check clean |
| `agent/agent_init.py` provider default headers/request overrides + `agent/transports/codex.py` Responses kwargs + `agent/anthropic_adapter.py` beta/cache behavior + Codex Cloudflare header tests | relevant agent-init `147-163`, `824-975`, `1021-1093`, `1815-1845` + codex transport `240-385` + Anthropic adapter `338-386`, `680-835` | Provider contract and transports in `aegis/providers/base.py`, `aegis/providers/chat_completions.py`, `aegis/providers/responses.py`, `aegis/providers/anthropic.py`; provider resolution in `aegis/providers/registry.py`; Codex account headers in `aegis/providers/auth.py`; config defaults in `aegis/config.py`; focused tests in `tests/test_provider_request_overrides.py` and `tests/test_providers.py` | 217 provider base / 319 chat transport / 580 Responses transport / 341 Anthropic transport / 1,534 registry / 1,873 auth / 1,202 config / 386 focused tests | updated provider-startup/header slice: AEGIS now treats `request_overrides` as a provider-level contract, exposes shared request override helpers for payload merge, `extra_headers`, and per-request timeout, accepts `model.default_headers` and `model.request_overrides`, preserves custom-provider `extra_body`/`extra_headers`, merges caller overrides after provider defaults, forwards overrides only to transports that accept them, applies overrides to chat-completions, Responses, and Anthropic without leaking `extra_body`/`extra_headers` into request JSON, strips `service_tier` for xAI even when it arrives through overrides, adds xAI Responses cache routing with `x-grok-conv-id` and stable `prompt_cache_key`, validates Anthropic cache TTL to Hermes' `5m`/`1h` set, carries Anthropic common/OAuth beta headers plus Claude Code OAuth identity headers, and uses canonical `ChatGPT-Account-ID` casing for Codex direct/OAuth account attribution (`aegis/providers/chat_completions.py:36`, `:44`, `:53`, `:66`, `:162`, `:196`; `aegis/providers/responses.py:49`, `:158`, `:239`, `:240`; `aegis/providers/anthropic.py:31`, `:155`, `:167`, `:217`; `aegis/providers/registry.py:64`, `:84`, `:373`, `:416`, `:835`; `aegis/providers/auth.py:931`, `:1138`; `tests/test_provider_request_overrides.py:9`, `:104`, `:193`, `:271`, `:331`). Remaining bounded provider/init gaps: cosmetic startup/status banners, context-limit warning prints, and any deeper provider-specific live edge cases not covered by this header/override slice. | `8 passed` focused provider override suite; `29 passed` provider/header slice; `49 passed` combined provider/prompt/Ollama/context/auth slice; py_compile/ruff/diff-check clean |
| `agent/tool_executor.py` | 1,539 | `aegis/agent/loop.py`; deferred bridge tests in `tests/test_stage_u_deferred_bridge.py` | 2,402 loop / 219 deferred bridge tests / 173 executor tests | updated first-pass: `tool_call` is now treated as a transport envelope like Hermes. Valid deferred calls resolve to the underlying tool before request middleware, guard/permission checks, tool events, untrusted wrapping, subdirectory hints, result spilling, and tool-message naming (`aegis/agent/loop.py:87`, `:913`, `:1204`). Out-of-scope deferred calls are blocked without dispatching the target, while preserving the original model tool-call id. | `16 passed` deferred/tool-executor slice; included in `34 passed` schema/executor/deferred slice; py_compile/ruff/diff-check clean for touched files |
| `agent/tool_dispatch_helpers.py` | 448 | `aegis/agent/verification.py`; executor callsite in `aegis/agent/loop.py` | 316 verification / 2,415 loop / 209 self-verify tests / 209 executor tests | updated first-pass: verify-after-edit now prefers landed mutation metadata from tool results (`files_modified`, `resolved_path`, `path`, `file`) before falling back to requested arguments (`aegis/agent/verification.py:131`, `:148`, `:258`; `aegis/agent/loop.py:1014`). Executor untrusted-result wrapping now preserves AEGIS' broader network-group coverage while adding Hermes' explicit data-not-instructions framing and re-entry guard. Latest dispatch update ports Hermes' MCP batch gate: `mcp__...` calls now run in parallel only when the active MCP manager marks the exact tool provenance as parallel-safe, and otherwise fail closed (`aegis/agent/loop.py:1178`, `:1209`; `tests/test_tool_executor_stage_j.py:49`). | included in `89 passed` integrated MCP/toolset/deferred/executor wave; focused MCP executor/provenance checks `2 passed`; py_compile/ruff/diff-check clean |
| `model_tools.py` | 1,259 | `aegis/tools/schema_validation.py`; registry generation in `aegis/tools/registry.py`; persistent async bridge in `aegis/tools/async_bridge.py`; backend awaitable helper in `aegis/tools/backends.py`; direct-dispatch surfaces in `aegis/tools/code_exec.py` and `aegis/mcp/server.py`; executor integration in `aegis/agent/loop.py`; plugin hook helpers in `aegis/plugins.py`; shell hook event names in `aegis/hooks.py`; model-visible schema selection in `aegis/agent/agent.py`; config defaults in `aegis/config.py`; focused tests in `tests/test_tool_schema_validation.py`, `tests/test_tool_executor_stage_j.py`, `tests/test_stage_u_deferred_bridge.py`, `tests/test_hardening.py`, `tests/test_smoke.py`, `tests/test_product_surfaces.py` | 341 schema-validation / 906 registry / 104 async bridge / 2,922 backends / 177 execute-code / 167 MCP server / 2,614 loop / 1,807 plugins / 202 hooks / 1,734 agent / 1,187 config / 378 schema tests / 522 executor tests / 365 deferred bridge tests / 227 hardening tests / 620 smoke tests / 3,983 product-surface tests | updated dispatch/schema/async slice: schema-driven argument coercion runs before request middleware/guard/permission/events/tool execution, tool exceptions are sanitized before model-visible result context, progressive-disclosure assembly defers large MCP/plugin schema surfaces behind `tool_search`/`tool_describe`/`tool_call`, registry generation/cache invalidation mirrors Hermes quiet schema-list behavior, structured `pre_tool_call`/`post_tool_call` observers and `transform_tool_result` run through the plugin flow, and AEGIS now ports Hermes `_run_async` behavior through `run_sync_awaitable`: one persistent main/CLI event loop, one persistent per-worker-thread event loop, isolated worker-thread execution when already inside a running event loop, context propagation, timeout cancellation, and no `asyncio.run()` create/close cycle for tool awaitables. The shared bridge is used by normal executor tool dispatch, async `tool_execution` middleware, backend helpers, `execute_code` RPC dispatch, and built-in MCP stdio `tools/call` dispatch. Remaining `model_tools.py` async gap: none found in this bounded pass; provider/Codex-app-server runtime-specific event/request handling is now tracked in the dedicated transport row below. | `107 passed` integrated MCP/toolset/deferred/todo/output/executor/schema/plugin-hook/async-entry wave; focused async bridge entry-point checks `15 passed`; backend/storage handoff checks `15 passed`; py_compile/ruff/diff-check clean |
| `agent/transports/codex_app_server.py` + `agent/transports/codex_app_server_session.py` + `agent/transports/codex_event_projector.py` + `agent/transports/hermes_tools_mcp_server.py` | 400 + 876 + 312 + 233 | `aegis/providers/codex_app_server.py`; `aegis/providers/codex_runtime_migration.py`; `aegis/mcp/aegis_tools_mcp_server.py`; `aegis/mcp/server.py`; canonical history splice in `aegis/agent/loop.py`; config defaults in `aegis/config.py`; focused tests in `tests/test_codex_app_server_parity.py`, `tests/test_stage_z_codex_app_server.py`, `tests/test_codex_runtime_mcp_callback_migration.py`; adjacent provider tests in `tests/test_providers.py` | 875 Codex provider / 481 migration / 103 aegis-tools MCP server / 172 MCP server / 2,697 loop / 1,193 config / 284 parity tests / 309 Stage Z tests / 226 callback-migration tests / 2,179 provider tests | updated provider-runtime slice: AEGIS now projects Codex `item/completed` command/file-change/MCP/dynamic-tool/user/assistant/reasoning events into `raw.projected_messages` with deterministic call ids, splices those projected messages into canonical AEGIS session history, records tool-iteration counts, captures `thread/tokenUsage/updated` last/total/context-window metadata, queues dynamic inline tool requests off the provider pump, handles Hermes/Aegis tools MCP elicitation accept and third-party decline, declines permission-escalation requests, maps approval choices to `accept`/`acceptForSession`/`decline`, caches `fileChange` `item/started` summaries for apply-patch approval prompts, treats `<turn_aborted>` markers as terminal interrupted responses, interrupts/retires on post-tool silence, and interrupts/retires on outer turn timeout. Latest callback/config lane adds fail-open provider startup migration, idempotent managed Codex `config.toml` updates with user TOML preservation and atomic writes, AEGIS `mcp.servers` to Codex `mcp_servers` translation, default permission entry, managed `[mcp_servers.aegis-tools]`, a curated stateless `aegis-tools` callback MCP server that excludes shell/file/agent-loop tools while exposing non-Codex web/browser/vision/image/skills/TTS/kanban surfaces, and generic MCP server visibility/name controls without changing default server behavior. Remaining bounded differences: full live Codex runtime smoke is still later; native Codex plugin discovery exists as a helper but provider migration defaults `discover_plugins=False` to avoid extra subprocesses during normal provider startup. | `4 passed` callback migration slice; `14 passed, 59 deselected` Codex app-server/provider slice; `127 passed` integrated Codex/MCP/provider/runtime sweep; py_compile/ruff clean for touched production/test files |
| `hermes_cli/codex_runtime_plugin_migration.py` | 757 | `aegis/providers/codex_runtime_migration.py`; CLI-facing re-export in `aegis/cli/codex_runtime_plugin_migration.py`; provider callsite in `aegis/providers/codex_app_server.py`; config defaults in `aegis/config.py`; focused tests in `tests/test_codex_runtime_mcp_callback_migration.py` | 481 migration / 23 CLI re-export / 875 Codex provider / 1,193 config / 226 focused tests | done first-pass for provider/runtime bootstrap: AEGIS has an idempotent managed Codex config migration with stable managed block behavior, user TOML preservation, atomic write, AEGIS MCP server translation, `aegis-tools` callback server registration, default permission migration, metadata-driven provider invocation, and fail-open handling of local migration failures. Native Codex plugin discovery/query support is implemented in the helper path, but the provider path intentionally leaves discovery off by default (`discover_plugins=False`) so normal Codex app-server startup does not spawn extra subprocesses. Remaining bounded difference: CLI command wiring for an explicit migration/discovery command may still be a later surface if not already tracked elsewhere. | `4 passed` focused callback migration slice; included in `127 passed` integrated Codex/MCP/provider/runtime sweep; py_compile/ruff clean |
| `toolsets.py` + `toolset_distributions.py` | 941 + 358 | `aegis/tools/registry.py`; Hermes leaf memberships in `aegis/tools/builtin.py`, `aegis/tools/extra_builtin.py`, `aegis/tools/process.py`, `aegis/tools/code_exec.py`, `aegis/tools/agentic.py`, `aegis/tools/cronjob_tool.py`, `aegis/tools/recall.py`, `aegis/tools/skill_manage.py`; focused tests in `tests/test_toolset_resolution.py` | 1,180 registry / 1,578 builtin / 494 extra builtin / 216 process / 178 execute-code / 1,103 agentic / 503 cronjob / 236 recall / 943 skill manager / 449 focused tests | updated distribution/leaf slice: registry toolset filtering resolves `default` and `guided/default` to core, expands `all`/`*` to all registered dynamic toolsets while keeping core protected, preserves stable de-duplication, exposes registered toolset names, maps Hermes static distributions and platform bundles, recursively expands composed bundles such as `hermes-gateway`, keeps display aliases for plugin/dynamic toolsets, cleans aliases when the last target tool is removed, separates disabled toolsets from per-tool deny lists, preserves shared AEGIS core tools when disabling `hermes-*` bundles, and keeps `mcp` selection compatible with dynamic per-server `mcp-*` toolsets. Latest pass adds Hermes `toolset_distributions.py` records/get/list/validate/sample/print helpers, raw `get_distribution()` object behavior, shallow `list_distributions()` copy behavior, independent probability sampling, raw highest-probability fallback, unknown-distribution `ValueError`, multi-toolset leaf memberships, nested concrete `image_gen` leaf handling, and dynamic `hermes-<platform>` expansion when the platform suffix is a registered toolset (`aegis/tools/registry.py:29`, `:50`, `:324`, `:333`, `:342`, `:371`, `:407`, `:481`; `aegis/tools/builtin.py:245`, `:326`, `:612`, `:786`, `:1180`; `aegis/tools/extra_builtin.py:108`, `:387`; `aegis/tools/agentic.py:992`; `tests/test_toolset_resolution.py:342`, `:365`, `:377`, `:386`, `:392`, `:413`, `:422`). Remaining bounded difference: AEGIS intentionally keeps runtime `default`/`guided/default` as core aliases for child-scope compatibility rather than treating them as the Hermes data-generation `default` distribution; print formatting is ASCII but covers the same distribution fields. | `26 passed` focused toolset suite; `44 passed` adjacent registry/schema/MCP/subagent/tool CLI sweep; `118 passed` integrated current wave; py_compile/ruff/diff-check clean |
| `tools/tool_search.py` | 735 | `aegis/tools/devtools.py`; deferred bridge tests in `tests/test_stage_u_deferred_bridge.py`, `tests/test_deferred_tools.py` | 362 devtools / 365 deferred bridge tests / 71 deferred tests | updated JSON-catalog slice: AEGIS bridge search now accepts a bounded `limit`, searches only the session-scoped deferred catalog, searches tool name words/descriptions/top-level parameter names, ranks hits with a BM25-ish score, validates blank queries as tool errors, keeps direct/core protection, returns Hermes-style JSON content and structured `ToolResult.data` with `query`, deferred-catalog `total_available`, and per-hit `name`/`source`/`source_name`/`description`; AEGIS additionally records session-sticky `activated` tool names and loaded `schemas` in that JSON so the next model call receives full schemas without prose scraping (`aegis/tools/devtools.py:97`, `:105`, `:126`, `:151`, `:172`, `:185`, `:235`, `:256`, `:272`, `:292`; `tests/test_stage_u_deferred_bridge.py:108`, `:141`; `tests/test_deferred_tools.py:38`). Devtools-specific gap: none found in this pass; async worker-loop parity is now tracked and closed under the `model_tools.py` bridge row. | `32 passed` deferred/cache/toolset slice; included in `107 passed` integrated wave; py_compile/ruff/diff-check clean |
| `tools/schema_sanitizer.py` | 483 | `aegis/providers/schema.py`; focused tests in `tests/test_stage_u_schema_sanitizer.py`, provider/net schema tests | 362 provider schema / 225 focused schema tests | updated metadata slice: provider sanitizer now preserves `examples` as literal schema metadata instead of treating example strings as nested schema nodes, carries nullable-union metadata forward, and preserves Hermes-style annotation/validation hints including `exclusiveMinimum`, `exclusiveMaximum`, `readOnly`, `writeOnly`, and `deprecated` while still stripping provider-boundary dialect identifiers `$schema`, `$id`, and `$comment` (`aegis/providers/schema.py:17`, `:255`; `tests/test_stage_u_schema_sanitizer.py:33`, `:86`; `tests/test_providers.py:252`; `tests/test_net_and_edit.py:169`). Remaining gaps: exact per-provider live smoke coverage and any future provider-specific strict-mode exceptions remain later work. | `11 passed` schema sanitizer/provider wire slice; py_compile/ruff/diff-check clean |
| `tools/todo_tool.py` | 325 | `aegis/tools/builtin.py`; active-list prompt preservation in `aegis/agent/agent.py`; focused tests in `tests/test_tools.py` | 1,570 builtin host / 1,584 agent / 1,610 tool tests | updated first-pass: `todo_write` and alias `todo` now support no-argument read, replace writes, JSON-string todo coercion, merge-by-id updates, `cancelled` status, duplicate-id collapse, bounded item count/content, structured JSON response with summary counts, and active pending/in-progress todo injection into rebuilt runtime prompts after compaction/session prompt refresh (`aegis/tools/builtin.py:1258`, `:1278`, `:1314`, `:1333`, `:1350`, `:1371`; `aegis/agent/agent.py:288`; `tests/test_tools.py:386`, `:406`, `:441`). Remaining gaps: AEGIS still keeps the tool name `todo_write` for existing prompts and exposes Hermes `todo` via alias rather than replacing the canonical name. | included in `79 passed` integrated wave; focused todo/schema alias checks green; py_compile/ruff/diff-check clean |
| `tools/tool_output_limits.py` | 110 | `aegis/tools/builtin.py`; CLI key acceptance in `aegis/cli/main.py`; focused tests in `tests/test_tools.py`, `tests/test_config_cli.py` | 1,570 builtin host / 6,029 CLI / 1,610 tool tests / 454 config tests | updated first-pass: AEGIS accepts Hermes-style `tool_output.max_bytes`, `tool_output.max_lines`, and `tool_output.max_line_length`; the aliases drive direct output truncation, read-file line-window clamping, and per-line truncation while preserving existing AEGIS `tools.*` keys. `aegis config set tool_output.max_bytes ...` is accepted without `--force` (`aegis/tools/builtin.py:66`, `:74`, `:82`; `aegis/cli/main.py:2995`; `tests/test_tools.py:46`, `:69`; `tests/test_config_cli.py:283`). Remaining gaps: AEGIS keeps its broader spill-to-disk/result-storage layer in `aegis/tools/tool_result_storage.py`; exact Hermes cache-reset helper names are not duplicated. | included in `79 passed` integrated wave; focused output-limit tests green; py_compile/ruff/diff-check clean |
| `agent/message_sanitization.py` + `agent/turn_finalizer.py` | 477 + 260 | `aegis/agent/response_normalization.py`; turn repair in `aegis/agent/governance.py`; focused tests in `tests/test_response_normalization_stage_h.py`, loop/thinking/finalization regressions | 314 response normalization / 286 governance / 960 focused test LOC | updated turn-repair slice: response normalization repairs provider-preserved raw tool-call argument sentinels (`{"__raw__": ...}`) for empty/`None`, strict-false JSON, trailing commas, truncated braces/brackets, excess closers, and literal control characters; it copies `ToolCall` objects rather than mutating provider responses and keeps structured reasoning/Anthropic thinking blocks intact. Governance now backfills missing tool results, closes repaired/interrupted `tool -> user` tails with synthetic `assistant("Operation interrupted.")`, leaves active tool tails open, normalizes malformed tool-call ids/names/arguments, drops invalid roles, and preserves Anthropic thinking blocks across loop repair (`aegis/agent/response_normalization.py:69`, `:76`, `:109`, `:186`, `:198`; `aegis/agent/governance.py:28`, `:176`, `:238`, `:252`, `:286`; `tests/test_response_normalization_stage_h.py:160`, `:187`, `:211`; `tests/test_turn_finalization_stage_r.py:254`, `:298`). Remaining gaps: no bounded gap found for this file lane after the focused Hermes interrupted-tail comparison; broader cross-provider live replay remains later harness work. | `80 passed` integrated normalization/finalization/schema/MCP/toolset slice; py_compile/ruff/diff-check clean |
| `agent/tool_guardrails.py` + `agent/tool_result_classification.py` | 475 + 26 | `aegis/agent/guardrails.py`; focused parity tests in `tests/test_guardrails_parity.py`; regressions in `tests/test_resilience.py`, `tests/test_upgrades_batch.py` | 696 guardrails / 196 parity tests / 865 resilience tests / 290 upgrade-batch tests | updated structured-controller slice: AEGIS now exposes Hermes-exact `IDEMPOTENT_TOOL_NAMES`/`MUTATING_TOOL_NAMES`, `ToolCallGuardrailConfig.from_mapping()` with nested and legacy key parsing, structured `ToolCallSignature`/`ToolGuardrailDecision` metadata, `ToolCallGuardrailController`, `toolguard_synthetic_result()`, `append_toolguard_guidance()`, standalone `classify_tool_failure()` for terminal exit codes, memory-full, structured JSON errors, display-style fallback failures, and landed file mutation classification (`aegis/agent/guardrails.py:18`, `:90`, `:105`, `:165`, `:182`, `:260`, `:297`, `:456`, `:467`). The existing AEGIS executor-facing `ToolLoopGuard.check()/record()` string API remains stable and uses separate runtime alias sets for AEGIS names such as `bash`, `list_dir`, `todo_write`, and `apply_patch` (`aegis/agent/guardrails.py:92`, `:535`, `:563`, `:595`, `:638`). Remaining bounded difference: the live executor still consumes the legacy string API rather than the structured decision controller; that keeps current loop behavior stable while the Hermes metadata API is available for the next executor wiring pass. | `11 passed` focused guardrail parity/regression slice; `52 passed` full guardrails/resilience/upgrade batch; py_compile/ruff/diff-check clean |
| `hermes_cli/mcp_startup.py` | 130 | `aegis/mcp/startup.py`; claim path in `aegis/mcp/client.py`; exported through `aegis/mcp/__init__.py`; config default in `aegis/config.py`; focused tests in `tests/test_stage_z_mcp_startup.py` | 130 startup / 3,351 MCP client / 42 init / 1,184 config / 81 focused tests | done first-pass: AEGIS now has a process-shared one-shot background MCP discovery helper, cheap no-server skip, configurable bounded wait (`mcp.discovery_timeout`), in-flight/join/error helpers, and one-time result claiming by `mcp_tools_from_config()` before synchronous fallback (`aegis/mcp/startup.py:25`, `:40`, `:59`, `:95`, `:102`, `:114`; `aegis/mcp/client.py:3339`; `aegis/config.py:664`). Remaining gaps: no CLI/TUI/dashboard callsite starts discovery yet; no cross-process discovery cache; no noninteractive OAuth suppression wrapper specific to discovery yet. | `10 passed` MCP startup/catalog/lifecycle slice; py_compile/ruff/diff-check clean |
| `tools/mcp_tool.py` | 4,911 | `aegis/mcp/client.py`; toolset compatibility in `aegis/tools/registry.py`; executor gate in `aegis/agent/loop.py`; focused tests in `tests/test_mcp_catalog.py`, `tests/test_stage_z_mcp_lifecycle.py`, `tests/test_toolset_resolution.py`, `tests/test_tool_executor_stage_j.py` | 3,450 MCP client / 897 registry / 2,415 loop / 221 catalog tests / 609 lifecycle tests / 209 executor tests | updated runtime slice: MCP tools, resource readers, and prompt renderers now use per-server dynamic toolsets (`mcp-<server>`) with server-name display aliases, while legacy `mcp` toolset selection expands to all dynamic MCP server toolsets. Manager discovery tracks utility wrappers as server-owned tools, refresh removes/re-registers stale remote tools/resources/prompts, captures non-SDK initialize capabilities, skips resource/prompt utility probes when capabilities are explicitly absent, keeps advertised utilities, records exact MCP tool provenance for underscore-ambiguous server names, carries `supports_parallel_tool_calls` from config/catalog into manager state, and exposes that provenance to executor parallel gating (`aegis/mcp/client.py:134`, `:2250`, `:2264`, `:2271`, `:2302`, `:2317`, `:3283`, `:3308`; `aegis/agent/loop.py:1178`, `:1209`; `tests/test_stage_z_mcp_lifecycle.py:143`, `:195`, `:228`, `:250`; `tests/test_tool_executor_stage_j.py:49`). Remaining gaps: manager/status output is still simpler than Hermes configured/connecting/failed/disabled inventory with sampling metrics; broader live SDK/OAuth/status smoke still needs a later lane. | `89 passed` integrated MCP/toolset/deferred/todo/output/executor wave; focused MCP executor/provenance checks `2 passed`; py_compile/ruff/diff-check clean |
| `hermes_cli/mcp_config.py` + `hermes_cli/mcp_security.py` | 977 + 181 | `aegis/cli/main.py`; focused tests in `tests/test_mcp_cli.py` | 6,029 CLI / 597 MCP CLI tests | updated lifecycle/security slice: `aegis mcp configure` treats explicit empty `--include`/`--exclude` as valid filters and now validates the existing server entry before saving filter changes, refusing suspicious pre-planted MCP payloads; `aegis mcp add` supports offline config saves for `--url`, `--command`, `--args`, repeated `--env`, `--preset codex`, `--auth oauth`, and `--force`, rejects ambiguous/unsafe stdio payloads, validates URL/env transport consistency, and preserves existing entries unless overwrite is explicit; `aegis mcp tools <name>` prints selected/unselected tools from `tool_checklist()`, persists `--include`, and clears filters with `--all`; `aegis mcp list` prints a config-backed table without probing live MCP servers; `aegis mcp remove` reports missing servers with available names, removes config entries, and purges MCP OAuth login state; `aegis mcp test <name>` prints transport/auth details, latency, discovered tools/resources/prompts, and probe errors through the existing AEGIS `probe_server()` API; `aegis mcp reauth <name> | --all` sequentially routes OAuth servers through the manager-backed login flow (`aegis/cli/main.py:1220`, `:1282`, `:1400`, `:1556`, `:1563`, `:1716`, `:5682`; `tests/test_mcp_cli.py:25`, `:108`, `:144`, `:162`, `:192`). Remaining gaps: no centralized Hermes-style save gate for every MCP write path yet, no live add-time probing/curses tool selection yet, `--auth header` is rejected offline instead of prompting for a secret, only the `codex` preset is implemented, command executable trust checks remain future work, and deeper live MCP SDK smoke coverage remains later. | `22 passed` worker MCP CLI/security slice; included in `79 passed` integrated wave; py_compile/ruff/diff-check clean |
| `gateway/slash_commands.py` | 4,185 | `aegis/gateway/runner.py`; gateway command tests in `tests/test_gateway_commands.py` | 2,295 runner / 1,817 gateway command tests | updated first-pass: AEGIS gateway now has an owned command registry (`aegis/gateway/runner.py:33`), `/commands [page]` control-plane dispatch (`:921`, `:934`, `:1222`), Telegram-sized pagination, invalid/out-of-range page handling, run metadata, and `/commands` discoverability in `/help`/status. | `45 passed` full gateway command suite; included in `35 passed` combined slice; ruff/diff-check clean |
| `hermes_cli/commands.py` | 2,053 | `aegis/cli/repl.py`; prefix dispatch tests in `tests/test_cli_slash_dispatch.py` | 3,121 REPL / 64 focused CLI tests | updated first-pass: terminal slash input now resolves exact and unique-prefix commands, keeps shortest unique matches, reports ambiguous prefixes, and expands prefixes before `/plan`-style preprocessors (`aegis/cli/repl.py:429`, `:2031`, `:2986`). | `4 passed` CLI prefix suite; `2 passed` product slash metadata tests; included in `35 passed` combined slice; ruff/diff-check clean |

Latest focused verification:

```text
Current wave:

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_mcp_cli.py \
  tests/test_toolset_resolution.py \
  tests/test_mcp_catalog.py \
  tests/test_stage_z_mcp_lifecycle.py \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_tool_executor_stage_j.py \
  tests/test_tools.py::test_todo_tool \
  tests/test_tools.py::test_todo_tool_merges_json_string_and_cancelled_status \
  tests/test_tools.py::test_todo_tool_rejects_bad_payload_and_bounds_injection \
  tests/test_tools.py::test_tool_output_hermes_config_aliases_for_read_file \
  tests/test_tools.py::test_tool_output_max_bytes_alias_truncates_tool_output \
  tests/test_config_cli.py::test_config_set_accepts_hermes_tool_output_alias \
  tests/test_tool_schema_validation.py::test_builtin_tool_schemas_validate_cleanly \
  tests/test_tool_schema_validation.py::test_aegis_tool_aliases_are_registered_with_provenance \
  tests/test_net_and_edit.py::test_python_plugin_hooks \
  tests/test_net_and_edit.py::test_python_plugin_middleware_chain \
  tests/test_hooks_cli.py \
  tests/test_smoke.py::test_execute_code_rpc \
  tests/test_product_surfaces.py::test_mcp_server_uses_full_tool_context_and_visible_inventory
# 118 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_stage_z_codex_app_server.py
# 3 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_providers.py -k codex_app_server
# 5 passed, 55 deselected

python -m ruff check aegis/providers/codex_app_server.py aegis/agent/loop.py tests/test_stage_z_codex_app_server.py
# All checks passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_toolset_resolution.py
# 26 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_tool_schema_validation.py tests/test_mcp_catalog.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_delegation_defaults.py tests/test_tools_cli.py
# 44 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_guardrails_parity.py tests/test_resilience.py tests/test_upgrades_batch.py
# 52 passed

python -m py_compile aegis/tools/registry.py aegis/tools/builtin.py aegis/tools/extra_builtin.py aegis/tools/process.py aegis/tools/code_exec.py aegis/tools/agentic.py aegis/tools/cronjob_tool.py aegis/tools/recall.py aegis/tools/skill_manage.py tests/test_toolset_resolution.py aegis/tools/async_bridge.py aegis/tools/backends.py aegis/mcp/server.py aegis/plugins.py aegis/hooks.py aegis/agent/loop.py tests/test_tool_executor_stage_j.py tests/test_smoke.py tests/test_product_surfaces.py aegis/agent/guardrails.py tests/test_guardrails_parity.py tests/test_resilience.py tests/test_upgrades_batch.py

python -m ruff check aegis/tools/registry.py aegis/tools/builtin.py aegis/tools/extra_builtin.py aegis/tools/process.py aegis/tools/code_exec.py aegis/tools/agentic.py aegis/tools/cronjob_tool.py aegis/tools/recall.py aegis/tools/skill_manage.py tests/test_toolset_resolution.py aegis/tools/async_bridge.py aegis/tools/backends.py aegis/mcp/server.py aegis/plugins.py aegis/hooks.py aegis/agent/loop.py tests/test_tool_executor_stage_j.py tests/test_smoke.py tests/test_product_surfaces.py aegis/agent/guardrails.py tests/test_guardrails_parity.py tests/test_resilience.py tests/test_upgrades_batch.py
# All checks passed

git diff --check -- BUILD_STATUS.md aegis/tools/registry.py aegis/tools/builtin.py aegis/tools/extra_builtin.py aegis/tools/process.py aegis/tools/code_exec.py aegis/tools/agentic.py aegis/tools/cronjob_tool.py aegis/tools/recall.py aegis/tools/skill_manage.py tests/test_toolset_resolution.py aegis/tools/async_bridge.py aegis/tools/backends.py aegis/mcp/server.py aegis/plugins.py aegis/hooks.py aegis/agent/loop.py tests/test_tool_executor_stage_j.py tests/test_smoke.py tests/test_product_surfaces.py aegis/agent/guardrails.py tests/test_guardrails_parity.py tests/test_resilience.py tests/test_upgrades_batch.py
# clean

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_response_normalization_stage_h.py \
  tests/test_response_normalization_loop_stage_h.py \
  tests/test_finalization_stage_r.py \
  tests/test_turn_finalization_stage_r.py \
  tests/test_stage_u_schema_sanitizer.py \
  tests/test_providers.py::test_tool_schema_sanitized_across_provider_transports \
  tests/test_net_and_edit.py::test_schema_sanitizer_strips_dialect_metadata_keeps_annotations_and_structure \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_mcp_cli.py \
  tests/test_mcp_catalog.py \
  tests/test_response_normalization_stage_h.py \
  tests/test_response_normalization_loop_stage_h.py \
  tests/test_thinking_recovery.py \
  tests/test_toolset_resolution.py
# 80 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_mcp_cli.py \
  tests/test_toolset_resolution.py
# 21 passed

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_u_schema_sanitizer.py \
  tests/test_providers.py::test_tool_schema_sanitized_across_provider_transports \
  tests/test_net_and_edit.py::test_schema_sanitizer_strips_dialect_metadata_keeps_annotations_and_structure
# 11 passed

python -m py_compile aegis/tools/devtools.py tests/test_stage_u_deferred_bridge.py tests/test_deferred_tools.py

python -m py_compile aegis/agent/governance.py aegis/providers/schema.py aegis/tools/devtools.py aegis/tools/registry.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_response_normalization_stage_h.py tests/test_turn_finalization_stage_r.py tests/test_finalization_stage_r.py tests/test_response_normalization_loop_stage_h.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_toolset_resolution.py tests/test_providers.py tests/test_net_and_edit.py tests/test_deferred_tools.py tests/test_thinking_recovery.py

python -m ruff check aegis/tools/devtools.py tests/test_stage_u_deferred_bridge.py tests/test_deferred_tools.py
# All checks passed

python -m ruff check aegis/agent/governance.py aegis/providers/schema.py aegis/tools/devtools.py aegis/tools/registry.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_response_normalization_stage_h.py tests/test_turn_finalization_stage_r.py tests/test_finalization_stage_r.py tests/test_response_normalization_loop_stage_h.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_toolset_resolution.py tests/test_providers.py tests/test_net_and_edit.py tests/test_deferred_tools.py tests/test_thinking_recovery.py
# All checks passed

git diff --check -- aegis/tools/devtools.py tests/test_stage_u_deferred_bridge.py tests/test_deferred_tools.py
# clean

git diff --check -- BUILD_STATUS.md aegis/agent/governance.py aegis/providers/schema.py aegis/tools/devtools.py aegis/tools/registry.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_response_normalization_stage_h.py tests/test_turn_finalization_stage_r.py tests/test_finalization_stage_r.py tests/test_response_normalization_loop_stage_h.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_toolset_resolution.py tests/test_providers.py tests/test_net_and_edit.py tests/test_deferred_tools.py tests/test_thinking_recovery.py
# clean

Earlier combined slice:

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_u_schema_sanitizer.py \
  tests/test_providers.py::test_tool_schema_sanitized_across_provider_transports \
  tests/test_net_and_edit.py::test_schema_sanitizer_strips_dialect_metadata_keeps_annotations_and_structure \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_mcp_cli.py \
  tests/test_mcp_catalog.py \
  tests/test_response_normalization_stage_h.py \
  tests/test_response_normalization_loop_stage_h.py \
  tests/test_thinking_recovery.py
# 55 passed

python -m py_compile aegis/providers/schema.py aegis/tools/devtools.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_response_normalization_stage_h.py

python -m ruff check aegis/providers/schema.py aegis/tools/devtools.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_response_normalization_stage_h.py tests/test_providers.py tests/test_net_and_edit.py tests/test_deferred_tools.py tests/test_response_normalization_loop_stage_h.py tests/test_thinking_recovery.py
# All checks passed

git diff --check -- BUILD_STATUS.md aegis/providers/schema.py aegis/tools/devtools.py aegis/cli/main.py aegis/agent/response_normalization.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py tests/test_mcp_cli.py tests/test_response_normalization_stage_h.py tests/test_providers.py tests/test_net_and_edit.py
# clean

AEGIS_HOME="$(mktemp -d)" HERMES_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_u_schema_sanitizer.py \
  tests/test_providers.py::test_tool_schema_sanitized_across_provider_transports \
  tests/test_net_and_edit.py::test_schema_sanitizer_strips_dialect_metadata_keeps_annotations_and_structure
# 11 passed

python -m py_compile aegis/providers/schema.py tests/test_stage_u_schema_sanitizer.py tests/test_providers.py tests/test_net_and_edit.py

python -m ruff check aegis/providers/schema.py tests/test_stage_u_schema_sanitizer.py tests/test_providers.py tests/test_net_and_edit.py
# All checks passed

git diff --check -- aegis/providers/schema.py tests/test_stage_u_schema_sanitizer.py tests/test_providers.py tests/test_net_and_edit.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_mcp_cli.py \
  tests/test_mcp_catalog.py
# 11 passed

python -m py_compile aegis/cli/main.py tests/test_mcp_cli.py

python -m ruff check aegis/cli/main.py tests/test_mcp_cli.py
# All checks passed

git diff --check -- aegis/cli/main.py tests/test_mcp_cli.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py
# 13 passed

python -m py_compile aegis/tools/devtools.py

python -m ruff check aegis/tools/devtools.py tests/test_stage_u_deferred_bridge.py tests/test_deferred_tools.py
# All checks passed

git diff --check -- aegis/tools/devtools.py tests/test_stage_u_deferred_bridge.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_toolset_resolution.py \
  tests/test_tool_schema_validation.py \
  tests/test_subagent_stage_t.py \
  tests/test_deferred_tools.py \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_stage_z_mcp_startup.py \
  tests/test_mcp_catalog.py \
  tests/test_stage_z_mcp_lifecycle.py::test_list_changed_notification_refreshes_registered_tools
# 48 passed

python -m py_compile aegis/tools/registry.py aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py

python -m ruff check aegis/tools/registry.py tests/test_toolset_resolution.py tests/test_tool_schema_validation.py aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py tests/test_stage_z_mcp_startup.py
# All checks passed

git diff --check -- aegis/tools/registry.py tests/test_toolset_resolution.py tests/test_tool_schema_validation.py aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py tests/test_stage_z_mcp_startup.py BUILD_STATUS.md
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_z_mcp_startup.py \
  tests/test_mcp_catalog.py \
  tests/test_stage_z_mcp_lifecycle.py::test_list_changed_notification_refreshes_registered_tools
# 10 passed

python -m py_compile aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py

python -m ruff check aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py tests/test_stage_z_mcp_startup.py
# All checks passed

git diff --check -- aegis/mcp/startup.py aegis/mcp/__init__.py aegis/mcp/client.py aegis/config.py tests/test_stage_z_mcp_startup.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_tool_schema_validation.py \
  tests/test_tool_executor_stage_j.py \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_hardening.py::test_tool_exception_text_is_sanitized_before_model_context \
  tests/test_hardening.py::test_untrusted_tool_result_wrapped
# 34 passed

python -m py_compile aegis/agent/loop.py aegis/tools/schema_validation.py

python -m ruff check aegis/agent/loop.py aegis/tools/schema_validation.py tests/test_tool_schema_validation.py tests/test_tool_executor_stage_j.py tests/test_hardening.py
# All checks passed

git diff --check -- aegis/agent/loop.py aegis/tools/schema_validation.py tests/test_tool_schema_validation.py tests/test_tool_executor_stage_j.py tests/test_hardening.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_resilience.py -k "loop_guard"
# 2 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_resilience.py tests/test_tool_executor_stage_j.py
# 32 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_self_verify.py \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_tool_executor_stage_j.py \
  tests/test_cli_slash_dispatch.py \
  tests/test_gateway_commands.py::test_whoami_and_help \
  tests/test_gateway_commands.py::test_commands_lists_gateway_registry_with_pagination \
  tests/test_product_surfaces.py::test_terminal_slash_help_is_searchable \
  tests/test_product_surfaces.py::test_terminal_slash_completer_uses_command_metadata
# 35 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_hardening.py::test_untrusted_tool_result_wrapped \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_tool_executor_stage_j.py
# 12 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_gateway_commands.py
# 45 passed

python -m py_compile aegis/agent/loop.py aegis/agent/verification.py aegis/cli/repl.py aegis/gateway/runner.py

python -m ruff check aegis/agent/loop.py aegis/agent/verification.py aegis/cli/repl.py aegis/gateway/runner.py tests/test_self_verify.py tests/test_stage_u_deferred_bridge.py tests/test_cli_slash_dispatch.py tests/test_gateway_commands.py tests/test_tool_executor_stage_j.py
# All checks passed

git diff --check -- BUILD_STATUS.md aegis/agent/loop.py aegis/agent/verification.py aegis/cli/repl.py aegis/gateway/runner.py tests/test_self_verify.py tests/test_stage_u_deferred_bridge.py tests/test_cli_slash_dispatch.py tests/test_gateway_commands.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_stage_u_deferred_bridge.py \
  tests/test_deferred_tools.py \
  tests/test_tool_executor_stage_j.py
# 16 passed

python -m py_compile aegis/agent/loop.py aegis/tools/devtools.py

python -m ruff check aegis/agent/loop.py tests/test_stage_u_deferred_bridge.py tests/test_tool_executor_stage_j.py
# All checks passed

git diff --check -- aegis/agent/loop.py tests/test_stage_u_deferred_bridge.py
# clean

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_write_approval_stage_l.py \
  tests/test_write_approval_review_stage_l.py \
  tests/test_gateway_commands.py::test_gateway_memory_pending_and_approve_all_uses_on_disk_store \
  tests/test_gateway_commands.py::test_gateway_skills_approval_pending_diff_and_approve_create
# 13 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_write_approval_stage_l.py \
  tests/test_write_approval_review_stage_l.py \
  tests/test_gateway_commands.py::test_gateway_memory_pending_and_approve_all_uses_on_disk_store \
  tests/test_gateway_commands.py::test_gateway_skills_approval_pending_diff_and_approve_create \
  tests/test_memory_behavior.py \
  tests/test_memory_lifecycle.py \
  tests/test_skill_manage_parity.py \
  tests/test_skills_memory.py::test_skill_manage_patch_pin_delete_report \
  tests/test_skills_memory.py::test_skill_manage_create_view_list_usage
# 69 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_skills_memory.py::test_skill_manage_patch_pin_delete_report \
  tests/test_skills_memory.py::test_skill_manage_create_view_list_usage \
  tests/test_memory_lifecycle.py::test_memory_tool_writes_mirror_to_external_provider \
  tests/test_memory_wiring.py::test_memory_is_stale_after_write_and_clears_on_refresh \
  tests/test_memory_wiring.py::test_message_refresh_mode_remembers_fact_saved_on_previous_turn \
  tests/test_phases.py::test_cli_memory_status_replace_remove
# 6 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_memory_behavior.py tests/test_memory_lifecycle.py
# 49 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_write_approval_stage_l.py::test_memory_manager_stages_add_without_mutating_memory_file \
  tests/test_write_approval_review_stage_l.py::test_memory_approve_applies_add_discards_after_success_and_reports_failures \
  tests/test_gateway_commands.py::test_gateway_memory_pending_and_approve_all_uses_on_disk_store
# 3 passed

python -m py_compile aegis/tools/skill_manage.py aegis/write_approval.py aegis/memory.py aegis/tools/builtin.py aegis/config.py

python -m ruff check aegis/write_approval.py aegis/write_approval_review.py aegis/memory.py aegis/tools/builtin.py aegis/tools/skill_manage.py aegis/cli/main.py aegis/cli/repl.py aegis/gateway/runner.py tests/test_write_approval_stage_l.py tests/test_write_approval_review_stage_l.py tests/test_gateway_commands.py tests/test_memory_behavior.py tests/test_skill_manage_parity.py tests/test_skills_memory.py
# All checks passed

git diff --check -- BUILD_STATUS.md aegis/write_approval.py aegis/write_approval_review.py aegis/memory.py aegis/config.py aegis/tools/builtin.py aegis/tools/skill_manage.py aegis/cli/main.py aegis/cli/repl.py aegis/gateway/runner.py tests/test_write_approval_stage_l.py tests/test_write_approval_review_stage_l.py tests/test_gateway_commands.py tests/test_memory_behavior.py tests/test_skill_manage_parity.py tests/test_skills_memory.py
# clean
```

Pending review/apply slice:

```text
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_write_approval_review_stage_l.py tests/test_write_approval_stage_l.py
# 11 passed

AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_skills_memory.py::test_skill_manage_patch_pin_delete_report \
  tests/test_skills_memory.py::test_skill_manage_create_view_list_usage \
  tests/test_memory_lifecycle.py::test_memory_tool_writes_mirror_to_external_provider \
  tests/test_memory_wiring.py::test_memory_is_stale_after_write_and_clears_on_refresh \
  tests/test_memory_wiring.py::test_message_refresh_mode_remembers_fact_saved_on_previous_turn \
  tests/test_phases.py::test_cli_memory_status_replace_remove
# 6 passed

python -m py_compile aegis/write_approval_review.py aegis/write_approval.py aegis/memory.py aegis/tools/skill_manage.py aegis/cli/main.py aegis/cli/repl.py

python -m ruff check aegis/write_approval_review.py aegis/write_approval.py aegis/memory.py aegis/tools/skill_manage.py aegis/cli/main.py aegis/cli/repl.py tests/test_write_approval_review_stage_l.py tests/test_write_approval_stage_l.py
# All checks passed
```

### Blockers To Full-Parity Claim

- Full Hermes reading coverage is not complete. Large areas still need
  deliberate read logs: dashboard/web, desktop/apps, TUI, CLI command breadth,
  plugin bundles, MCP/plugin breadth, ACP/LSP/project adapters, docs/packaging,
  and many tests.
- The local Hermes reference checkout is behind current GitHub `main`. Before a
  new line-by-line domain audit, either update the checkout or explicitly tie
  the audit to `0198713c3364f7a16603fa684e78671b1392941d`.
- AEGIS has many useful core patches, but full-harness parity cannot be claimed
  until each domain has read evidence, behavior mapping, implementation, and
  regression tests.
- Dashboard, desktop, TUI, web/static, and packaging are in scope for full
  harness parity. Patch them in dedicated lanes so their build/test risk stays
  visible, but do not treat them as out of scope.

### Work Protocol

- For each next domain, read Hermes first, then inspect the AEGIS owner files,
  then patch.
- Use subagents for disjoint write scopes: one worker per file family or test
  lane, never overlapping production files.
- Update this dashboard after every large patch with LOC/file movement,
  parity-confidence movement, test evidence, and remaining blockers.
- Do not use the old alphabetical stage framing as a progress claim. Track full
  harness domains and evidence instead.

## Reference Reading Log

Read directly so far:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `AGENTS.md` | Reference repo working rules | Full file, 1,399 LOC |
| `agent/agent_init.py` | Agent bootstrap and runtime state, provider routing, session state, config-derived prompt controls, compression/context-engine setup, provider-specific startup branches | Full file, 1,888 LOC |
| `agent/turn_context.py` | Turn prologue | Full file, 489 LOC |
| `agent/system_prompt.py` | Stable/context/volatile system prompt assembly, prompt cache posture, skills prompt callsite, runtime profile guidance | Full file, 536 LOC |
| `agent/prompt_builder.py` | Prompt construction pieces | Partial: prompt cache/search hits plus skills prompt cache/index `1250-1715` |
| `agent/skill_utils.py` | Skill discovery helpers, exclusions, frontmatter/platform/environment gates | Targeted `1-260` |
| `agent/skill_preprocessing.py` | Skill content preprocessing, template vars, inline shell expansion controls | Full file, 144 LOC |
| `agent/conversation_loop.py` | Main loop | `1-90`, `250-500`, `497-1235`, `980-1448`, `2801-3306`, `4000-4895`, `4318-4352`, `4377-4420` |
| `agent/tool_executor.py` | Tool execution | Full file, `1-1545` |
| `agent/tool_dispatch_helpers.py` | Tool dispatch helpers: parallel gating, multimodal/result envelopes, mutation target/result extraction, untrusted wrappers | Full file, 448 LOC |
| `agent/tool_result_classification.py` | File mutation result classification helpers | Full file, 26 LOC |
| `agent/tool_guardrails.py` | Tool-call guardrails | Full file, 475 LOC |
| `gateway/slash_commands.py` | Gateway slash-command dispatch and command browser | Full file, 4,185 LOC |
| `hermes_cli/commands.py` | Central CLI/gateway command registry and help generation | Read registry/help/prefix-relevant regions; worker reported full file with emphasis on `1-325`, `1317-2038` |
| `cli.py` | Terminal command dispatch/help integration | Worker-read relevant help/dispatch regions `6329-6378`, `7966-8722` |
| `agent/think_scrubber.py` | Streaming reasoning/thinking suppression | Full file, 413 LOC |
| `agent/context_compressor.py` | Context compression engine | Full file, 2,683 LOC |
| `agent/conversation_compression.py` | Compression summaries/history | Full file, 1,090 LOC |
| `agent/error_classifier.py` | Provider/API error taxonomy, payload/context/output-cap classification | Full file, 1,447 LOC |
| `agent/model_metadata.py` | Provider-reported context/output-cap parser helpers | Stage O ranges `1000-1145` |
| `agent/turn_retry_state.py` | Per-attempt recovery guard bookkeeping | Full file, 79 LOC |
| `agent/verification_stop.py` | Stop-before-verify behavior | searched/read relevant sections |
| `agent/verification_evidence.py` | Verification evidence tracking | searched/read relevant sections |
| `run_agent.py` | Runtime wrapper helpers, stream scrubbers, guardrail helpers | `360-585`, `1530-1665`, `2468-2565`, `4240-4370`, `4960-5045`, `5248-5295` |
| `tests/agent/test_think_scrubber.py` | Hermes stream-scrubber contract | Full file, 245 LOC |

Stage E reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/turn_context.py` | Runtime reset/prologue and primary runtime restore | `140-260` |
| `agent/agent_init.py` | Provider/model/api-mode setup and central provider/auth routing | `290-420`, `629-735` |
| `agent/conversation_loop.py` | Runtime locals, auth refresh counters, provider preflight/rate guard | `520-620`, `980-1065` |

Stage F reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/agent_runtime_helpers.py` | API-message sanitation, role repair, thinking-only wire cleanup | `245-520`, `900-1010`, `2135-2265` |
| `agent/conversation_loop.py` | Placement of argument repair, role repair, API-copy sanitizer, thinking-only drop | `700-930` |
| `run_agent.py` | Thinking-only assistant detection and strict tool-call forwarders | `3410-3605`, `5180-5245` |
| `agent/message_sanitization.py` | Malformed tool-call argument repair and interrupted tool-tail closure behavior | Full file, 477 LOC |

Stage G reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `run_agent.py` | Stream delivery reset/flush and `_fire_stream_delta` thinking-scrubber placement | `4240-4370` |
| `agent/think_scrubber.py` | Stateful streamed `<think>`/reasoning suppression across chunk boundaries | Full file, 413 LOC |
| `tests/agent/test_think_scrubber.py` | Expected scrubber behavior: closed pairs, partial tags, orphan close tags, flush/reset | Full file, 245 LOC |
| `agent/conversation_loop.py` | API request ids, observer hook placement, provider lifecycle | `980-1448` |

Stage H reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/agent_runtime_helpers.py` | Final response reasoning/tool XML sanitation and reasoning extraction | `480-590`, `1130-1225` |
| `agent/chat_completion_helpers.py` | Assistant message builder, reasoning preservation, persistence-boundary redaction/sanitation | `850-1015` |
| `agent/conversation_loop.py` | Thinking-budget/empty/thinking-only response handling context | `1628-1690`, `4500-4618` |

Stage J reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/tool_executor.py` | Tool executor dispatch, permission/checkpoint ordering, destructive shell target detection, observations | Full file, `1-1545` |

Stage L reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `tools/checkpoint_manager.py` | Shared shadow-git checkpoint store, rollback/diff/prune lifecycle | Full file, 1,675 LOC |
| `tools/file_state.py` | Cross-agent read/write stamps, sibling-write stale warnings, partial-read warnings, path locks | Full file, 332 LOC |
| `tools/write_approval.py` | Persistent write approval/pending-store gate | Full file, 493 LOC |
| `tools/file_operations.py` | Backend file operations, atomic writes, BOM/CRLF preservation, lint/LSP diagnostics, search/read behavior | Full file, 2,423 LOC |
| `tools/file_tools.py` | Agent-visible file tools, task cwd resolution, read dedup, stale-write wiring, patch/write stamp refresh | Full file, 1,915 LOC |

Stage N reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/conversation_compression.py` | Compression feasibility, lock lifecycle, in-place durable rewrite, rotation, post-boundary memory/context notifications | Full file, 1,090 LOC |
| `agent/context_compressor.py` | Summary generation, fallback/abort semantics, tail-budget boundaries, no-progress guards, summary metadata | Full file, 2,683 LOC |
| `agent/turn_context.py` | Preflight compression before provider call and conversation-history re-baseline | `300-430` |
| `agent/conversation_loop.py` | Overflow/payload-too-large compression retry, no-progress detection, post-response compaction | `2801-3306`, `4377-4420` |

Stage O reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/error_classifier.py` | Priority-ordered error taxonomy: payload-too-large, request-validation before context overflow, disconnect/large-session heuristics | Full file, 1,447 LOC |
| `agent/model_metadata.py` | Provider-reported context-window and available-output-token parsing | `1000-1145` |
| `agent/turn_retry_state.py` | One-shot recovery guards and restart signals | Full file, 79 LOC |
| `agent/conversation_loop.py` | Overflow disabled guard, long-context tier reduction, payload-too-large retry, output-cap retry, context-overflow retry caps | `2320-2405`, `2780-3425` |

Stage P reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/conversation_loop.py` | Length continuation, Codex incomplete continuation, post-tool empty nudge, thinking-only prefill, empty retries/fallback, and scaffold cleanup before final response | `400-440`, `570-600`, `1466-1910`, `3820-3862`, `3968-4062`, `4244-4310`, `4424-4710`, `4750-4770` |
| `run_agent.py` | Durable empty-response scaffold stripping before session persistence | `1560-1645` |
| `agent/replay_cleanup.py` | Replay-tail cleanup concepts for interrupted/dangling tool-call tails | `1-130` |
| `agent/turn_finalizer.py` | Abnormal turn completion explanation gates and empty/partial final handling | `240-315` |
| `agent/transports/chat_completions.py` | Strict-wire stripping of internal scaffold keys | `140-180` |
| `agent/chat_completion_helpers.py` | Final-summary request cleanup of internal continuation/scaffold metadata | `1420-1480` |
| `tests/run_agent/test_empty_response_recovery_persistence.py` | Empty-recovery scaffold persistence regressions | Full file, 94 LOC |
| `tests/run_agent/test_anthropic_truncation_continuation.py` | Anthropic truncation normalization and continuation branch evidence | Full file, 114 LOC |
| `tests/run_agent/test_partial_stream_finish_reason.py` | Continuation prompt variants and partial-stream continuation tests | `234-360` |
| `tests/run_agent/test_turn_completion_explainer.py` | Empty-response exhaustion user-visible explanation regression | `130-180` |
| `tests/agent/transports/test_chat_completions.py` | Refusal-as-content-filter avoids empty retry loop | `850-900` |

Stage R reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/turn_finalizer.py` | Post-loop finalization: cleanup fail-open ordering, persistence cleanup, interrupt alternation, result metadata, memory/review gates | Full file, 488 LOC |
| `run_agent.py` | Background review wrapper, `_persist_session`, empty-scaffold stripping, DB flush, trajectory save, usage summary hook, external memory sync | `1470-1765`, `2040-2085`, `3150-3238` |
| `agent/conversation_loop.py` | Finalizer handoff and turn-exit reason plumbing | `4870-4910` |
| `agent/message_sanitization.py` | Interrupted tool-tail closure helper | `270-330` |
| `tests/agent/test_turn_finalizer_cleanup_guard.py` | Cleanup failure must not lose returned response | Full file, 181 LOC |
| `tests/agent/test_turn_finalizer_interrupt_alternation.py` | Interrupted tool result tail must persist as assistant-closed sequence | Full file, 168 LOC |
| `tests/run_agent/test_memory_sync_interrupted.py` | External memory sync skips interrupted/empty turns and stays fail-soft | Full file, 326 LOC |
| `tests/run_agent/test_last_reasoning_per_turn.py` | Current-turn reasoning extraction boundaries | Full file, 109 LOC |
| `tests/run_agent/test_860_dedup.py` | Repeated session persistence dedupe expectations | `90-150` |
| `tests/run_agent/test_message_sequence_repair.py` | Empty-scaffold tail cleanup and tool-tail rewind behavior | `1-90` |

Stage S reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `docs/session-lifecycle.md` | Session source/entry/store lifecycle, restart recovery, resume-pending, switching, cache and reset policies | Full file, 631 LOC |
| `hermes_state.py` | Durable session schema, create/end/reopen, compression-tip projection, resume resolver, message replay, branch/delegate exclusion | `640-730`, `793-870`, `1470-1668`, `1850-1872`, `2060-2288`, `2400-2735`, `3070-3135`, `3390-3605` |
| `gateway/session.py` | SessionEntry resume flags, get-or-create reset order, resume-pending, switch/reopen, transcript load/rewrite | `500-675`, `1210-1410`, `1460-1730` |
| `acp_adapter/session.py` | ACP create/fork/persist/restore runtime metadata | `210-285`, `388-545` |
| `agent/conversation_compression.py` | Compression split, parent end, child create, session/log context update, memory/context boundary hooks, `session:compress` event | `340-430`, `540-790` |
| `tools/delegate_tool.py` | Delegate/subagent child session metadata and `_delegate_from` marker | `1-120`, `1280-1370`, `2460-2512` |
| `run_agent.py` | Agent session DB creation and context-engine transition helper | `430-670` |
| `tests/hermes_state/test_resolve_resume_session_id.py` | Compression resume-to-tip and branch/delegate exclusion regressions | Full file, 217 LOC |
| `tests/acp/test_session_provenance.py` | Compression provenance/root/depth expectations | Full file, 103 LOC |
| `tests/run_agent/test_compression_boundary_hook.py` | Compression boundary hooks and event emission expectations | Full file, 255 LOC |
| `tests/agent/test_memory_session_switch.py` | Memory provider session-switch fanout and Hindsight state reset expectations | Full file, 334 LOC |
| `tests/test_tui_gateway_server.py` | Resume follows compression tip, parent-lineage display, runtime metadata preservation | `920-1095`, `1235-1295`, `2100-2150` |

Stage T reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `tools/delegate_tool.py` | Delegate/subagent contract: child isolation, toolsets, role/depth, foreground/background dispatch, event identity, registry, cancellation, summaries | `1-260`, `260-620`, `620-980`, `980-1375`, `1375-1780`, `1780-2180`, `2180-2340`, `2340-2545`, `2540-2665`, `2660-2860`, `2860-3065`, `3065-3238` |
| `tools/async_delegation.py` | Background delegation manager, capacity/list/status/cancel/interrupt, completion event metadata | `1-260`, `260-556` |
| `tests/tools/test_delegate_toolset_scope.py` | Parent-child toolset scoping regression expectations | Full file, 65 LOC |
| `tests/tools/test_delegate_composite_toolsets.py` | Composite/default toolset expansion expectations | Full file, 45 LOC |
| `tests/tools/test_delegate.py` | Role/depth/approval/fallback/background behavior and result shaping | Targeted `2280-2873` plus `rg` across role/depth/approval/fallback paths |
| `tests/agent/test_subagent_progress.py` | Subagent progress, identity, and parent event visibility | Full file, 387 LOC |
| `tests/agent/test_subagent_stop_hook.py` | Stop/cleanup hook behavior for subagents | Full file, 224 LOC |
| `tests/run_agent/test_interrupt_propagation.py` | Interrupt propagation into child agents | Full file, 244 LOC |
| `tests/run_agent/test_real_interrupt_subagent.py` | Real subagent interrupt path expectations | Full file, 186 LOC |
| `run_agent.py` | Runtime subagent dispatch, progress, cancellation, and background integration hooks | `360-540`, `2440-2575`, `5310-5365` plus subagent/interrupt searches |
| `tools/approval.py` | Child/non-interactive approval posture | `1-240` |
| `tools/interrupt.py` | Interrupt/cancel propagation model | `1-260` |
| `toolsets.py` | Toolset alias/default/effective scope behavior | `1-120`, `330-450`, `660-750` |

Stage U reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `tools/mcp_tool.py` | MCP client/server task lifecycle, schema registration, dynamic refresh, capability gates, keepalive, OAuth/error recovery | Full ordered pass by ranges `1-4911` |
| `tools/tool_search.py` | Deferred tool disclosure, bridge names, scoped catalog/dispatch, bridge recursion rejection | Full file, 735 LOC |
| `tools/schema_sanitizer.py` | Provider-safe schema deep-copy, nullable collapse, malformed schema repair, reactive pattern/format and slash-enum recovery | Full file, 483 LOC |
| `tools/registry.py` | Registry generation, dynamic definitions, toolset aliases, dispatch and shadowing contracts | Full file, 645 LOC |
| `model_tools.py` | Tool definition assembly, schema sanitization, deferred bridge dispatch, plugin middleware dispatch, schema-driven argument coercion, tool-error sanitization | Full file, 1,259 LOC |
| `toolsets.py` | Core tool protections, dynamic MCP/plugin toolsets, aliases, all/star resolution | Full file, 941 LOC |
| `hermes_cli/mcp_startup.py` | Background MCP discovery singleton and bounded wait behavior | Full file, 130 LOC |
| `hermes_cli/mcp_config.py` | MCP CLI add/remove/list/test/configure/login/reauth and include/exclude persistence | Full file, 977 LOC |
| `tests/tools/test_tool_search.py` | Deferred bridge regressions: core protection, scoping, search/describe/call rejection | Full file, 538 LOC |
| `tests/tools/test_schema_sanitizer.py` | Schema sanitizer regressions for nullable unions, required pruning, top-level combinators, recovery helpers | Full file, 702 LOC |
| `tests/tools/test_mcp_capability_gating.py` | MCP advertised capability and keepalive fallback regressions | Full file, 380 LOC |
| `tests/tools/test_mcp_utility_capability_gating.py` | MCP utility schema capability gate regressions | Full file, 174 LOC |
| `tests/tools/test_mcp_dynamic_discovery.py` | Dynamic MCP tool discovery/register/deregister regressions | Full file, 165 LOC |
| `tests/test_toolsets.py` | Dynamic plugin/MCP toolset alias and resolution regressions | Full file, 255 LOC |
| Additional MCP tests | Late refresh, ACP MCP e2e, Anthropic MCP name normalization, Hermes-as-MCP server, async bridge behavior | Full files listed in turn notes and used for Stage U gap audit |

Stage V reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/credential_pool.py` | Credential pool status, OAuth refresh, source seeding, rotation/lease strategies | Full file, `1-2316` |
| `agent/credential_persistence.py` | Borrowed/reference-only secret stripping and safe persistence boundary | Full file |
| `agent/credential_sources.py` | Unified credential removal/suppression behavior | Full file |
| `agent/retry_utils.py` | Jittered retry/backoff and provider overload backoff helpers | Full file |
| `agent/rate_limit_tracker.py` | Rate-limit header parsing/display contracts | Full file |
| `agent/nous_rate_guard.py` | Cross-session account-bucket breaker behavior | Full file |
| `providers/base.py` | Declarative provider profile boundary | Full file |
| `hermes_cli/fallback_config.py` | Fallback chain merge/dedupe semantics | Full file |
| `hermes_cli/fallback_cmd.py` | Fallback add/remove/list/clear behavior | Full file |
| `agent/agent_init.py` | Init-time provider/runtime/fallback/auth setup | Targeted provider/auth/fallback ranges |
| `agent/conversation_loop.py` | Retry/fallback activation and empty/content-filter fallback branches | Targeted fallback/auth/error ranges |
| `agent/agent_runtime_helpers.py` | Credential-pool recovery and primary-runtime restore | Targeted `recover_with_credential_pool` and `restore_primary_runtime` ranges |
| `agent/chat_completion_helpers.py` | Runtime fallback activation and pool isolation | Targeted `try_activate_fallback` range |
| `run_agent.py` | Provider-specific OAuth refresh helpers | Targeted refresh helper ranges |

Stage W reference slices read before implementation:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/usage_pricing.py` | Canonical usage buckets, route/pricing resolution, estimated/included/unknown cost results, cache-read/write handling | Full function ranges `548-944` plus pricing-table inventory, 944 LOC |
| `agent/account_usage.py` | Account usage snapshots, OAuth/API usage fetchers, fail-open usage/credits rendering | Function map and targeted account/credits fetch ranges, 638 LOC |
| `agent/credits_tracker.py` | Nous credits header parsing, notice policy, session-start seeding, fail-open behavior | Function map and targeted parser/notice/seed ranges, 794 LOC |
| `agent/billing_view.py` | Surface-agnostic billing state, Decimal money parsing, idempotency/validation | Function map and targeted billing-state/amount ranges, 295 LOC |
| `agent/trajectory.py` | Trajectory conversion/save fail-open contracts | Full small-file function map, 56 LOC |
| `run_agent.py` | Status callbacks, activity touch/summary, usage-summary hook, token counters, trajectory save hooks | Targeted ranges `640-690`, `805-850`, `2048-2070`, `2870-3110`, plus trajectory/save hooks |
| Stage W Hermes tests | Usage pricing, streaming activity, retry status buffer, account/credits/billing views | Targeted test map from `tests/agent/test_usage_pricing.py`, `tests/run_agent/test_streaming.py`, `tests/run_agent/test_retry_status_buffer.py`, account/credits/billing tests |

Latest Stage Z continuation reference slices:

| Hermes file | Purpose | Coverage |
| --- | --- | --- |
| `agent/credential_sources.py` | Provider/source removal registry, suppression, external/source hints | `1-520` |
| `hermes_cli/auth_commands.py` | Auth remove/logout structured hint surfacing | `430-500` |
| `agent/credential_pool.py` | Source removal/reset integration | `1180-1235` |
| `tests/agent/test_credential_pool.py` | Source-removal and suppression expectations | `2651-2758` |
| `tools/mcp_tool.py` | MCP lifecycle, stale transport ownership, sampling/callback behavior | `1-620`, `838-1310`, `1419-2510`, `3260-3585`, `4722-4915` |
| MCP Hermes tests | Dynamic discovery, elicitation, reconnect, stability, cancellation, SSE, and sampling regressions | Targeted ranges from `tests/tools/test_mcp_tool.py`, `test_mcp_dynamic_discovery.py`, `test_mcp_elicitation.py`, `test_mcp_reconnect_signal.py`, `test_mcp_stability.py`, `test_mcp_cancelled_error_propagation.py`, and `test_mcp_sse_transport.py` |
| `hermes_state.py` | Session DB repair, malformed DB policy, WAL/sidecar handling, status metadata | `127-355`, `355-635`, `798-1155`, `1239-1470` |
| Session repair Hermes tests | Malformed DB and WAL fallback repair regressions | Targeted ranges from `tests/test_state_db_malformed_repair.py` and `tests/test_hermes_state_wal_fallback.py` |
| `tools/execution_environment/daytona.py` | Daytona live environment creation/resume/file lifecycle reference | `1-270` |
| `tools/execution_environment/modal.py` | Modal sandbox, snapshot, upload/download lifecycle reference | `1-478` |
| `tools/execution_environment/file_sync.py` | Remote sync upload/download/delete/sync-back contracts | `1-403` |
| `tools/terminal_tool.py` | Remote execution/upload/sync integration context | `560-760`, `1240-1510`, `2749-2856` |
| Remote backend Hermes tests | Modal bulk upload, sync-back, Daytona, and file-sync regressions | Targeted ranges from `test_modal_bulk_upload.py`, `test_sync_back_backends.py`, `test_daytona_environment.py`, `test_file_sync.py`, and `test_file_sync_back.py` |

Subagent audit inputs:

| Agent | Lane | Status |
| --- | --- | --- |
| Descartes `019f18a5-1dd0-7d33-a015-513275b3d18d` | Full-harness compaction/session lifecycle audit | completed, docs/audit only, no code edits |
| Sagan `019f18a5-4df9-7341-9450-c1033f5eabe9` | Full-harness governance/message sanitation audit | completed, docs/audit only, no code edits |
| Volta `019f18b4-290d-75e3-a365-6af026fbb0a3` | Stage C Hermes turn-prologue invariant audit | completed, docs/audit only, no code edits |
| Ohm `019f18b4-2957-7922-8845-19c42115de8e` | Stage C AEGIS turn-prologue gap audit | completed, docs/audit only, no code edits |
| Laplace `019f18bf-1125-7b00-80af-9859326e203e` | Stage D Hermes volatile-context invariant audit | completed, docs/audit only, no code edits |
| Parfit `019f18bf-116b-7c41-bad8-909349b9c817` | Stage D AEGIS volatile-context gap audit | completed, docs/audit only, no code edits |
| Jason `019f18d0-8c4a-7ca2-87c3-0a4c60c9aa8e` | Stage E provider/model/auth readiness worker | completed, code + tests landed |
| Goodall `019f18d0-d109-7bc3-80f4-b9831c3e9d79` | Stage E routing/budget runtime-selection worker | completed, code + tests landed |
| McClintock `019f18e0-824f-7cd0-bbda-988265e7a5cd` | Stage F persistent Message governance worker | completed, code + tests landed |
| Hypatia `019f18e0-b524-7673-8a8f-19aaa85343d3` | Stage F provider-wire request-copy governance worker | completed, code + tests landed |
| Peirce `019f18ec-63a5-79e2-8a42-b90c22ae9438` | Stage G streaming thinking scrubber worker | completed, code + tests landed |
| Kant `019f18ec-83b5-7783-beb4-4908ecc84c7a` | Stage G provider lifecycle regression worker | completed, tests landed |
| Kierkegaard `019f18f4-b209-78a2-9a64-adb319428940` | Stage H response-normalization helper worker | completed, code + tests landed |
| Carson `019f18f4-e9c1-7212-9e3d-beac49db7c37` | Stage H loop-boundary response-normalization worker | completed, tests landed |
| Cicero `019f18fb-60a0-79f1-9cb0-96945cdbc96c` | Stage J tool executor checkpoint-ordering worker | completed, tests landed |
| Arendt `019f1900-9bd2-7413-b788-bd9f542284f6` | Stage J build-status ledger worker | completed, status-only patch landed |
| Mill `019f1902-5fd9-74d3-8ece-fdebc88ab86c` | Stage L task-aware file-state regression worker | completed, tests landed |
| Harvey `019f190c-cb44-7392-82a9-d98148e8b5b2` | Stage N compaction durability regression worker | completed, tests landed |
| Schrodinger `019f1913-4d98-7182-aa7d-61b06fe10990` | Stage O overflow recovery regression worker | completed, tests landed |
| Popper `019f191c-f36d-7f13-8810-038031dffdc3` | Stage P continuation/empty-response regression worker | completed, tests landed |
| Gibbs `019f1929-db79-75a1-a4a3-c9cd4cc9e072` | Stage R finalization/memory-sync regression worker | completed, tests landed |
| Noether `019f192a-00dc-7472-94a7-18669fa9dc1c` | Stage R interrupt/final-text persistence regression worker | completed, tests landed |
| Feynman `019f1938-1dcb-7453-b8d2-d479a0add9ce` | Stage S resume-to-compression-tip regression worker | completed, tests landed |
| Confucius `019f1938-1e22-7b92-a048-9fd11ae9136e` | Stage S session-switch/runtime metadata regression worker | completed, tests landed |
| Aquinas `019f1946-2181-7621-bfb2-e45f048a4d3e` | Stage T synchronous subagent parity regression worker | completed, tests landed |
| Leibniz `019f1946-41d9-7c43-8beb-7bf6bfff6458` | Stage T background subagent parity regression worker | completed, tests landed |
| Banach `019f1953-b44c-72f1-876e-211d26a159c3` | Stage U deferred bridge worker | completed, code + tests landed |
| Franklin `019f1953-de03-7242-afce-b9753568e8ae` | Stage U schema/MCP sanitizer worker | completed, code + tests landed |
| Ramanujan `019f1961-34de-7c00-94ee-daa650afe526` | Stage V Hermes provider/auth/fallback test-map explorer | completed, read-only audit |
| Aristotle `019f1963-6f94-7641-bf92-3b2959b85e7c` | Stage V provider fallback/taxonomy worker | completed, code + tests landed |
| Hooke `019f1963-a602-7b13-be05-46e033176d3b` | Stage V credential-pool status worker | completed, code + tests landed |
| Euler `019f1973-1a99-7da1-a220-7dd7e156c13d` | Stage W Hermes observability/cost reference explorer | completed, read-only audit |
| Fermat `019f1973-1af0-7ff2-a983-2dc7dbc53672` | Stage W AEGIS observability gap explorer | completed, read-only audit |
| Euclid `019f1976-64b5-7542-9832-e5d6b48d6516` | Stage W usage/cost evidence worker | completed, code + tests landed |
| Curie `019f1976-8293-76a0-afb9-201358d7b46e` | Stage W activity/status event worker | completed, code + tests landed |
| Copernicus `019f1977-5946-7361-bb15-ce5821ac7b81` | Stage W trace timeline/audit artifact worker | completed, code + tests landed |
| Epicurus `019f19ab-2816-7b43-854d-2444b9f46560` | Stage Z gateway approval queue regression worker | completed; tests landed, production completed by main agent |
| Bernoulli `019f19b3-9dcc-7ed0-b1c7-c0cfe08e777f` | Stage Z multi-pending approval prompt-id regression worker | completed; tests landed |
| James `019f19bb-0e19-7ba0-8499-c591dd33b5e4` | Stage Z environment-backed tool-result storage regression worker | completed; tests landed |
| Halley `019f19e6-eaa0-73a0-816e-e88c63a00ba9` | Stage Z MCP OAuth regression worker | completed; tests landed |
| Russell `019f19f3-5d63-7b33-9f3c-6564596e91f1` | Stage Z MCP OAuth manager regression worker | completed; tests landed |
| Nash `019f1a00-eb5b-76d0-b0c5-65bad3780c1d` | Stage Z MCP elicitation regression worker | completed; tests landed |
| Einstein `019f1a60-3591-7a12-be31-b41f7b2407d5` | Stage Z session DB repair/corrupt snapshot recovery worker | completed; production patch and tests landed |
| Helmholtz `019f1a60-5a92-71a2-894c-9e5254544bab` | Stage Z provider-specific rate-limit/account-breaker classifier worker | completed; production patch and tests landed |
| Averroes `019f1a64-6b1c-7030-b6b6-e715e4b3d834` | Stage Z MCP SSE/lifecycle/reconnect parity worker | completed; production patch and tests landed |
| Sartre `019f1a64-8df0-78f1-8af5-c0e099f2c301` | Stage Z tool-result persistence/metadata/rehydration worker | completed; production patch and tests landed |
| Zeno `019f1a4f-1806-72f1-acd7-5d2023d5bac1` | Stage Z durable no-progress compaction worker | completed; code + tests landed |
| Galileo `019f1a4f-3d00-7713-b80f-bd65001d1102` | Stage Z backend-specific live interrupt worker | completed; code + tests landed |
| Lagrange `019f1a55-f9da-7891-aa71-01d68ecef8ac` | Stage Z prompt/cache snapshot worker | completed; code + tests landed |
| Anscombe `019f1a10-59eb-7923-98b2-91ed4c5b3ce3` | Stage Z MCP OAuth metadata/bootstrap regression worker | completed; tests landed, production completed by main agent |
| Darwin `019f1a1b-08dd-7932-bf19-2dd5ad490ccc` | Stage Z MCP OAuth invalid-client regression worker | completed; tests landed, production completed by main agent |
| Beauvoir `019f1a28-6112-74c0-9cfc-9b47b05d6a41` | Stage Z MCP OAuth login regression worker | completed; tests landed |
| Boole `019f1a28-7c16-74c3-95fc-14a8445a44fe` | Stage Z MCP OAuth login/reference mapper | completed; read-only implementation checklist |
| Bohr `019f1a30-1fab-7213-9d72-0a3fdd696b77` | Stage Z approval/sandbox-context worker | completed; code + tests landed |
| Meitner `019f1a30-4afc-75b2-a42b-891dc084e420` | Stage Z append-row persistence worker | completed; code + tests landed |
| Dewey `019f1a30-78ba-7e21-b485-b49071ffc98e` | Stage Z live-backend interrupt worker | completed; code + tests landed |
| Lovelace `019f1a37-e48c-7251-b0d6-33d30858f528` | Stage Z MCP lifecycle/noninteractive startup worker | completed; code + tests landed |
| Plato `019f1a38-f471-7450-8f8c-5ad5a1d2a8c7` | Stage Z provider fallback/rate-control worker | completed; code + tests landed |
| Mendel `019f1a42-34e1-7251-81b4-d3acfde0f943` | Stage Z tool-result local atomic persistence worker | completed; code + tests landed |
| Turing `019f1a42-13fe-78e2-b245-5374fa51dbbd` | Stage Z session/compaction archival persistence worker | completed; code + tests landed |
| Socrates `019f1a41-f30e-70b2-8738-43eb311b9d4a` | Stage Z MCP lifecycle/reconnect worker | completed; code + tests landed |
| Zeno `019f1a4f-1806-72f1-acd7-5d2023d5bac1` | Stage Z session/compaction no-progress guard worker | completed; code + tests landed |
| Galileo `019f1a4f-3d00-7713-b80f-bd65001d1102` | Stage Z backend live-interrupt worker | completed; code + tests landed |
| Lagrange `019f1a55-f9da-7891-aa71-01d68ecef8ac` | Stage Z prompt/cache snapshot worker | completed; code + tests landed |
| Tesla `019f1aa3-0630-7691-9bd5-43c9a6071392` | Stage Z Modal snapshot restore/cleanup-save worker | completed; code + tests landed |
| Erdos `019f1aa2-deec-7223-9e6c-1292293d8d39` | Stage Z provider/auth borrowed-source removal/suppression worker | completed; code + tests landed |
| Locke `019f1aa3-26d3-7b61-9b40-4e55dd19807a` | Stage Z SQLite/WAL sidecar recovery worker | completed; code + tests landed |
| Mencius `019f1ab7-6efc-7d70-974d-e3be3a57a888` | Stage Z MCP post-tool idle sampling worker | completed; code + tests landed |
| Poincare `019f1ab7-9f03-77c0-8cc7-9439fcd27768` | Stage Z remote sync diagnostics/bulk upload worker | completed; code + tests landed |
| Kuhn `019f1ab7-c32c-7601-9273-1c92a236e001` | Stage Z WAL repair metadata/preservation policy worker | completed; code + tests landed |
| Heisenberg `019f1ac4-5028-7240-aaa9-93c9561e5f62` | Stage Z MCP lifecycle/stale-transport/idle-sampling worker | completed; code + tests landed |
| Carver `019f1ac4-7bc9-7982-a32e-afe167b4cc82` | Stage Z remote backend diagnostics/live-proof boundary worker | completed; code + tests landed |
| Planck `019f1ac4-afa2-73a0-b97b-a49c6664dafd` | Stage Z session malformed-repair/status-metadata worker | completed; code + tests landed |
| Avicenna `019f1ad6-2437-7f93-9feb-72c4cf69a4e9` | Stage Z MCP SDK same-task lifecycle/callback worker | completed; code + tests landed |
| Rawls `019f1ad6-247b-79f0-8ff0-8b8df8ece2bb` | Stage Z provider-specific singleton/source parity worker | completed; code + tests landed |
| Euclid the 2nd `019f1b39-f56d-7ba1-8017-5199f2b4e6d0` | Stage L checkpoint CLI/list UX parity worker | completed; code + tests landed |
| Locke the 2nd `019f1b3a-1201-7040-ae5a-a24cd6fe9ffd` | Stage L checkpoint policy/root/size-cap regression worker | completed; tests landed, production patched by main agent |
| Huygens the 2nd `019f1b48-b5a4-7193-a233-95fc6842e9bc` | Stage L checkpoint legacy-clear/history regression worker | completed; tests landed, production patched by main agent |
| McClintock the 2nd `019f1b55-bc13-7590-adf5-ecea30c9eb5f` | Stage L file-operation parity regression worker | completed; tests landed, production patched by main agent |

Previous subagents completed:

| Agent | Result |
| --- | --- |
| Linnaeus | Added first parity tracker to this file only |
| Kepler | Added verify-after-edit helper/tests |
| Chandrasekhar | Added invalid tool-call recovery helper/tests |

Earlier subagent attempts timed out before the successful worker run. If that
recurs for several consecutive turns, treat it as an account/tooling backend
limit, not as a thread-specific problem.

Open audit follow-ups from Ohm that were intentionally not folded into Stage C:

| Finding | Target stage |
| --- | --- |
| None currently open from Ohm | - |

Resolved in Stage E first pass:

| Finding | Resolution |
| --- | --- |
| Scrub streamed inline thinking across chunk boundaries before `assistant_delta` | Stage G adds `StreamingThinkScrubber` and routes normal/grace streamed deltas through it before event emission |
| Enforce budget block before provider call, not only emit warning | `Agent._apply_budget_governor()` marks a blocked turn and `run_conversation()` returns before provider execution |
| Add provider/model/auth readiness preconditions before provider call | `runtime_readiness.py` checks provider/model/complete/auth before normal and grace provider calls |
| Persist in-place automatic compaction before the next provider call | Stage N saves automatic in-place and overflow-recovery compacted sessions immediately after recording compaction metadata |

## Current Baseline

Generated inventories live under `docs/audit/` and are intentionally ignored
because they include local paths and raw comparison data.

| Tree | Source-like files | Source-like LOC |
| --- | ---: | ---: |
| AEGIS | 1,026 | 300,050 |
| Hermes reference | 5,459 | 2,310,169 |
| AEGIS/Hermes ratio | 18.8% | 13.0% |

## Full-Surface Matrix

These numbers are older coarse sizing signals, not feature-completeness claims.
Use the Stage Z mechanical inventory for the latest exact source-like totals:
full source-like AEGIS is 18.8% of Hermes files and 13.0% of Hermes LOC. A
representative active core harness slice (agent/providers/tools/mcp plus
session/config/credentials/usage/tracing/background) is about 89 files / 38,581
LOC vs roughly 258 files / 181,157 LOC in Hermes (34.5% files / 21.3% LOC).
Adding gateway gives about 109 files / 48,759 LOC vs roughly 326 files /
254,037 LOC (33.4% files / 19.2% LOC). These are audit-pressure ratios, not
feature-completeness percentages. The active A-W/Z core-stage tracker still has
23 of 24 formal core-active stages complete (95.8%); counting the now-closed
Stage Z MCP-real-SDK and safe-WAL-replay sub-blockers puts the working active
core-harness tracker around 97-98%, with Stage Z still active.

| Area | AEGIS files | AEGIS LOC | Hermes files | Hermes LOC | LOC parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| Core agent engine | 22 | 5,776 | 136 | 84,059 | 6.9% |
| Provider/model runtime | 10 | 4,444 | 14 | 8,922 | 49.8% |
| Tool execution | 40 | 11,389 | 108 | 90,103 | 12.6% |
| Memory and learning | 4 | 3,312 | 42 | 24,476 | 13.5% |
| Skills | 315 | 67,143 | 814 | 239,138 | 28.1% |
| MCP/ACP/extensions | 8 | 1,685 | 301 | 113,591 | 1.5% |
| Gateway/channels | 22 | 10,747 | 68 | 72,880 | 14.7% |
| Background/cron | 2 | 2,133 | 9 | 6,415 | 33.3% |
| Dashboard/web/TUI | 122 | 34,093 | 1,076 | 244,722 | 13.9% |
| Desktop/installers | 59 | 8,741 | 677 | 427,432 | 2.0% |
| CLI/setup | 23 | 11,470 | 198 | 172,840 | 6.6% |
| Tests | 155 | 53,859 | 1,835 | 656,658 | 8.2% |
| Docs/release | 67 | 8,307 | 732 | 264,924 | 3.1% |

## Core Harness File Map

| AEGIS file | Current LOC | Hermes comparison |
| --- | ---: | --- |
| `aegis/agent/agent.py` | 1,574 | `agent/agent_init.py`, `agent/turn_context.py`, `run_agent.py` wrapper/resume routing behavior; Stage Z prompt snapshot metadata |
| `aegis/agent/loop.py` | 2,402 | `agent/conversation_loop.py`, `agent/tool_executor.py`, `model_tools.py`, `run_agent.py` stream delivery/tool executor behavior |
| `aegis/agent/context.py` | 373 | Hermes stable/ephemeral prompt assembly behavior and non-volatile prompt/context/skills fingerprints |
| `aegis/agent/events.py` | 61 | shared agent event contract |
| `aegis/agent/guardrails.py` | 696 | `agent/tool_guardrails.py`, `agent/tool_result_classification.py`; structured controller plus AEGIS executor adapter |
| `aegis/agent/governance.py` | 263 | Hermes API-message sanitation and role repair helpers |
| `aegis/agent/request_wire.py` | 109 | Hermes wire-only thinking-turn drop and adjacent-user merge behavior |
| `aegis/agent/response_normalization.py` | 160 | Hermes final response sanitation and reasoning-preservation boundary |
| `aegis/agent/streaming_think_scrubber.py` | 222 | `agent/think_scrubber.py` streamed reasoning/thinking block suppression |
| `aegis/agent/compaction_runner.py` | 1,040 | `agent/context_compressor.py`, `agent/conversation_compression.py`, loop compression paths; durable no-progress guard and multi-pass recovery planning |
| `aegis/agent/compaction.py` | 389 | compression summarization/preserve-tail behavior plus persisted-output rehydration for summaries |
| `aegis/agent/invalid_tool_calls.py` | 100 | invalid tool-name/JSON recovery paths in `conversation_loop.py` |
| `aegis/agent/runtime_readiness.py` | 224 | provider/model/auth readiness before provider calls |
| `aegis/agent/verification.py` | 259 | `verification_stop.py`, `verification_evidence.py` |
| `aegis/providers/fallback.py` | 954 | `agent/error_classifier.py`, fallback/no-fallback recovery action taxonomy plus Stage Z rate-control cooldowns |
| `aegis/providers/chat_completions.py` | 256 | OpenAI-compatible request payload shaping |
| `aegis/session.py` | 2,594 | `hermes_state.py`, `gateway/session.py`, session persistence/read/resume metadata surfaces, DB/FTS/WAL repair backups, corrupt sidecar recovery, repair locks, manual restore helpers, and repair metadata |
| `tests/test_resilience.py` | 865 | focused harness resilience, Stage C prologue, Stage D plugin/middleware wire-copy coverage, guardrail runtime regressions, and Stage P prior-turn empty-retry coverage |
| `tests/test_guardrails_parity.py` | 196 | Hermes `tool_guardrails.py` / `tool_result_classification.py` structured parity sidecar |
| `tests/test_governance_stage_f.py` | 88 | Stage F persistent governance regressions |
| `tests/test_request_wire_stage_f.py` | 118 | Stage F wire-only request governance regressions |
| `tests/test_response_normalization_stage_h.py` | 109 | Stage H response-normalization helper regressions |
| `tests/test_response_normalization_loop_stage_h.py` | 163 | Stage H loop-boundary response sanitation/persistence regressions |
| `tests/test_streaming_think_scrubber_stage_g.py` | 72 | Stage G stateful stream-scrubber unit regressions |
| `tests/test_provider_lifecycle_stage_g.py` | 122 | Stage G provider lifecycle and split-stream reasoning leak regressions |
| `tests/test_routing_stage_e.py` | 196 | Stage E route/downshift/blocking regressions |
| `tests/test_tool_executor_stage_j.py` | 120 | Stage J ToolExecutor checkpoint ordering regressions |
| `tests/test_compaction_stage_n.py` | 421 | Stage N automatic/overflow compaction durability, Stage Z archival compaction row regressions, no-progress guard persistence, and multi-pass recovery |
| `tests/test_overflow_stage_o.py` | 175 | Stage O payload-too-large, request-validation, output-cap retry, chat max-token payload regressions |
| `tests/test_continuation_stage_p.py` | 181 | Stage P bounded continuation, empty retry, thinking-only recovery, and continuation max-token regressions |
| `tests/test_agent_perms.py` | 425 | broad agent permissions, governance, budget, and resume routing regressions |
| `tests/test_runtime_readiness.py` | 105 | Stage E provider readiness regressions |
| `tests/test_self_verify.py` | 187 | self-verify and verify-after-edit coverage |
| `tests/test_session_config.py` | 313 | session metadata read/scroll coverage |
| `tests/test_session_stage_s.py` | 183 | Stage S resume-to-compression-tip and branch/delegate exclusion coverage |
| `tests/test_session_switch_stage_s.py` | 207 | Stage S session-switch, branch runtime metadata, SDK resume, terminal picker coverage |
| `tests/test_upgrades_batch.py` | 290 | guardrail direct unit coverage |

Hermes core comparison LOC:

| Hermes file | LOC |
| --- | ---: |
| `agent/conversation_loop.py` | 4,899 |
| `agent/tool_executor.py` | 1,538 |
| `agent/tool_guardrails.py` | 475 |
| `agent/context_compressor.py` | 2,683 |
| `agent/conversation_compression.py` | 1,090 |

## Full-Harness Stage Ledger

Status meanings:

- `done`: implemented and verified in AEGIS for this pass.
- `partial`: exists but does not yet match the mapped Hermes behavior.
- `active`: currently being implemented or audited.
- `queued`: not started in this pass.
- `later`: explicitly outside current core-harness pass.

| Stage | Harness area | Hermes evidence | AEGIS evidence | Status | Next action | Verification gate |
| --- | --- | --- | --- | --- | --- | --- |
| A | Agent bootstrap and turn-start persistence | `turn_context.py` persists inbound user turn after system prompt restore and before provider call | `Agent.run()` + `run_conversation()` early-save after `ensure_system_prompt()` | done | Keep regression green | `test_turn_start_is_persisted_before_provider_call` |
| B | Stable system prompt, runtime block, prefix-cache posture | Hermes cached system prompt and API-only ephemeral context in `turn_context.py`/`conversation_loop.py` | `Agent.ensure_system_prompt()` now reuses existing prompt without rebuild on non-forced calls | done | Keep prompt-cache regression green | prompt metadata tests plus `test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild` |
| C | User turn prologue: ids, counters, reset state | Hermes resets retry counters, task ids, API request id, stream scrubbers, guardrails in `turn_context.py` | `Agent._begin_turn_prologue()`, `run_conversation()` turn/API id handoff, turn-local tool count, direct-loop fallback, session turn metadata | done | Keep Stage C regressions green | turn-start metadata, identity reset, direct-loop prologue, and turn-local tool-count tests |
| D | Memory, skills, wakeups, plugin context before provider | Hermes prefetches memory/plugin context API-only and appends ephemeral system context only to API-message copies | AEGIS memory prefetch, skills, wakeups, `pre_llm_call`, LLM middleware, and volatile system-prompt parts now feed provider-wire copies without mutating canonical session messages | done | Keep volatile-context regressions green | `83 passed` broad harness, `44 passed` Stage D focused, `5 passed` prompt/memory gates |
| E | Routing, model budget governor, runtime selection | Hermes provider/runtime setup and fallback state in init/prologue plus provider-call preflight in `conversation_loop.py` | AEGIS prompt routing, one-turn runtime selection/restore metadata, budget warning/block, and provider/model/auth readiness before normal and grace provider calls | done for first pass | Carry auxiliary provider-call readiness and proactive fallback into V/N where those paths live | `tests/test_routing_stage_e.py`, `tests/test_runtime_readiness.py`, plus broad Stage A-D guardrails |
| F | Request message governance before provider | Hermes repairs role alternation, orphan tools, tool-call args, and drops thinking-only turns only on API copies | AEGIS normalizes canonical tool-call ids/names/args, active tool-result groups, invalid roles, adjacent users, and applies thinking-only drop/merge only on provider-wire copies | done for first pass | Keep Stage F guardrails green; deeper strict-provider field stripping belongs with provider transports if needed | `tests/test_governance_stage_f.py`, `tests/test_request_wire_stage_f.py`, governance legacy tests, broad Stage D/E guardrails |
| G | Provider request/response tracing, observers, cancellation, streamed delta hygiene | Hermes has request ids, callbacks, interrupt paths, and stateful stream thinking scrubber before callbacks | AEGIS tracing spans, provider observers, cancel response id, stable API request metadata, and stateful streamed inline-thinking suppression before `assistant_delta` | done for first pass | Carry deeper activity-touch/cost observer parity into W/V if gaps remain | `tests/test_provider_lifecycle_stage_g.py`, `tests/test_streaming_think_scrubber_stage_g.py`, observability/resilience guardrails |
| H | Provider response normalization and reasoning preservation | Hermes sanitizes reasoning/thinking/tool XML, extracts/preserves reasoning, and redacts before persistence/display | AEGIS normalizes provider responses before `to_message()`, extracts inline reasoning only when structured reasoning is absent, strips reasoning/tool XML, redacts secrets, cleans surrogates, and preserves Anthropic thinking blocks | done for first pass | Carry thinking-only continuation/prefill refinements into P if needed | `tests/test_response_normalization_stage_h.py`, `tests/test_response_normalization_loop_stage_h.py`, broad A-H guardrails |
| I | Invalid tool-name and malformed tool-call recovery | Hermes recovers invalid names/JSON and bounds retries | AEGIS `invalid_tool_calls.py` and loop integration | done | Extend only after F/H if gaps remain | `test_empty_tool_call_name_gets_recovery_result`, `test_invalid_tool_call_recovery_is_bounded` |
| J | Tool executor dispatch, permissions, concurrency, result shaping | Hermes `tool_executor.py` read end-to-end (`1-1545`) and `model_tools.py` read end-to-end (`1-1259`): pre-exec decisions, checkpoints, schema argument coercion, sanitized tool errors, sequential/concurrent dispatch, observations, persistent async tool bridge | AEGIS `ToolExecutor` now gates checkpoints after permission/guard approval, checkpoints destructive shell overwrite targets, coerces schema-typed arguments before middleware/guard/permission/events/tool execution, sanitizes raised tool errors, emits structured plugin/shell `pre_tool_call`/`post_tool_call` observer payloads, applies `transform_tool_result`, resolves async tool and middleware awaitables through a Hermes-style persistent main/worker-loop bridge, and keeps safe parallelism/spills/untrusted wrappers; `execute_code` RPC and MCP stdio dispatch use the same bridge; `Agent` restores one-turn route state across resume | done for first pass | Provider-inline dynamic tool worker queue remains with the provider/Codex-app-server lane, not this executor/model-tools lane | `tests/test_tool_executor_stage_j.py`; `15 passed` focused async executor/entry-point slice; `107 passed` integrated MCP/toolset/deferred/executor/plugin-hook/async-entry wave |
| K | Tool-loop guardrails and hard stops | Hermes structured `ToolCallGuardrailController` halt/warn/block decisions plus `tool_result_classification.py` mutation-success helpers | AEGIS now exposes Hermes-exact tool-name constants, config `from_mapping()`, structured signatures/decisions/controller metadata, synthetic blocked-result JSON, terminal/memory/display failure classification, landed file-mutation detection, and the existing `ToolLoopGuard` executor adapter with optional hard-stop and canonical JSON argument/result hashing | done for this pass | Next executor pass can consume structured decisions directly if needed; current loop API intentionally remains stable | `tests/test_guardrails_parity.py`; hard-stop tests; `52 passed` full guardrails/resilience/upgrade batch |
| L | File safety, checkpoints, stale-write protection | Hermes file safety refs read fully: `checkpoint_manager.py`, `file_state.py`, `write_approval.py`, `file_operations.py`, `file_tools.py`; restore validation re-read at `checkpoint_manager.py` `145-195`, `794-849`; shared-store ranges re-read at `220-520`, `840-1260`, policy/status ranges re-read at `540-620`, `612-660`, `851-930`, `1008-1220`, `1570-1625`, clear/list ranges re-read at `690-735`, `1208-1240`, `1625-1675`, and `hermes_cli/checkpoints.py` `1-244`; file-operation ranges re-read at `file_operations.py` `1-520`, `521-1040`, `1041-1580`, `1581-2110`, `2111-2440`, `file_tools.py` `1-420`, `421-960`, `961-1500`, `1501-1945`, and `binary_extensions.py` `1-42` | AEGIS file tools now use task-aware read/write stamps, sibling-writer stale warnings, partial-read warnings, path locks, write/patch stamp refresh, compact `LINE|CONTENT` read gutters, text-read binary-extension blocking, internal read-display write rejection, post-write persistence verification, Hermes-style search/files/count pagination with `next_offset`, checkpoint workdir metadata, project-root detection, shared git blob store dedupe, checkpoint store status with git-store and legacy archive sizing, stale/orphan/over-limit prune with git ref cleanup, file-count/per-file/total-size checkpoint caps, auto-prune markers, CLI status/list/prune/clear/clear-legacy controls with `--max-size-mb`, history rows with full checkpoint id/date/git-prefix/workdir/label, and single-file rollback with absolute/traversal path rejection | done for first pass | Continue broader file-operation parity if later Hermes reading finds gaps; checkpoint lower-depth gaps are now mostly migration/edge-policy polish | `tests/test_file_operations_stage_l.py`, `tests/test_file_state_stage_l.py`, `tests/test_checkpoint_depth.py`, `tests/test_checkpoint_policy_stage_l.py`, `tests/test_checkpoint_cli_stage_l.py`; file-tool/tool-executor broad checks |
| M | Verify-after-edit and stop-before-verify | Hermes verification-stop/evidence modules | AEGIS `verification.py`, opt-in `agent.verify_after_edit` loop gate | done for first pass | Add richer evidence ledger later | `tests/test_self_verify.py` |
| N | Context compaction preflight and in-loop behavior | Hermes preflight compression in `turn_context.py`, loop compression, context compressor, and durable in-place/rotation persistence in `conversation_compression.py` | AEGIS `compaction_runner.py` now records and saves automatic in-place, aborted automatic, and overflow-recovery compaction state before provider retry/call; `compaction.py` already owns summary/tail behavior | done for first pass | Carry deeper no-progress/cooldown, rough-token calibration, and post-response compaction refinements into O/P/S as needed | `tests/test_compaction_stage_n.py`; compaction boundary/resilience/product-surface guardrails |
| O | Context overflow recovery | Hermes catches overflow, reduces context tier, retries/compresses, separates payload-too-large and output-cap errors, and avoids compaction for deterministic request-validation failures | AEGIS classifier now treats 413/message payload-too-large as compressible without fallback, keeps unsupported-parameter `max_tokens` errors as client aborts, parses available output-token limits, retries once with an ephemeral reduced output cap before compaction, and chat-completions sends `max_tokens` | done for first pass | Carry multi-attempt no-progress retry caps and provider-reported context-window persistence into S/V if needed | `tests/test_overflow_stage_o.py`; reliability/provider/runtime/resilience guardrails |
| P | Empty response, truncated response, continuation nudges | Hermes has bounded empty/truncated/thinking-only continuations, continuation max-token boosts, synthetic scaffold cleanup, and reasoning-only prefill recovery | AEGIS now bounds length continuation to 3 retries, preserves partial chunks into the final text, boosts ephemeral max tokens for continuation calls, nudges post-tool empty once, retries plain empty responses 3 times, and recovers thinking-only responses without surfacing private reasoning | done for first pass | Carry richer terminal empty explanations and provider-specific partial-stream prompt variants into R/W if needed | `tests/test_continuation_stage_p.py`; resilience/provider/runtime/request-boundary guardrails |
| Q | Budget exhaustion and graceful final summary | Hermes iteration budget with grace call | AEGIS budget and grace call | done for first pass | Keep regression green | `test_loop_budget_exhaustion_grace` |
| R | Turn finalization, persistence, usage, trajectory, memory sync | Hermes `turn_finalizer.py`, session persistence helpers, memory sync/review gates, interrupt-tail closure, cleanup-failure regressions | AEGIS now marks turn-result status/exit metadata, keeps synthesized visible final text canonical before persistence/memory sync, closes cancelled tool tails with assistant turns, skips memory/review for interrupted or empty finals, records cleanup errors fail-open, and keeps trajectory capture independent | done for first pass | Carry deeper current-turn reasoning result object and per-message DB append parity into W/S if needed | `tests/test_finalization_stage_r.py`, `tests/test_turn_finalization_stage_r.py`, memory lifecycle/resilience guardrails |
| S | Session lifecycle, branch/resume/compaction split | Hermes session lifecycle docs, DB resolver, gateway resume, ACP persistence, compression boundary, delegate markers | AEGIS session store, branch helper, resolver, SDK/terminal/TUI/surface resume hooks, subagent delegate marker | done for first pass | Carry gateway restart/lane policy depth into Y later | `tests/test_session_stage_s.py`, `tests/test_session_switch_stage_s.py`, session/memory/resilience/SDK guardrails |
| T | Subagents and delegated work | Hermes `delegate_tool.py`, `async_delegation.py`, delegate tests, subagent progress/stop/interrupt tests, approval/interrupt/toolset helpers | AEGIS strict child tool scoping, parent-intersected toolsets, child memory skip, role/depth metadata/events, active registry list/cancel, Hermes-style top-level default background dispatch, background completion/list metadata, and synchronous/background child usage/cost evidence on `subagent_done`, async delegation events, registry/list rows | done for first pass | Carry timeout diagnostics and richer cancellation status into W/Y or later if required | `tests/test_subagent_stage_t.py`, `tests/test_subagent_background_stage_t.py`, typed/delegation/agentic guardrails |
| U | MCP/plugin/deferred tool contracts | Hermes MCP refresh, deferred bridge, schema sanitizer, registry/toolset contracts, MCP CLI/config, deferred search/catalog behavior | AEGIS scoped deferred candidates, `tool_search`/`tool_describe`/`tool_call`, parameter-aware deferred search limits, provider schema sanitizer, MCP schema normalization, MCP server bridge permissions | done for first pass | Carry live MCP refresh/list_changed, BM25/catalog metadata, and OAuth/session-expiry recovery depth into later MCP pass if needed | Stage U deferred/schema/MCP focused tests plus agentic/MCP sweep |
| V | Provider fallback, auth refresh, retry taxonomy | Hermes credential pool, OAuth refresh/quarantine, provider fallback activation, primary restore, and retry taxonomy | AEGIS structured provider-failure classifier, fallback chain dedupe, content-policy/model-missing fallback, ordinary 429 credential rotation, API-key ok/exhausted/dead pool state, sticky env/config source suppression, centralized source-removal registry, provider-specific singleton/source removal for Nous/Codex/xAI/Qwen/MiniMax, borrowed-source removal suppression/hints, Qwen CLI reference-only import, CLI removal-hint surfacing, soft leases, provider-wide account breaker state, OAuth refresh/exhaust/quarantine reporting, provider-call handoff, fallback endpoint/key-env hints, per-turn primary restore, borrowed external OAuth resync, and Claude Code reference-only import | done for first pass | Carry deeper account-bucket classifiers and live provider singleton smoke only if later audits find gaps | Stage V provider/auth/credential tests plus reliability/provider guardrails |
| W | Observability, traces, costs, audit artifacts | Hermes canonical usage/cost buckets, activity/touch/status callbacks, account/credits/billing fail-open views, trajectory/audit exports, and child/delegation usage visibility | AEGIS now has canonical-ish usage summaries, joinable usage log records, cost status/source/labels, trace/provider-span cost parity, session last-turn cost metadata, child subagent usage/cost completion payloads for sync and background delegation, stamped fail-soft events, `Agent.get_activity_summary()`, and `TraceStore.timeline()` audit rows | done for first pass | Carry account/credits/billing live API depth and per-attempt child trace spans into later provider/UX passes if required | Stage W usage/activity/timeline tests plus observability/RPC/SDK/live-activity guardrails |
| X | CLI/onboarding surfaces | Hermes CLI setup richer than AEGIS | AEGIS installer/onboarding first pass landed | later | Defer until core harness A-W stabilizes | installer/product tests |
| Y | Gateway/background/channels | Hermes gateway/background rich stack | AEGIS gateway/background exists; Stage Z core approval replies now resolve the shared pending approval queue with prompt/user/session validation | later | Defer per user request except core lifecycle links | gateway approval queue tests; deeper channel/product work later |
| Z | Full parity gate and release readiness | Hermes full harness behavior under long sessions | AEGIS not yet full parity; Stage Z now has green focused/broad proof for MCP lifecycle/OAuth/elicitation/sampling/post-tool-idle-sampling/roots/notifications/completion slices, lifecycle/transport generation ownership and stale-reader suppression, optional SDK same-task `ClientSession` ownership with callback adapters plus a real installed `mcp` SDK stdio server proof, provider source suppression/centralized removal registry/provider-specific singleton parity/leases/account breaker/borrowed-token resync/removal hints/CLI hint surfacing, no-progress and multi-pass compaction, DB/FTS/WAL repair backups, sidecar recovery, safe SQLite WAL checkpoint replay from copied sidecars, malformed-repair status metadata, cross-process repair locks, manual repair-backup restore, backend interrupt capture, Modal/Daytona fake-SDK sync plus Modal snapshot restore, remote sync diagnostics/passive SDK credential checks, redacted fail-closed remote creation diagnostics, gated remote live-proof readiness, prompt snapshots, tool-result persistence/rehydration, and append-row archival compaction | active | Continue re-audit on credentialed live Modal/Daytona proof and full Hermes reading coverage before any parity claim | broad full-suite regression, full-suite-ready gate, and manual smoke when live provider credentials are intentionally enabled |

## Completed Core Harness Work

- Neutral reference-audit script added: `scripts/audit_reference_compare.py`.
- Local generated audit output ignored via `.gitignore`.
- AEGIS MIT license file and package license metadata added.
- Startup bootstrap module added for Windows UTF-8 stdio safety.
- Installer stage manifest and stage execution landed before the current
  harness-only reset.
- Tool-loop hard-stop behavior landed for repeated no-progress read-only calls,
  exact repeated failures, and varied same-tool failures.
- Invalid tool-call recovery landed for unknown, empty, and provider-unsafe tool
  names with bounded correction loops.
- Verify-after-edit helper and opt-in loop gate landed for successful code edits.
- Stage A landed: turn-start persistence before provider calls.
- Stage B landed: non-forced system prompt ensure reuses stored prompt bytes
  without rebuilding, preserving prompt-cache stability.
- Stage C landed: per-turn prologue state reset, durable turn/API request
  metadata, direct `run_conversation()` prologue fallback, and turn-local tool
  count decisions.
- Stage D user-message/request-copy slice landed: wakeups, skill scaffolding,
  retrieved memory, and `pre_llm_call` plugin context now affect provider copies
  without persisting into canonical user turns or memory sync.
- Stage D prompt-side volatile tier landed: `memory` and `environment` prompt
  parts are no longer stored in the canonical system message; they are appended
  only to provider-wire system-message copies.
- Stage E runtime-selection slice landed: prompt routes and budget downshifts
  now record `runtime_selection`, emit structured route/downshift events, use
  one-turn provider swaps, and restore the base provider after the turn.
- Stage E budget/readiness slice landed: hard budget blocks now return before
  provider execution, and provider/model/auth readiness checks run before normal
  and grace provider calls.
- Stage F persistent governance slice landed: malformed canonical tool-call ids,
  names, arguments, invalid roles, active tool-result groups, orphan results, and
  adjacent user messages are repaired before provider-wire copy.
- Stage F wire-only governance slice landed: thinking-only assistant turns are
  dropped from provider request copies only, and adjacent user messages left by
  that drop are merged while canonical Claude/Anthropic thinking blocks remain
  intact.
- Stage G streaming/provider lifecycle slice landed: normal and grace streamed
  assistant deltas now pass through an AEGIS-owned stateful thinking scrubber
  before `assistant_delta`, while provider observers/API request ids remain
  stable and current request ids clear after provider completion.
- Stage H response-normalization slice landed: provider responses are normalized
  before persistence/display so inline reasoning and standalone tool-call XML
  are stripped from visible text, inline reasoning is preserved when structured
  reasoning is absent, obvious assistant-text secrets are redacted, surrogates
  are cleaned, and Anthropic thinking blocks remain intact.
- Stage J checkpoint-ordering slice landed: `ToolExecutor` now checkpoints only
  after permission and loop-guard approval, destructive bash overwrite
  redirections snapshot the target before execution, and resume one-turn routing
  restore was fixed after broad verification exposed it.
- Stage L task-aware file-state slice landed: file tools now record per-task
  reads/writes, warn when a sibling task wrote after this task's last read,
  warn after partial/paginated reads, serialize per-path writes, and refresh
  stamps after successful writes/patches.
- Stage N compaction durability slice landed: automatic in-place compaction,
  aborted automatic compaction, and overflow-recovery compaction now record
  metadata and save the compacted session immediately, before the next provider
  call or retry.
- Stage O overflow recovery slice landed: payload-too-large errors are now
  compressible without provider fallback, unsupported `max_tokens` request
  validation no longer routes into compaction, output-cap overflow retries once
  with a reduced ephemeral `max_tokens`, and chat-completions sends `max_tokens`
  on the wire.
- Stage P continuation slice landed: truncated text continuations are bounded
  and preserve partial chunks, continuation retries boost ephemeral output caps,
  post-tool empty replies get one valid-sequence nudge, plain empty replies
  retry before terminal finalization, and thinking-only responses get bounded
  visible-answer recovery without exposing private reasoning.
- Stage R finalization slice landed: synthesized visible final text is now
  canonical before persistence and memory sync, cancelled tool tails close with
  assistant messages, memory/review skip interrupted or empty finals, cleanup
  failures are recorded fail-open, and trajectory capture is independent.
- Stage S session lifecycle slice landed: resume now resolves compressed
  parents/titles/prefixes to the live compression tip, branch/delegate/subagent
  children are explicitly excluded from resume projection, branch helpers record
  Hermes-style markers while preserving runtime metadata, and SDK/terminal/TUI/
  surface resume paths use the same resolver.
- Stage T subagent/delegation slice landed: child agents now inherit a scoped
  runtime, cannot widen parent toolsets through aliases, skip parent memory, get
  role/depth/session/provider/model identity in events and metadata, expose
  active list/cancel state, and record richer background completion/list data.
- Stage U MCP/deferred/schema slice landed: deferred tools now use scoped
  `tool_search`/`tool_describe`/`tool_call` bridge contracts, direct/core tools
  are protected from over-broad deferral, MCP schemas route through the central
  provider sanitizer, and schema recovery helpers cover nullable unions,
  required pruning, provider-hostile combinators, pattern/format, and slash
  enums.
- Stage V provider/auth/fallback slice landed: provider failures now use a
  Hermes-style structured taxonomy, ordinary 429/auth/billing failures can
  rotate or exhaust credentials before fallback, overload/usage-limit paths avoid
  wrong credential churn, content-policy/model-missing errors can try configured
  fallbacks, deterministic payload/context/thinking/provider-policy errors stop
  provider fallback, API-key pools persist `ok`/`exhausted`/`dead` state, OAuth
  auth refreshes/quarantines/exhausts locally, fallback rows can carry
  endpoint/key-env hints, real provider calls hand off to the fallback wrapper,
  and fallback-active providers restore primary at the next turn boundary.
- Stage W observability/cost/audit slice landed: usage logging now records
  Hermes-style token summaries, cost status/source/labels, and
  session/turn/trace/run ids; provider trace cost now uses the same
  cache-write-aware calculator as `aegis cost`; session metadata carries
  last-turn cost evidence; loop events are stamped and fail-soft relayed to
  `Agent.event_callback`; activity heartbeat/summary surfaces current API/tool
  state; and `TraceStore.timeline()` exports ordered prompt/provider/attempt/
  tool/compaction/final audit rows.

## Most Recent Completed Patch

Latest integrated Stage Z patch wave, verified 2026-06-30:

- MCP SDK lifecycle parity: `aegis/mcp/client.py` now has an opt-in SDK
  transport path (`sdk`/`use_sdk`) with import gates, one-owner-task
  `ClientSession` lifecycle/RPC execution, and SDK-native sampling,
  elicitation, roots, message, and logging callback adapters. The hand-rolled
  JSON-RPC path remains the default when the SDK is absent.
- Provider singleton/source parity: `aegis/providers/auth.py` now removes and
  suppresses Hermes-shaped provider singletons under `providers.<provider>` for
  Nous `device_code`, Codex `device_code`/`manual:device_code`, xAI
  `loopback_pkce`, Qwen CLI `qwen-cli`, and MiniMax `oauth`, including Qwen CLI
  reference-only OAuth resync from `~/.qwen/oauth_creds.json` without
  persisting borrowed secrets.
- Session repair policy parity: `aegis/session.py` now serializes WAL sidecar
  and malformed-schema repair surgery with a sidecar `state.db.repair.lock`,
  records repair-lock evidence in repair status files, and exposes locked
  manual restore from preserved repair backups while preserving the current DB
  first.
- Subagents used for this wave: Avicenna on MCP SDK lifecycle/callbacks and
  Rawls on provider singleton/source parity. The main agent handled the
  session repair-lock/manual-restore lane after reading Hermes state repair and
  doctor/session repair references directly.

Hermes reference read for this latest wave:

- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`: `190-285`,
  `838-1220`, `1227-1418`, `1420-1625`, `1838-1905`, `2048-2225`,
  `3326-3336`, `3978-4010`, `4390-4410`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_tool.py`:
  `807-1030`, `3148-3265`
- Hermes MCP related tests: `test_mcp_elicitation.py`, `test_mcp_dynamic_discovery.py`,
  `test_mcp_stability.py`, `test_mcp_sse_transport.py`,
  `test_mcp_tool_issue_948.py`, and `test_mcp_cancelled_error_propagation.py`
  targeted ranges
- `/home/alienai/.hermes/hermes-agent/agent/credential_sources.py`: `1-448`
- `/home/alienai/.hermes/hermes-agent/agent/credential_pool.py`: `508-760`,
  `1652-2044`
- `/home/alienai/.hermes/hermes-agent/providers/base.py`: `1-217`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth_commands.py`: `1-780`
- Hermes provider/auth tests: `test_auth_commands.py` targeted ranges
  `132-310`, `511-570`, `1199-1460`, `1638-1815`, `1832-1940`; and
  `test_auth_xai_oauth_provider.py` `1164-1280`
- `/home/alienai/.hermes/hermes-agent/hermes_state.py`: `120-700`,
  `780-1175`, `1230-1490`
- `/home/alienai/.hermes/hermes-agent/tests/test_state_db_malformed_repair.py`:
  `1-255`
- `/home/alienai/.hermes/hermes-agent/tests/test_hermes_state_wal_fallback.py`:
  `1-260`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/main.py`: `13180-13235`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/doctor.py`: `1200-1290`

Focused verification for this latest wave:

```bash
python -m pytest -q tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sdk_callbacks.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py tests/test_stage_z_session_repair.py tests/test_session_locks.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_compaction_stage_n.py tests/test_stage_z_append_row_persistence.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_smoke.py tests/test_agent_perms.py tests/test_coding_context.py tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_memory_wiring.py tests/test_reliability.py tests/test_resilience.py tests/test_thinking_recovery.py tests/test_bootstrap.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_file_state_stage_l.py tests/test_finalization_stage_r.py tests/test_governance_stage_f.py tests/test_overflow_stage_o.py tests/test_provider_lifecycle_stage_g.py tests/test_request_wire_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_response_normalization_stage_h.py tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_skills_memory.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_w_usage_cost.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_session_repair.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_remote_environments.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_streaming_think_scrubber_stage_g.py tests/test_subagent_background_stage_t.py tests/test_subagent_stage_t.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_credential_pools.py tests/test_auth_cli.py tests/test_observability.py tests/test_session_locks.py
python -m compileall -q aegis
ruff check aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sdk_callbacks.py aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py
python -m py_compile aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sdk_callbacks.py aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py
git diff --check -- aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sdk_callbacks.py aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py BUILD_STATUS.md
python scripts/audit_reference_compare.py
```

Results: combined MCP/provider/session focused slice `78 passed`; broader
session/compaction slice `34 passed`; broad core harness sweep `564 passed in
44.93s`; `python -m compileall -q aegis`, ruff, py_compile, and diff
whitespace checks are clean for touched files. The mechanical inventory
regenerated as 1,023 AEGIS source-like files / 295,367 LOC vs 5,459 Hermes
source-like files / 2,310,169 LOC.

Previous integrated Stage Z patch wave, verified 2026-06-30:

- Provider/auth source-removal parity: `aegis/providers/auth.py` now has a
  centralized source-removal registry for borrowed external references,
  environment, config/model-config, Codex CLI, Claude Code, and manual sources.
  `AuthStore.delete()` now uses that registry for structured hints and source
  suppression decisions without leaking raw secrets or copying Claude tokens.
- MCP lifecycle parity: `aegis/mcp/client.py` now records lifecycle owner and
  transport-generation metadata, suppresses stale stdio/SSE readers from old
  generations, keeps bounded idle-sampling snapshots, and fails closed when an
  expired idle sampling context cannot safely service a server request.
- Session repair parity: `aegis/session.py` now records canonical row evidence
  for repair decisions, writes malformed-repair status metadata next to raw
  backups, and refuses repeated unsafe malformed DB repair attempts with a
  per-process one-shot guard.
- Remote backend proof-depth parity: `aegis/tools/backends.py` now exposes
  passive `remote_backend_diagnostics()` SDK/credential/config checks and
  fails closed before live Modal lookup/creation when credentials or config are
  absent.
- Subagents used for this wave: Heisenberg on MCP lifecycle/idle sampling,
  Carver on remote backend diagnostics, and Planck on session repair metadata.
  The main agent handled the provider-source registry after reading Hermes
  credential-source code directly.

Hermes reference read for this latest wave:

- `/home/alienai/.hermes/hermes-agent/agent/credential_sources.py`: `1-520`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth_commands.py`: `430-500`
- `/home/alienai/.hermes/hermes-agent/agent/credential_pool.py`: `1180-1235`
- `/home/alienai/.hermes/hermes-agent/tests/agent/test_credential_pool.py`:
  `2651-2758`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`: `1-620`,
  `838-1310`, `1419-2510`, `3260-3585`, `4722-4915`
- Hermes MCP tests: `tests/tools/test_mcp_tool.py` targeted ranges plus
  `test_mcp_dynamic_discovery.py`, `test_mcp_elicitation.py`,
  `test_mcp_reconnect_signal.py`, `test_mcp_stability.py`,
  `test_mcp_cancelled_error_propagation.py`, and `test_mcp_sse_transport.py`
- `/home/alienai/.hermes/hermes-agent/hermes_state.py`: `127-355`,
  `355-635`, `798-1155`, `1239-1470`
- Hermes session repair tests: `test_state_db_malformed_repair.py` and
  `test_hermes_state_wal_fallback.py` targeted ranges
- `/home/alienai/.hermes/hermes-agent/tools/execution_environment/daytona.py`:
  `1-270`
- `/home/alienai/.hermes/hermes-agent/tools/execution_environment/modal.py`:
  `1-478`
- `/home/alienai/.hermes/hermes-agent/tools/execution_environment/file_sync.py`:
  `1-403`
- `/home/alienai/.hermes/hermes-agent/tools/terminal_tool.py`: `560-760`,
  `1240-1510`, `2749-2856`
- Hermes remote tests: `test_modal_bulk_upload.py`, `test_sync_back_backends.py`,
  `test_daytona_environment.py`, `test_file_sync.py`, and
  `test_file_sync_back.py` targeted ranges

Focused verification for this latest wave:

```bash
python -m pytest -q tests/test_stage_v_auth_integration.py tests/test_auth_cli.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_auth_cli.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_z_provider_rate_controls.py tests/test_credential_pools.py tests/test_observability.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_stage_z_session_repair.py tests/test_session_locks.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py
python -m pytest -q tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_mcp_client_capabilities.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py
python -m pytest -q tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_smoke.py tests/test_agent_perms.py tests/test_coding_context.py tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_memory_wiring.py tests/test_reliability.py tests/test_resilience.py tests/test_thinking_recovery.py tests/test_bootstrap.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_file_state_stage_l.py tests/test_finalization_stage_r.py tests/test_governance_stage_f.py tests/test_overflow_stage_o.py tests/test_provider_lifecycle_stage_g.py tests/test_request_wire_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_response_normalization_stage_h.py tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_skills_memory.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_w_usage_cost.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_session_repair.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_remote_environments.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_streaming_think_scrubber_stage_g.py tests/test_subagent_background_stage_t.py tests/test_subagent_stage_t.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_credential_pools.py tests/test_auth_cli.py tests/test_observability.py tests/test_session_locks.py
python -m compileall -q aegis
ruff check aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
python -m py_compile aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
git diff --check -- aegis/providers/auth.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py aegis/session.py tests/test_stage_z_session_repair.py tests/test_session_locks.py aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py BUILD_STATUS.md
python scripts/audit_reference_compare.py
```

Results: provider/auth focused slice `30 passed`; integrated nearby Stage Z
slice `179 passed`; session repair focused slice `11 passed`; MCP focused
slice `105 passed`; remote backend focused slice `19 passed`; broad core
harness sweep `552 passed in 38.55s`; `python -m compileall -q aegis`, ruff,
py_compile, and diff whitespace checks are clean for touched files. The
mechanical inventory regenerated as 1,022 AEGIS source-like files / 293,560 LOC
vs 5,459 Hermes source-like files / 2,310,169 LOC.

Previous integrated Stage Z patch wave, verified 2026-06-30:

- Provider/auth CLI parity: `aegis auth remove <provider>` and `aegis auth
  logout <provider>` now surface structured `AuthRemovalResult` details,
  including pooled credential counts, suppressed borrowed sources, and
  provider-specific hints, without printing raw OAuth/API secrets.
- MCP post-tool idle sampling parity: `aegis/mcp/client.py` now captures a
  narrow sampling context snapshot from MCP tool calls and can satisfy later
  server-initiated `sampling/createMessage` requests after the active tool call
  has returned. Lifecycle keepalive timing was adjusted so idle server requests
  do not race immediate pings.
- Remote sandbox sync parity: `aegis/tools/backends.py` now records retry/final
  sync-back diagnostics, records oversized/unstatable downloaded tar failures,
  raises on Daytona process-exec tar upload fallback failures, and has fake-SDK
  proof that Modal bulk upload streams data through stdin chunks instead of
  embedding payload bytes in command text.
- WAL preservation-policy parity: `aegis/session.py` now writes deterministic
  `*.repair.json` status metadata for WAL-sidecar repair attempts, including
  preserved backup paths, health probes, sidecar actions, failure stage/result,
  and explicit `safe_replay_possible: false` when AEGIS refuses unsafe replay.

Hermes reference read for this latest wave:

- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth.py`: `1232-1370`,
  `1450-1490`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth_commands.py`: `150-205`,
  `442-481`
- `/home/alienai/.hermes/hermes-agent/tests/hermes_cli/test_auth_commands.py`:
  `540-730`, `1060-1145`, `1460-1545`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`: `1-82`,
  `230-279`, `838-1220`, `1253-1375`, `1414-1680`, `1680-1905`,
  `2048-2220`, `2260-2295`, `4718-4730`
- Hermes MCP tests: `tests/tools/test_mcp_tool.py:2422-3265`,
  `test_mcp_capability_gating.py:1-260`,
  `test_mcp_utility_capability_gating.py:1-180`
- `/home/alienai/.hermes/hermes-agent/tools/environments/daytona.py`: `1-270`
- `/home/alienai/.hermes/hermes-agent/tools/environments/modal.py`: `1-478`
- `/home/alienai/.hermes/hermes-agent/tools/environments/file_sync.py`:
  `1-403`
- `/home/alienai/.hermes/hermes-agent/tools/terminal_tool.py`: `1360-1510`,
  `1970-2235`, `2700-2725`
- Hermes remote tests: `test_modal_bulk_upload.py:1-294`,
  `test_sync_back_backends.py:1-494`, `test_daytona_environment.py:1-442`,
  `test_file_sync.py:1-311`, `test_file_sync_back.py:1-473`
- `/home/alienai/.hermes/hermes-agent/hermes_state.py`: `127-355`,
  `355-635`, `798-890`, `1060-1155`
- `/home/alienai/.hermes/hermes-agent/tests/test_state_db_malformed_repair.py`:
  `1-380`
- `/home/alienai/.hermes/hermes-agent/tests/test_hermes_state_wal_fallback.py`:
  `1-390`

Focused verification for this latest wave:

```bash
python -m pytest -q tests/test_auth_cli.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_credential_pools.py
python -m pytest -q tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_mcp_client_capabilities.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py
python -m pytest -q tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_auth_cli.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_stage_z_session_repair.py tests/test_session_locks.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py tests/test_credential_pools.py tests/test_observability.py
```

Results: auth/provider focused slice `29 passed`; MCP focused slice `79
passed`; remote backend focused slice `17 passed`; session repair focused slice
`9 passed`; integrated Stage Z slice `178 passed`; broad core harness sweep
`543 passed in 37.42s`. `python -m compileall -q aegis`, ruff, py_compile, and
diff whitespace checks are clean for touched files. `scripts/audit_reference_compare.py`
regenerated inventory as 1,022 AEGIS source-like files / 292,405 LOC vs 5,459
Hermes source-like files / 2,310,169 LOC.

Previous integrated Stage Z patch wave, verified 2026-06-30:

- MCP capability parity: `aegis/mcp/client.py` now accepts configured roots,
  advertises roots support, answers `roots/list`, records redacted progress and
  logging/resource/prompt notifications for inspection, exposes a
  `completion/complete` helper, and allows catalog `roots` entries.
- Remote sandbox parity: `aegis/tools/backends.py` now has Modal snapshot
  store helpers, `direct:<task_id>` restore, legacy snapshot-key migration,
  stale snapshot pruning/fallback to the base image, and cleanup-time snapshot
  save before terminate.
- Provider/auth source-removal parity: `aegis/providers/auth.py` now returns
  structured removal results with provider-specific hints, suppresses borrowed
  Codex/Claude-style sources instead of copying secrets, filters suppressed
  pooled OAuth entries, and clears suppression when an explicit local save
  happens. `aegis/credentials.py` preserves sticky suppressed sources across
  runtime state reset.
- Session DB recovery parity: `aegis/session.py` now detects corrupt WAL/SHM
  sidecars, backs up `state.db`, `state.db-wal`, and `state.db-shm`, recovers
  only when a copied main DB is healthy and has a `sessions` table, resets the
  main DB header to rollback-journal mode after sidecar removal, and requires a
  clean post-repair health probe for malformed-schema repair.

Hermes reference read for this latest wave:

- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`, MCP capability,
  request, notification, lifecycle, streamable HTTP, and server-state ranges:
  `190-360`, `2140-2285`, `2527-2715`
- `/home/alienai/.hermes/hermes-agent/tools/environments/modal.py`: `1-478`
- `/home/alienai/.hermes/hermes-agent/tools/environments/daytona.py`: `1-270`
- `/home/alienai/.hermes/hermes-agent/tools/environments/file_sync.py`: `1-403`
- `/home/alienai/.hermes/hermes-agent/tools/terminal_tool.py`: `1-360`,
  `860-930`, `1360-1510`, `1620-1665`
- Hermes Modal/Daytona/file-sync tests: `test_modal_snapshot_isolation.py`,
  `test_modal_bulk_upload.py`, `test_sync_back_backends.py`,
  `test_daytona_environment.py`, `test_file_sync.py`, `test_file_sync_back.py`
- `/home/alienai/.hermes/hermes-agent/agent/credential_sources.py`: `1-448`
- `/home/alienai/.hermes/hermes-agent/agent/credential_persistence.py`: `1-174`
- `/home/alienai/.hermes/hermes-agent/agent/credential_pool.py`: `1-230`,
  `451-546`, `601-1440`, `1600-2316`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth.py`: `1232-1370`,
  `1450-1490`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/auth_commands.py`: `150-205`,
  `442-481`
- Hermes auth tests: `tests/hermes_cli/test_auth_commands.py` ranges
  `540-730`, `1060-1438`, `1460-1940`; `tests/agent/test_credential_pool.py`
  ranges `864-1115`, `2651-2758`
- `/home/alienai/.hermes/hermes-agent/hermes_state.py`: `120-355`,
  `355-635`, `795-895`, `1061-1149`, `1239-1474`, `5323-5362`
- `/home/alienai/.hermes/hermes-agent/tests/test_state_db_malformed_repair.py`:
  `1-358`
- `/home/alienai/.hermes/hermes-agent/tests/test_hermes_state_wal_fallback.py`:
  `1-369`

Focused verification for this latest wave:

```bash
python -m pytest -q tests/test_stage_z_mcp_client_capabilities.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
python -m pytest -q tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py
python -m pytest -q tests/test_stage_z_provider_pooling.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py tests/test_credential_pools.py tests/test_observability.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py tests/test_credential_pools.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py
ruff check aegis/session.py tests/test_stage_z_session_repair.py aegis/providers/auth.py aegis/credentials.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_auth_integration.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py aegis/mcp/client.py tests/test_stage_z_mcp_client_capabilities.py
python -m py_compile aegis/session.py tests/test_stage_z_session_repair.py aegis/providers/auth.py aegis/credentials.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py aegis/mcp/client.py tests/test_stage_z_mcp_client_capabilities.py
git diff --check -- aegis/session.py tests/test_stage_z_session_repair.py aegis/providers/auth.py aegis/credentials.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_auth_integration.py aegis/tools/backends.py tests/test_stage_z_remote_environments.py aegis/mcp/client.py tests/test_stage_z_mcp_client_capabilities.py
```

Results: MCP focused slice `29 passed`; remote backend focused slice `14
passed`; provider/auth focused slice `59 passed`; session repair focused slice
`8 passed`; combined integration slice `117 passed`; ruff clean; Python
compilation clean; diff whitespace clean. The broad core harness sweep passed
`537 passed in 38.40s`. `python -m compileall -q aegis` is clean.
`scripts/audit_reference_compare.py` regenerated inventory as 1,021 AEGIS
source-like files / 291,681 LOC vs 5,459 Hermes source-like files / 2,310,169
LOC.

Previous Stage Z patch wave, verified 2026-06-30:

- Provider/auth pooling parity: `aegis/credentials.py` now tags config/env
  credential sources, keeps sticky source suppression in the shared credential
  state, prevents suppressed env sources from silently reseeding on reload,
  exposes soft nonblocking credential leases, and records provider-wide account
  breaker state. `ApiKeyAuth` exposes acquire/release lease helpers while
  preserving existing header behavior.
- Session/compaction parity: automatic compaction now records durable
  `compression_no_progress` metadata, increments low-savings counts, persists
  the guard through session reload, skips repeated ineffective automatic
  compactions after two low-savings passes, and lets manual/overflow compaction
  continue clearing the guard when real progress is made.
- Backend interruption parity: non-local SSH/Singularity-style backend execution
  now uses tracked live subprocess/process-group capture under the interrupt
  wrapper, so interrupts terminate the actual backend client and return 130
  instead of merely abandoning a daemon worker.
- Prompt/cache parity: `PromptBuild.snapshot()` records deterministic
  non-volatile prompt, stable, context, and skills fingerprints in session
  metadata and `prompt_audit.cache`, while provider-wire volatile context stays
  out of the stored snapshot.
- Session DB repair parity: corrupt `sessions.data` JSON now recovers a
  loadable session from active message rows, records `_session_repair`
  metadata, and rewrites the repaired snapshot when the store is writable.
- Provider account-breaker classifier parity: fallback classification now parses
  Hermes-style `x-ratelimit-*` buckets and trips provider-wide account cooldowns
  only when exhausted buckets have long reset horizons; healthy or short-reset
  429s remain fallback-only.
- Skills prompt parity: `SkillsLoader` now persists a disk-backed
  `skills_prompt_snapshot.json` with metadata, invalidates it by discovery
  signature, keeps clipped names visible, and supports Hermes-style
  focus-mode category demotion to `[names only]` without hiding skill names.
- MCP transport parity: HTTP MCP now handles Streamable HTTP
  `text/event-stream` responses until the matching JSON-RPC id, supports legacy
  `transport="sse"` endpoint discovery/background reading/idle notifications,
  and extends reconnect retry to resource/prompt utility calls.
- Tool-result persistence parity: oversized tool outputs now write content plus
  `.metadata.json` sidecars with SHA-256/byte/char accounting, environment/local
  storage metadata, parse/load helpers, and verified rehydration support.

Hermes reference read for this wave:

- `/home/alienai/.hermes/hermes-agent/agent/credential_sources.py`, source
  removal/suppression contract: `1-260`
- `/home/alienai/.hermes/hermes-agent/agent/credential_persistence.py`,
  borrowed/reference-only credential persistence: `1-280`
- `/home/alienai/.hermes/hermes-agent/agent/credential_pool.py`, source gates,
  reseeding, leases, and pool load behavior: `1500-1605`, `1720-2325`
- `/home/alienai/.hermes/hermes-agent/agent/nous_rate_guard.py`, cross-session
  account breaker behavior: `1-320`
- `/home/alienai/.hermes/hermes-agent/docs/session-lifecycle.md`, full file
- `/home/alienai/.hermes/hermes-agent/hermes_state.py`, targeted session/repair
  and compaction state ranges: `127-625`, `625-920`, `1489-1668`,
  `1728-1885`, `2885-3265`, `3390-3608`, `3900-4188`
- `/home/alienai/.hermes/hermes-agent/agent/context_compressor.py`,
  no-progress and fallback compression ranges: `620-715`, `900-990`,
  `1426-1850`, `2025-2683`
- `/home/alienai/.hermes/hermes-agent/agent/conversation_compression.py`,
  durable compression lifecycle: `291-1090`
- `/home/alienai/.hermes/hermes-agent/tools/terminal_tool.py`, terminal backend
  process/interrupt/storage handoff ranges: `54-80`, `1373-1495`,
  `1523-1767`, `1960-2610`
- `/home/alienai/.hermes/hermes-agent/tools/interrupt.py`, full file
- `/home/alienai/.hermes/hermes-agent/tools/thread_context.py`, full file
- `/home/alienai/.hermes/hermes-agent/tools/tool_result_storage.py`, full file
- `/home/alienai/.hermes/hermes-agent/agent/system_prompt.py`, prompt-cache
  posture and stable prompt behavior: `1-620`
- `/home/alienai/.hermes/hermes-agent/agent/prompt_builder.py`, prompt/skills
  snapshot behavior: `1-320`, `1252-1688`, `1924-2075`
- `/home/alienai/.hermes/hermes-agent/tests/agent/test_system_prompt.py`,
  prompt-cache regressions: `1-180`
- `/home/alienai/.hermes/hermes-agent/docs/session-lifecycle.md`, session
  repair/resume contract: `1-260`
- `/home/alienai/.hermes/hermes-agent/tests/hermes_state/test_resolve_resume_session_id.py`,
  session resume repair coverage: `1-217`
- `/home/alienai/.hermes/hermes-agent/tests/hermes_state/test_session_archiving.py`,
  session archiving coverage: `1-51`
- `/home/alienai/.hermes/hermes-agent/agent/nous_rate_guard.py`,
  provider account breaker/rate guard: `1-332`
- `/home/alienai/.hermes/hermes-agent/agent/rate_limit_tracker.py`,
  rate-limit bucket tracking: `1-230`
- `/home/alienai/.hermes/hermes-agent/tests/agent/test_nous_rate_guard.py`,
  account breaker regressions: `250-400`
- `/home/alienai/.hermes/hermes-agent/tests/agent/test_rate_limit_tracker.py`,
  rate-limit tracker regressions: `1-230`
- `/home/alienai/.hermes/hermes-agent/agent/coding_context.py`,
  focus-mode compact skill category behavior: `270-330`, `500-628`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`, MCP SSE/lifecycle
  ranges: `1414-2255`, `2541-2915`, `3260-3695`, `3990-4225`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_oauth.py`, OAuth transport
  context: `1-840`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_oauth_manager.py`, OAuth
  manager context: `1-714`
- `/home/alienai/.hermes/hermes-agent/mcp_serve.py`, MCP server/SSE context:
  `1-904`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_tool_result_storage.py`,
  result-storage regressions: `1-550`
- `/home/alienai/.hermes/hermes-agent/agent/tool_executor.py`, tool-result
  persistence and classification ranges: `1-230`, `760-850`, `1430-1495`
- `/home/alienai/.hermes/hermes-agent/tools/budget_config.py`, result-size
  budget config: `1-114`
- `/home/alienai/.hermes/hermes-agent/agent/tool_result_classification.py`,
  result classification: `1-26`

Files touched by this wave:

| File | Change |
| --- | --- |
| `aegis/credentials.py` | Source-aware env/config seeding, sticky suppression, soft leases, provider-wide account breaker status, source/lease/breaker status reporting |
| `aegis/providers/auth.py` | API-key lease acquire/release surface without changing existing header acquisition semantics |
| `aegis/agent/compaction_runner.py` | Durable no-progress compaction guard, low-savings tracking, persisted skip behavior |
| `aegis/tools/backends.py` | Interrupt-aware live backend process capture for SSH/Singularity-style execution and shared backend env reuse |
| `aegis/agent/context.py` | Deterministic non-volatile prompt snapshot/fingerprints |
| `aegis/agent/agent.py` | Persisted prompt snapshot metadata and prompt-audit cache fingerprints |
| `aegis/session.py` | Corrupt snapshot recovery from active message rows plus writable repaired snapshot persistence |
| `aegis/providers/fallback.py` | Provider-specific rate-limit bucket parsing and account-breaker classification |
| `aegis/skills.py` | Disk-backed skills prompt snapshot, snapshot invalidation, category metadata, names-only demotion |
| `aegis/agent/coding_context.py` | Hermes-style `auto`/`focus`/`on`/`off` coding-context mode normalization and compact skill category selector |
| `aegis/config.py` | Default `agent.coding_context="auto"` while preserving legacy boolean behavior |
| `aegis/mcp/client.py` | Streamable HTTP SSE response handling, legacy SSE transport, utility reconnect retry |
| `aegis/tools/tool_result_storage.py` | Metadata sidecars, checksum/size accounting, parse/load/rehydrate helpers |
| `aegis/tools/base.py` | Persisted-output classification as truncated and max-result-size metadata surface |
| `aegis/tools/registry.py` | Hermes-style `get_max_result_size()` helper |
| `aegis/tools/builtin.py` | High-output core tool result-size caps |
| `tests/test_stage_z_provider_pooling.py` | Source suppression/reseed, all-suppressed env fallback block, leases, account breaker regressions |
| `tests/test_compaction_stage_n.py` | Reload-persistent no-progress guard regression |
| `tests/test_stage_z_backend_interrupts.py` | Live backend client process interruption regression |
| `tests/test_smoke.py` | Prompt snapshot/cache/skills fingerprint regressions |

Verification for this wave:

```bash
python -m pytest -q tests/test_stage_z_provider_pooling.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_credential_pools.py
python -m pytest -q tests/test_session_stage_s.py tests/test_compaction_stage_n.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_tools.py
python -m pytest -q tests/test_smoke.py::test_agent_system_prompt_includes_runtime_auth tests/test_smoke.py::test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild tests/test_smoke.py::test_provider_wire_volatile_context_does_not_churn_prompt_snapshot tests/test_smoke.py::test_skills_index_snapshot_fingerprint_tracks_rebuilt_skill_context tests/test_skills_memory.py::test_skill_discovery_refreshes_when_files_change tests/test_skills_memory.py::test_skill_tool_create_updates_same_turn_prompt_index tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python -m pytest -q tests/test_smoke.py tests/test_resilience.py tests/test_agent_perms.py tests/test_memory_wiring.py tests/test_memory_lifecycle.py tests/test_skills_memory.py tests/test_wakeups.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_governance_stage_f.py tests/test_request_wire_stage_f.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_tool_executor_stage_j.py tests/test_file_state_stage_l.py tests/test_self_verify.py tests/test_compaction_stage_n.py tests/test_overflow_stage_o.py tests/test_continuation_stage_p.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_session_config.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_noninteractive_oauth.py tests/test_stage_z_mcp_reconnect_parking.py
ruff check aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python -m py_compile aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
git diff --check -- aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python scripts/audit_reference_compare.py
```

Results: provider/auth focused slice `21 passed`; combined session/provider/
backend slice `91 passed`; prompt/provider/backend/compaction focused slice `19
passed`; final broad core harness sweep `372 passed in 20.43s`; ruff clean;
Python compilation clean; diff whitespace clean; mechanical inventory
regenerated.

Previous Stage Z patch wave:

Stage Z approval/thread-context production wiring, gateway approval-queue
resolution, environment-backed tool-result storage plus non-local backend
handoff, tool-persistence integration, bounded MCP live-lifecycle refresh, MCP
reconnect parking/circuit-breaker behavior, stdio background notification
handling, and HTTP MCP disk-backed OAuth token refresh/cache/dedup recovery are
the latest focused core-harness slices. This does not claim full Hermes parity
yet. AEGIS now has owned primitives for
context-local approval/session
metadata, thread-context propagation, per-thread interrupt state, and pending
approval queues with interrupt-aware waits plus prompt-id targeted resolution.
`Agent.run()` now binds the active session key and context-local approver for
the turn, `Agent.cancel()` marks the active run thread interrupted, and
`ToolExecutor.execute_one_raw()` binds turn/tool-call ids around
permission/tool execution so queued approvals are traceable.
`PermissionEngine.authorize()` now preserves direct `ctx.approver` behavior
first, falls back to the context-local approver, then uses the pending approval
queue only when an explicit session notifier is registered, failing closed when
none exists. Gateway turns register a session notifier for that shared queue,
surface queued exec approvals through the platform adapter, and resolve inbound
approval replies after prompt/user/session validation; prompt-id callbacks
resolve the matching pending item even when multiple approvals are waiting in a
session. The main tool executor also uses the thread-context propagator for
parallel tool workers, persists transcript-safe tool-result snapshots as each
result becomes durable, records explicit skipped tool messages when a turn is
cancelled before later sequential calls, and now persists oversized tool output
through an explicit execution environment when available before falling back to
AEGIS' local `tool_outputs` store. Non-local terminal backend creation now
caches/reuses active backend environments by `(backend, task_id)`, so foreground
`bash`/`run_command`, background `bash`, and `process start` paths leave an
environment available for later large-result persistence. Local fallback
tool-output persistence now writes through a same-directory temp file, flushes
and fsyncs, preserves existing file mode when replacing an artifact, performs
atomic `os.replace`, and skips the active target during stale-output cleanup so
failed replacement does not delete the previous complete artifact.

The MCP client now tracks `notifications/tools/list_changed`,
caches/invalidates tool lists, repaves registered MCP tools through
`MCPManager.refresh_changed_tools()`, marks auth-refresh-needed state on HTTP
401/403/auth errors, and retries a tool call once after transport session
expiry. `Agent.refresh_mcp_tools()` and the conversation loop now wire that
manager refresh into the provider schema boundary, so a changed MCP catalog is
reflected before the next provider call and after same-turn MCP notifications.
Same-name MCP tool refreshes can replace stale descriptions/schemas without
weakening duplicate protection for normal tools. MCP also exposes a bounded
keepalive probe: `ping` first, `tools/list` fallback when ping is unsupported,
reconnect-needed state on liveness failure, and no-throw manager-level
keepalive reports. Model-facing MCP tool calls now treat Hermes' stale
transport markers (`ClosedResourceError`, closed transport/connection, broken
pipe, end-of-file, expired/unknown/terminated sessions) as reconnect-needed,
retry once when the failure happens during the call, park calls after background
keepalive marks the transport dead, short-circuit repeated failures with a
cooldown breaker, and half-open after cooldown so a successful probe resets the
server.
For stdio MCP servers, AEGIS now starts a persistent stdout reader thread when
the server is spawned. The reader continuously consumes JSON-RPC lines,
dispatches id-less `notifications/tools/list_changed` messages while idle, and
queues id-bearing responses for request waiters, so dynamic tool updates do not
depend on a request currently polling stdout.
For HTTP MCP servers, AEGIS can now attach an `OAuthAuth` instance backed by
the local `AuthStore`, load `mcp:<server>` disk tokens into the Authorization
header, refresh expired tokens before traffic, refresh and retry once after
401/403 auth failures, and mark `auth_refresh_needed` when no local recovery is
possible.
The MCP OAuth path now also routes through an AEGIS-owned
`MCPOAuthManager`, caching auth handles by server/url/config/auth-store path,
tracking auth-store mtime so externally refreshed disk tokens can be noticed by
already-built clients, and deduplicating same-token 401 recovery so concurrent
tool calls share one refresh result instead of stampeding the token endpoint.
When an HTTP MCP OAuth spec has no static `client_id`/`token_url`, AEGIS now
discovers protected-resource metadata and authorization-server metadata,
persists the discovered metadata and dynamically registered client info under
`$AEGIS_HOME/mcp-oauth/`, and reuses that cache on later manager builds so
expired disk tokens can refresh at the real token endpoint on cold start.
If that dynamically registered client is later rejected with `invalid_client`
by the token endpoint, AEGIS now backs up/removes the dynamic client cache,
drops the matching metadata cache, evicts the stale auth entry, and forces the
next manager build to rediscover and re-register. Static configured client ids
remain untouched because re-registration cannot repair user config.
AEGIS now also exposes a real `aegis mcp login <name>` path for uncached HTTP
MCP OAuth servers. The CLI accepts marker-only `auth: oauth` or full `oauth`
blocks, purges stale MCP-owned AuthStore/client/metadata cache before relogin,
discovers protected-resource and authorization-server metadata, dynamically
registers a loopback-redirect client when needed, runs the AEGIS PKCE
authorization-code flow, persists tokens under `mcp:<server>`, and keeps static
configured client ids out of discovery/registration. Manual paste parsing now
accepts full callback URLs, provider callback URLs, bare query strings,
leading-`?` query strings, provider `code#state` values, authorization errors,
and skip/cancel tokens.
For stdio MCP servers, AEGIS now answers server-initiated JSON-RPC
`elicitation/create` requests from the reader thread instead of treating them
as active client responses. URL-mode elicitations fail closed with `decline`;
form-mode elicitations replay the captured turn context, use a direct
context-local approver when present, otherwise route through the pending
approval queue/notifier for gateway-style surfaces, and return `cancel` on
timeout or interrupt.

Hermes reference read:

- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`, targeted dynamic
  discovery/list_changed/reconnect/parking/keepalive/session-expiry ranges:
  `1-620`, `1414-1805`, `2220-2498`, `2540-2925`, `3268-3335`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_dynamic_discovery.py`,
  full file, 165 LOC
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_circuit_breaker.py`,
  targeted breaker half-open/parking recovery ranges: `1-260`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_reconnect_signal.py`,
  full file, 57 LOC
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_capability_gating.py`,
  targeted keepalive ping/list fallback and latch ranges: `1-390`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_tool_session_expired.py`,
  targeted stale transport/session marker and reconnect retry ranges: `1-220`
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_tool.py`, targeted
  background refresh task cleanup reference around `943`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_oauth.py`, OAuth token storage,
  expiry, metadata, dynamic client registration bootstrap, and non-interactive
  callback setup: full file, 840 LOC
- `/home/alienai/.hermes/hermes-agent/tools/mcp_oauth_manager.py`, provider
  cache/disk refresh, disk-watch invalidation, and 401 recovery ranges:
  full file, 714 LOC
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_oauth_metadata.py`,
  OAuth server metadata persistence and cold-load restore: full file, 213 LOC
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_mcp_oauth_manager.py`,
  invalid-client auto-heal reference ranges: `170-360`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_oauth.py`, interactive
  callback/paste/metadata/client-registration login ranges: `515-690`,
  `707-838`
- `/home/alienai/.hermes/hermes-agent/hermes_cli/mcp_config.py`, `mcp login`
  force-reauth/token-verification path: `650-720`
- `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`, elicitation
  schema-summary and `ElicitationHandler` ranges: `1224-1408`
- `/home/alienai/.hermes/hermes-agent/tools/thread_context.py`, full file, 120 LOC
- `/home/alienai/.hermes/hermes-agent/tools/interrupt.py`, full file, 98 LOC
- `/home/alienai/.hermes/hermes-agent/tools/approval.py`, targeted
  context-local and pending-approval ranges: `1-160`, `729-890`, `979-1090`,
  `1441-1560`, `1734-1848`, `2008-2050`, `2090-2145`
- `/home/alienai/.hermes/hermes-agent/gateway/slash_commands.py`, searched and
  targeted approval-command ranges for gateway approve/deny routing
- `/home/alienai/.hermes/hermes-agent/gateway/run.py`, searched and targeted
  gateway approval resolver ranges around `16719-16977`
- `/home/alienai/.hermes/hermes-agent/agent/tool_executor.py`, targeted
  per-tool persistence, cancellation, concurrent context, and result-storage
  ranges: `1-120`, `260-330`, `620-700`, `770-855`, `1430-1532`
- `/home/alienai/.hermes/hermes-agent/tools/terminal_tool.py`, targeted active
  environment cache/get-active and task-id handoff ranges: `1-110`, `1622-1650`,
  `1960-2245`
- `/home/alienai/.hermes/hermes-agent/tools/tool_result_storage.py`, full file,
  232 LOC, for environment-backed persisted large-output/result-storage
  behavior
- `/home/alienai/.hermes/hermes-agent/tools/budget_config.py`, targeted
  result/turn budget configuration ranges: `1-145`

Files touched:

| File | Change |
| --- | --- |
| `aegis/tools/thread_context.py` | Added approval contextvars, runtime backend/task/cwd/surface context propagation, notifier registration/introspection, FIFO pending approvals, resolve/clear helpers, and interrupt-aware approval waits |
| `aegis/tools/interrupt.py` | Added per-thread interrupt state, clear/check helpers, diagnostics snapshot, live interrupt hooks, and a compatibility event proxy |
| `aegis/tools/permissions.py` | Wired production approvals into context-local approvers and pending approval queues while preserving direct approver precedence, fail-closed no-notifier behavior, and backend/sandbox execution metadata in queued approval payloads |
| `aegis/tools/tool_result_storage.py` | Added Hermes-shaped large-result persistence helpers: environment-first writes through `env.execute(stdin_data=...)`, local fallback, bounded previews, and largest-first aggregate turn-budget enforcement |
| `aegis/gateway/base.py` | Added shared pending-approval prompt surfacing, inbound prompt/user/session validation, stale-prompt rejection, and prompt-id targeted pending-queue resolution |
| `aegis/gateway/runner.py` | Registered per-session gateway approval notifiers around agent turns, redacted approval payloads, and cleaned up outstanding shared approval prompts on turn exit |
| `aegis/agent/agent.py` | Bound session/approver context around turns, marked run-thread interrupts on cancel, and added Agent-level MCP refresh metadata/events |
| `aegis/agent/loop.py` | Integrated thread-context propagation into parallel tool execution; added per-result progress saves, append-row tool-result persistence, skipped-result messages for cancellation, per-tool approval correlation ids, environment-backed large-result storage, and live MCP schema refresh before provider calls |
| `aegis/tools/base.py` | Added `ToolContext.result_storage_env` so harness/tests/backend tools can hand the executor an explicit result-storage environment |
| `aegis/tools/backends.py` | Cached and reused non-local backend environments by `(backend, task_id)`, exposed effective backend/cwd context for approval payloads, and wrapped backend `execute()` calls with interrupt-aware cancel hooks/live subprocess capture |
| `aegis/session.py` | Added durable appended-message-row hydration and idempotent `append_messages()` persistence for tool-result rows before whole-session snapshots |
| `aegis/mcp/client.py` | Added list-change stale tracking, cached/forced tool lists, reconnect/auth-needed state, one-shot session/stale-transport retry, same-name manager repave refresh, ping/list keepalive probing, no-throw manager keepalive reports, reconnect parking, a cooldown/half-open circuit breaker, a persistent stdio reader that receives idle notifications and queues responses by id, HTTP OAuth manager-backed disk-token loading/expired-token refresh/401 retry, and stdio `elicitation/create` routing through approver/pending approval paths |
| `aegis/mcp/oauth_manager.py` | Added process-local MCP OAuth manager with cached auth handles, auth-store mtime invalidation, same-token 401 dedupe, protected-resource/auth-server metadata discovery, dynamic client registration bootstrap, disk cache/reuse, invalid-client dynamic-cache healing, forced-login purge, redirect-stable dynamic registration, CLI login entrypoint, and test reset hook |
| `aegis/mcp/__init__.py` | Exported MCP OAuth manager helpers for focused tests |
| `aegis/providers/auth.py` | Extended OAuth PKCE/manual login to parse Hermes-style callback URLs/query strings/errors/skip tokens and report localhost callback errors |
| `aegis/cli/main.py` | Replaced MCP OAuth login placeholder with real manager-backed PKCE login and added `mcp login --manual` |
| `aegis/tools/registry.py` | Added a small `deregister()` seam for live MCP repave |
| `tests/test_stage_z_thread_context.py` | Focused Stage Z regressions for context propagation/cleanup, per-thread interrupts, FIFO pending approvals, unregister cleanup, and interrupt-aware approval waits |
| `tests/test_stage_z_approval_wiring.py` | Focused Stage Z regressions for direct approver precedence, context-local fallback, queued allow, unregister-deny, and timeout-deny production approval paths |
| `tests/test_stage_z_gateway_approval_queue.py` | Focused Stage Z regressions for gateway replies resolving the shared pending queue, stale/wrong-session/wrong-user rejection, direct waiter preservation, and prompt-id matching with multiple pending approvals |
| `tests/test_stage_z_tool_persistence.py` | Focused Stage Z regressions for per-result save evidence, skipped cancelled tools, and context propagation in parallel tool workers |
| `tests/test_stage_z_append_row_persistence.py` | Focused Stage Z regressions for multiple durable appended tool rows, existing-session recovery from appended rows, and cancelled/skipped tool-row durability |
| `tests/test_stage_z_tool_result_storage.py` | Focused Stage Z regressions for environment-backed stdin persistence, ToolExecutor env handoff, local fallback, and largest-first aggregate spill behavior |
| `tests/test_stage_z_backend_storage_handoff.py` | Focused Stage Z regressions for non-local backend environment caching, foreground `run_command()` handoff, and `bash` foreground handoff into result storage |
| `tests/test_stage_z_backend_interrupts.py` | Focused Stage Z regressions for foreground local process kill, non-local backend cancel hooks, and process-tool interrupted status reporting |
| `tests/test_stage_z_sandbox_approval_context.py` | Focused Stage Z regressions for backend/sandbox metadata in bash/process approval queues and runtime context inheritance across worker threads |
| `tests/test_stage_z_mcp_lifecycle.py` | Focused Stage Z regressions for `tools/list_changed` repave, same-name schema replacement, Agent provider-boundary schema refresh, session-expired reconnect retry, auth-refresh-needed state, ping fallback keepalive, reconnect-needed marking, and manager keepalive reports |
| `tests/test_stage_z_mcp_reconnect_parking.py` | Focused Stage Z regressions for stale transport reconnect retry, keepalive-driven parked calls, breaker short-circuiting, half-open success reset, and half-open dead-transport reconnect requests |
| `tests/test_stage_z_mcp_background_notifications.py` | Focused Stage Z real-stdio regression for receiving `tools/list_changed` while no request is active, then refreshing registered tools from old to new |
| `tests/test_stage_z_mcp_oauth.py` | Focused Stage Z regressions for disk-backed MCP OAuth tokens, expired-token refresh before traffic, 401 refresh/retry, and unrecoverable 401 `auth_refresh_needed` state |
| `tests/test_stage_z_mcp_oauth_manager.py` | Focused Stage Z regressions for already-built clients picking up external disk token changes and wrapper-level same-token 401 refresh dedupe |
| `tests/test_stage_z_mcp_oauth_metadata.py` | Focused Stage Z regressions for metadata-derived MCP OAuth config, dynamic client registration, persisted metadata/client cache reuse, and expired-token cold-start refresh via discovered token endpoint |
| `tests/test_stage_z_mcp_oauth_invalid_client.py` | Focused Stage Z regressions for dynamic MCP OAuth invalid-client cache poisoning/re-registration and static configured client preservation |
| `tests/test_stage_z_mcp_oauth_login.py` | Focused Stage Z regressions for `aegis mcp login --manual`, dynamic/static MCP OAuth login, stale cache purge, redirect-stable client registration, AuthStore persistence, and Hermes-style manual callback parsing |
| `tests/test_stage_z_mcp_elicitation.py` | Focused Stage Z regressions for stdio `elicitation/create`, URL-mode decline, direct approver accept/decline, no-approval fail-closed, pending approval accept, pending approval timeout cancel, and request-id collision safety |

Verification:

```bash
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_stage_z_mcp_lifecycle.py
pytest -q tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_lifecycle.py
pytest -q tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
pytest -q tests/test_stage_z_mcp_elicitation.py
pytest -q tests/test_stage_z_mcp_oauth.py
pytest -q tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py
pytest -q tests/test_stage_z_mcp_oauth_metadata.py
pytest -q tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py
pytest -q tests/test_stage_z_mcp_oauth_invalid_client.py
pytest -q tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py
pytest -q tests/test_stage_z_mcp_oauth_login.py
pytest -q tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_mcp_cli.py
pytest -q tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
pytest -q tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
pytest -q tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
pytest -q tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py
pytest -q tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py
pytest -q tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py
python -m pytest -q tests/test_stage_z_thread_context.py
pytest -q tests/test_stage_z_tool_persistence.py
pytest -q tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_stage_w_activity.py
pytest -q tests/test_stage_z_mcp_lifecycle.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_u_deferred_bridge.py
pytest -q tests/test_stage_z_gateway_approval_queue.py
pytest -q tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_gateway_commands.py::test_gateway_wires_exec_approval_to_platform_adapter tests/test_gateway_commands.py::test_gateway_redacts_exec_approval_prompt_before_adapter tests/test_gateway_inbound.py::test_shared_inbound_exec_approval_waiter_uses_exec_prompt
pytest -q tests/test_stage_z_backend_storage_handoff.py
pytest -q tests/test_stage_z_tool_result_storage.py tests/test_observability.py::test_tool_output_spill_to_disk tests/test_observability.py::test_tool_output_spill_fallback_stays_bounded tests/test_observability.py::test_tool_turn_budget_spills_many_medium_outputs
pytest -q tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_tool_executor_stage_j.py tests/test_observability.py::test_tool_output_spill_to_disk tests/test_observability.py::test_tool_output_spill_fallback_stays_bounded tests/test_observability.py::test_tool_turn_budget_spills_many_medium_outputs tests/test_hardening.py::test_untrusted_tool_result_wrapped
pytest -q tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py tests/test_tools.py::test_bash_tool_background_uses_nonlocal_env_backend tests/test_tools.py::test_process_tool_start_uses_nonlocal_env_backend tests/test_tools.py::test_task_terminal_backend_override_changes_dispatch tests/test_tools.py::test_docker_backend_uses_task_image_override tests/test_tools.py::test_docker_backend_merges_config_and_env_extra_args tests/test_tools.py::test_local_environment_cache_is_scoped_to_aegis_home tests/test_tools.py::test_docker_environment_injects_task_id tests/test_tools.py::test_docker_environment_adds_extra_run_args_before_image
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_stage_w_activity.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_tool_executor_stage_j.py tests/test_file_state_stage_l.py tests/test_compaction_stage_n.py tests/test_overflow_stage_o.py tests/test_continuation_stage_p.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_hardening.py tests/test_thinking_recovery.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_gateway_commands.py::test_gateway_wires_exec_approval_to_platform_adapter tests/test_gateway_commands.py::test_gateway_redacts_exec_approval_prompt_before_adapter tests/test_gateway_inbound.py::test_shared_inbound_exec_approval_waiter_uses_exec_prompt tests/test_tools.py::test_bash_tool_background_uses_nonlocal_env_backend tests/test_tools.py::test_process_tool_start_uses_nonlocal_env_backend tests/test_tools.py::test_task_terminal_backend_override_changes_dispatch tests/test_tools.py::test_docker_backend_uses_task_image_override tests/test_tools.py::test_docker_backend_merges_config_and_env_extra_args tests/test_tools.py::test_docker_environment_adds_extra_run_args_before_image tests/test_providers.py::test_anthropic_coalesces_tool_results tests/test_smoke.py::test_oauth_configs_present tests/test_smoke.py::test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild
python -m py_compile aegis/tools/tool_result_storage.py aegis/tools/base.py aegis/tools/backends.py aegis/gateway/base.py aegis/gateway/runner.py aegis/tools/thread_context.py aegis/tools/permissions.py aegis/agent/agent.py aegis/agent/loop.py aegis/mcp/client.py aegis/mcp/oauth_manager.py aegis/mcp/__init__.py aegis/providers/auth.py aegis/cli/main.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_thread_context.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth_login.py
ruff check aegis/tools/tool_result_storage.py aegis/tools/base.py aegis/tools/backends.py aegis/gateway/base.py aegis/gateway/runner.py aegis/tools/thread_context.py aegis/tools/permissions.py aegis/agent/agent.py aegis/agent/loop.py aegis/mcp/client.py aegis/mcp/oauth_manager.py aegis/mcp/__init__.py aegis/providers/auth.py aegis/cli/main.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_thread_context.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth_login.py
python -m py_compile aegis/mcp/client.py aegis/tools/registry.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py
ruff check aegis/mcp/client.py aegis/tools/registry.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py
python -m py_compile aegis/mcp/oauth_manager.py aegis/mcp/client.py aegis/mcp/__init__.py aegis/providers/auth.py aegis/cli/main.py tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
ruff check aegis/mcp/oauth_manager.py aegis/mcp/client.py aegis/mcp/__init__.py aegis/providers/auth.py aegis/cli/main.py tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
```

Results: gateway approval queue tests `6 passed`; Stage Z primitive plus
approval/gateway regression tests `18 passed`; Stage Z tool-result storage plus
local spill compatibility tests `7 passed`; Stage Z backend storage handoff
tests `3 passed`; Stage Z backend/storage/executor compatibility tests `15
passed`; Stage Z tool-persistence/storage/executor/hardening tests `18 passed`;
Stage Z tool-persistence tests `3
passed`; focused MCP lifecycle tests `8 passed`; MCP reconnect parking/lifecycle
tests `17 passed`; real-stdio background notification/lifecycle tests `18
passed`; focused MCP OAuth login tests `3 passed`; focused MCP OAuth tests `6
passed`; focused MCP OAuth manager tests `2 passed`; focused MCP OAuth
metadata/bootstrap tests `3 passed`; focused MCP OAuth invalid-client tests `2
passed`; focused MCP elicitation tests `8 passed`; focused MCP
OAuth/manager/metadata/invalid-client/login plus CLI tests `19 passed`;
focused MCP OAuth/manager/metadata/invalid-client/login/elicitation/background/lifecycle/reconnect
tests `42 passed`; sandbox approval context slice `22 passed`; backend
interrupt/storage/thread slice `19 passed`; append-row/session/tool persistence
slice `35 passed`; integrated Stage Z patch-wave slice `50 passed`; nearby
MCP/U/Auth guardrail slice `67 passed`; nearby Stage Z/J/R/W guardrail slice
`67 passed`; Stage U/V guardrail slice `33 passed`; broad core Stage Z
regression slice `243 passed`; Python compilation is clean; ruff is clean; diff
whitespace check is clean.

Current Stage Z files put `aegis/tools/thread_context.py` at 465 LOC,
`aegis/tools/permissions.py` at 522 LOC, `aegis/tools/interrupt.py` at 133 LOC,
`tests/test_stage_z_thread_context.py` at 239 LOC,
`tests/test_stage_z_approval_wiring.py` at 209 LOC,
`tests/test_stage_z_gateway_approval_queue.py` at 328 LOC,
`tests/test_stage_z_tool_persistence.py` at 264 LOC,
`tests/test_stage_z_tool_result_storage.py` at 226 LOC,
`tests/test_stage_z_backend_storage_handoff.py` at 159 LOC,
`tests/test_stage_z_sandbox_approval_context.py` at 237 LOC,
`tests/test_stage_z_backend_interrupts.py` at 297 LOC,
`tests/test_stage_z_provider_pooling.py` at 152 LOC, and
`tests/test_stage_z_append_row_persistence.py` at 299 LOC,
`aegis/agent/agent.py` at 1,574 LOC, `aegis/agent/context.py` at 373 LOC,
`aegis/agent/compaction_runner.py` at 1,040 LOC,
`aegis/credentials.py` at 782 LOC, `aegis/providers/auth.py` at 1,873 LOC,
`aegis/agent/loop.py` at 2,402 LOC, and `aegis/session.py` at 2,594 LOC.
Current bounded MCP Stage Z files put `aegis/mcp/client.py` at 3,343 LOC,
`aegis/mcp/oauth_manager.py` at 801 LOC,
`aegis/tools/registry.py` at 523 LOC,
`tests/test_stage_z_mcp_lifecycle.py` at 432 LOC,
`tests/test_stage_z_mcp_sdk_callbacks.py` at 266 LOC,
`tests/test_stage_z_mcp_async_sampling.py` at 188 LOC,
`tests/test_stage_z_mcp_reconnect_parking.py` at 282 LOC,
`tests/test_stage_z_mcp_background_notifications.py` at 117 LOC,
`tests/test_stage_z_mcp_elicitation.py` at 381 LOC,
`tests/test_stage_z_mcp_oauth.py` at 414 LOC, and
`tests/test_stage_z_mcp_oauth_manager.py` at 271 LOC,
`tests/test_stage_z_mcp_oauth_metadata.py` at 368 LOC,
`tests/test_stage_z_mcp_oauth_invalid_client.py` at 413 LOC,
`tests/test_stage_z_mcp_oauth_login.py` at 381 LOC, and
`tests/test_stage_z_mcp_noninteractive_oauth.py` at 106 LOC. Gateway approval
wiring files now put `aegis/gateway/base.py` at 918 LOC and
`aegis/gateway/runner.py` at 2,123 LOC. Tool-result storage files put
`aegis/tools/tool_result_storage.py` at 301 LOC, `aegis/tools/backends.py` at
2,347 LOC, and `aegis/tools/base.py` at 321 LOC.

Previous broad slice: Stage W observability, traces, costs, and audit artifacts
closed the biggest
core harness observability gap in AEGIS: token/cost evidence is now explicit and
joinable, trace cost matches `aegis cost`, loop events carry stable ids, direct
agent callbacks receive the same stamped events fail-soft, activity diagnostics
can explain what the agent is doing, and traces can be exported as ordered audit
timelines instead of raw spans only.

Hermes reference read:

- `/home/alienai/.hermes/hermes-agent/agent/usage_pricing.py`, targeted full
  function ranges plus pricing-table inventory, 944 LOC
- `/home/alienai/.hermes/hermes-agent/agent/account_usage.py`, function map and
  account/credits fetch ranges, 638 LOC
- `/home/alienai/.hermes/hermes-agent/agent/credits_tracker.py`, function map and
  parser/notice/seed ranges, 794 LOC
- `/home/alienai/.hermes/hermes-agent/agent/billing_view.py`, function map and
  billing-state/amount ranges, 295 LOC
- `/home/alienai/.hermes/hermes-agent/agent/trajectory.py`, function map, 56 LOC
- `/home/alienai/.hermes/hermes-agent/run_agent.py`, targeted status, activity,
  usage-summary, token-counter, and trajectory ranges
- Stage W Hermes test map from usage-pricing, streaming activity,
  retry-status-buffer, account/credits, and billing-view tests

Files touched:

| File | Change |
| --- | --- |
| `aegis/usage_log.py` | Added usage summaries, cost evidence helpers, explicit unknown/estimated/actual labels, joinable context ids, and richer reports/series |
| `aegis/agent/agent.py` | Added activity heartbeat/summary, stamped event emitter, fail-soft `event_callback`, joinable usage-log calls, session last-turn cost metadata, and cache-write-aware session spend |
| `aegis/agent/loop.py` | Routed loop events through the stamped emitter, touched API activity boundaries, and switched provider-span costs to usage-log math |
| `aegis/tracing.py` | Added `TraceStore.timeline()` and normalized audit rows for prompt/provider/attempt/tool/compaction/final/error events |
| `tests/test_stage_w_usage_cost.py` | Focused usage/cost/joinability/cache-write trace-cost regressions |
| `tests/test_stage_w_activity.py` | Focused stamped-event, callback, fail-soft, and activity-summary regressions |
| `tests/test_stage_w_trace_timeline.py` | Focused trace timeline/audit export regressions |
| `BUILD_STATUS.md` | Updated Stage W Hermes reference coverage, subagents, LOC, percentage, and verification ledger |

Verification:

```bash
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_cost_pricing.py tests/test_tracing_evals.py tests/test_rpc_surface.py::test_rpc_server_initializes_runs_and_exposes_session_trace tests/test_observability.py::test_fallback_attempts_fire_provider_hooks
python -m py_compile aegis/usage_log.py aegis/agent/agent.py aegis/agent/loop.py aegis/tracing.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py
ruff check aegis/usage_log.py aegis/agent/agent.py aegis/agent/loop.py aegis/tracing.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_cost_pricing.py tests/test_tracing_evals.py tests/test_observability.py tests/test_rpc_surface.py tests/test_sdk.py tests/test_live_activity.py tests/test_provider_lifecycle_stage_g.py tests/test_response_normalization_loop_stage_h.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
```

Results: focused Stage W integration `35 passed`; post-joinability rerun
`36 passed`; broad Stage W/V/RPC/SDK/live-activity/provider guardrail slice
`105 passed`; ruff clean; Stage W Python compilation is clean; `git diff
--check` is clean.

Known Stage W parity gaps to carry forward:

- Hermes still has deeper live account/credits/billing API surfaces and
  structured notices. AEGIS now records cost/usage evidence and trace timelines,
  but the account/credits UI/API depth remains outside this core harness slice.
- Hermes can represent some fallback attempts as richer first-class lifecycle
  artifacts. AEGIS now exposes attempts in observer payloads and timeline rows,
  but child attempt spans can be deepened in a later provider observability pass.

## Stage Z Audit Checkpoint

Status: active, not complete. This checkpoint verifies the current harness
against Hermes enough to decide what remains; it does not certify full parity.

Subagents:

| Agent | Scope | Result |
| --- | --- | --- |
| Hubble `019f1984-190f-70c2-af02-5b883e9def67` | Hermes-to-AEGIS behavior gap audit | completed; identified five P0 full-parity blockers and two P1 debt areas |
| Godel `019f1984-3a21-7393-9672-0b489611b2c0` | AEGIS verification/proof-depth audit | completed; found green focused tests but insufficient Stage Z proof depth |
| Hume `019f19c6-e3ee-78d1-85da-4334fde8b73c` | Stage Z non-local backend result-storage handoff regression worker | completed; tests landed |
| Ptolemy `019f19d3-ceff-7b53-8589-10b01c892877` | Stage Z MCP reconnect parking/circuit-breaker regression worker | completed; tests landed and production patched by main agent |
| Faraday `019f19df-2a34-7871-a3be-4b5d2df6d488` | Stage Z MCP stdio background notification regression worker | completed; tests landed |
| Halley `019f19e6-eaa0-73a0-816e-e88c63a00ba9` | Stage Z MCP OAuth regression worker | completed; tests landed |
| Russell `019f19f3-5d63-7b33-9f3c-6564596e91f1` | Stage Z MCP OAuth manager regression worker | completed; tests landed |
| Nash `019f1a00-eb5b-76d0-b0c5-65bad3780c1d` | Stage Z MCP elicitation regression worker | completed; tests landed |
| Hegel `019f1a76-b96a-7ac3-b486-56efdf7d6043` | Stage Z borrowed external-token/provider auth resync worker | completed; production patch and tests landed |
| Herschel `019f1a76-91c3-7b82-a066-7c81f0b72c01` | Stage Z malformed SQLite/FTS/WAL session repair worker | completed; production patch and tests landed |
| Lorentz `019f1a7b-cfee-74d1-8955-4024a5c217fa` | Stage Z Daytona/Modal execution-environment depth worker | completed; production patch and tests landed |
| Huygens `019f1a83-9750-7ae2-8ad3-6723dea8679f` | Stage Z MCP sampling/server-request worker | completed; production patch and tests landed |
| Hilbert `019f1a83-b407-7ac2-9e23-022f240beca7` | Stage Z multi-pass compaction planning worker | completed; production patch and tests landed |
| Wegener `019f1a83-cec4-72c2-839c-c7ec72c98df0` | Stage Z remote sandbox sync worker | completed; production patch and tests landed |
| Heisenberg `019f1ac4-5028-7240-aaa9-93c9561e5f62` | Stage Z MCP lifecycle/stale-transport/idle-sampling worker | completed; production patch and tests landed |
| Carver `019f1ac4-7bc9-7982-a32e-afe167b4cc82` | Stage Z remote backend diagnostics/live-proof boundary worker | completed; production patch and tests landed |
| Planck `019f1ac4-afa2-73a0-b97b-a49c6664dafd` | Stage Z session malformed-repair/status-metadata worker | completed; production patch and tests landed |
| Avicenna `019f1ad6-2437-7f93-9feb-72c4cf69a4e9` | Stage Z MCP SDK same-task lifecycle/callback worker | completed; production patch and tests landed |
| Rawls `019f1ad6-247b-79f0-8ff0-8b8df8ece2bb` | Stage Z provider-specific singleton/source parity worker | completed; production patch and tests landed |

Mechanical inventory regenerated with:

```bash
python scripts/audit_reference_compare.py
```

Inventory results:

| Scope | AEGIS | Hermes reference | Ratio |
| --- | ---: | ---: | ---: |
| Source-like files | 1,026 files / 300,050 LOC | 5,459 files / 2,310,169 LOC | 18.8% files / 13.0% LOC |
| Representative core Python harness slice (`agent`, `providers`, `tools`, `mcp`, session/config/credentials/usage/tracing/background) | ~89 files / 38,581 LOC | ~258 files / 181,157 LOC | ~34.5% files / 21.3% LOC |
| Representative core plus gateway slice (core slice + `gateway`) | ~109 files / 48,759 LOC | ~326 files / 254,037 LOC | ~33.4% files / 19.2% LOC |

Important interpretation: these LOC ratios are audit pressure, not parity
percentages. The full source-like inventory includes dashboard, desktop,
website, plugins, optional skills, and other surfaces the user explicitly
deferred. The active Python harness slice is closer to the requested core, but
Hermes still carries much deeper behavior and test surface in agent/tools.

Local Stage Z checks:

```bash
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth_login.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_tool_executor_stage_j.py tests/test_file_state_stage_l.py tests/test_compaction_stage_n.py tests/test_overflow_stage_o.py tests/test_continuation_stage_p.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_hardening.py tests/test_thinking_recovery.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_gateway_commands.py::test_gateway_wires_exec_approval_to_platform_adapter tests/test_gateway_commands.py::test_gateway_redacts_exec_approval_prompt_before_adapter tests/test_gateway_inbound.py::test_shared_inbound_exec_approval_waiter_uses_exec_prompt tests/test_tools.py::test_bash_tool_background_uses_nonlocal_env_backend tests/test_tools.py::test_process_tool_start_uses_nonlocal_env_backend tests/test_tools.py::test_task_terminal_backend_override_changes_dispatch tests/test_tools.py::test_docker_backend_uses_task_image_override tests/test_tools.py::test_docker_backend_merges_config_and_env_extra_args tests/test_tools.py::test_docker_environment_adds_extra_run_args_before_image tests/test_providers.py::test_anthropic_coalesces_tool_results tests/test_smoke.py::test_oauth_configs_present tests/test_smoke.py::test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild
```

Result: `243 passed in 18.18s`.

Latest post-Lovelace/Plato focused verification:

```bash
python -m pytest -q tests/test_stage_z_provider_rate_controls.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py
python -m pytest -q tests/test_providers.py -k 'fallback_provider'
python -m pytest -q tests/test_stage_z_mcp_noninteractive_oauth.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_login.py
ruff check aegis/providers/fallback.py aegis/providers/base.py tests/test_stage_z_provider_rate_controls.py aegis/mcp/client.py tests/test_stage_z_mcp_noninteractive_oauth.py
python -m py_compile aegis/providers/fallback.py aegis/providers/base.py tests/test_stage_z_provider_rate_controls.py aegis/mcp/client.py tests/test_stage_z_mcp_noninteractive_oauth.py
git diff --check -- aegis/providers/fallback.py aegis/providers/base.py tests/test_stage_z_provider_rate_controls.py aegis/mcp/client.py tests/test_stage_z_mcp_noninteractive_oauth.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_stage_z_append_row_persistence.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_noninteractive_oauth.py tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_provider_rate_controls.py tests/test_mcp_cli.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_provider_lifecycle_stage_g.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_streaming_think_scrubber_stage_g.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_overflow_stage_o.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py
```

Results: provider Stage Z/V focused checks `21 passed`; legacy fallback-provider
subset `4 passed, 56 deselected`; MCP noninteractive OAuth/OAuth focused checks
`14 passed`; latest broad Stage Z/core sweep `194 passed in 16.57s`; ruff,
Python compilation, and diff whitespace checks are clean.

Mendel tool-result/file-safety verification:

```bash
python -m pytest -q tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_file_state_stage_l.py
ruff check aegis/tools/tool_result_storage.py tests/test_stage_z_tool_result_storage.py
python -m py_compile aegis/tools/tool_result_storage.py tests/test_stage_z_tool_result_storage.py
git diff --check -- aegis/tools/tool_result_storage.py tests/test_stage_z_tool_result_storage.py
```

Result: `13 passed`; ruff, Python compilation, and diff whitespace checks are
clean.

Turing session/compaction durability verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_session_stage_s.py tests/test_compaction_stage_n.py tests/test_stage_z_append_row_persistence.py
ruff check aegis/session.py aegis/agent/compaction_runner.py tests/test_compaction_stage_n.py tests/test_session_stage_s.py tests/test_stage_z_append_row_persistence.py
python -m py_compile aegis/session.py aegis/agent/compaction_runner.py tests/test_compaction_stage_n.py tests/test_session_stage_s.py tests/test_stage_z_append_row_persistence.py
git diff --check -- aegis/session.py aegis/agent/compaction_runner.py tests/test_compaction_stage_n.py tests/test_session_stage_s.py tests/test_stage_z_append_row_persistence.py
```

Result: `12 passed`; ruff, Python compilation, and diff whitespace checks are
clean.

Socrates MCP lifecycle/reconnect verification:

```bash
python -m pytest -q tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_noninteractive_oauth.py
ruff check aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
python -m py_compile aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
git diff --check -- aegis/mcp/client.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py
```

Result: `23 passed`; ruff, Python compilation, and diff whitespace checks are
clean.

Latest integrated Stage Z sweep after Mendel/Turing/Socrates:

```bash
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_stage_z_append_row_persistence.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_thread_context.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_noninteractive_oauth.py tests/test_stage_z_mcp_oauth_login.py tests/test_stage_z_mcp_oauth_metadata.py tests/test_stage_z_mcp_oauth_invalid_client.py tests/test_stage_z_mcp_oauth.py tests/test_stage_z_mcp_oauth_manager.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_provider_rate_controls.py tests/test_mcp_cli.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_provider_lifecycle_stage_g.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_streaming_think_scrubber_stage_g.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_overflow_stage_o.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_file_state_stage_l.py
```

Result: `209 passed in 16.01s`.

Godel verification sidecar results:

- Core Stage A-W proof slice: `311 passed in 20.67s`.
- Claude/Anthropic thinking/cache/OAuth preservation probes: `12 passed in
  0.53s`.
- Stage U/MCP deferred/schema focused proof: `36 passed in 0.55s`.

Marker scan:

```bash
rg -n "TODO|FIXME|XXX|NotImplemented|not implemented|placeholder|stub" aegis/agent aegis/tools aegis/providers aegis/mcp aegis/session.py aegis/usage_log.py aegis/tracing.py -g '!**/node_modules/**' -g '!aegis/static/**' -g '!aegis/tui_ink/**' -g '!aegis/builtin_skills/**'
```

Result: no accidental core TODO/FIXME implementation holes were found. The
remaining markers are abstract base `NotImplementedError` contracts,
instructional prompt text, and deliberate user-facing "not implemented"
phrasing outside the patched core runtime paths.

Concrete Stage Z gaps still blocking a full parity claim:

- P0 MCP live lifecycle/OAuth: Hermes has long-lived MCP tasks, dynamic
  `tools/list_changed`, keepalive, reconnect/parking, deregistration, OAuth
  2.1 PKCE/token refresh, session-expiry recovery, and SDK-level elicitation
  routing.
  AEGIS now has cached tool lists, `tools/list_changed` invalidation, manager
  repave refresh, same-name schema replacement, Agent/provider-boundary schema
  refresh hooks, auth-refresh-needed state, one-shot session-expired tool retry,
  stale-transport marker reconnect retry, keepalive-driven parked tool calls,
  ping/list keepalive reporting, a cooldown/half-open circuit breaker for
  repeated model-facing MCP failures, stdio background notification handling
  for idle `tools/list_changed` events, HTTP MCP disk-backed OAuth token
  loading/expired-token refresh/401 retry/cache invalidation/same-token
  recovery dedupe through the AEGIS `AuthStore` and `MCPOAuthManager`,
  protected-resource/auth-server metadata discovery, dynamic client
  registration bootstrap, metadata/client cache reuse under
  `$AEGIS_HOME/mcp-oauth/`, expired-token cold-start refresh through the
  discovered token endpoint, and invalid-client dynamic-cache healing that
  backs up/removes stale dynamic client registrations before rediscovery and
  re-registration. AEGIS also has `aegis mcp login <name>` for uncached HTTP
  MCP OAuth servers, including stale login-state purge, dynamic/static client
  handling, redirect-stable registration, PKCE authorization-code exchange,
  token persistence under `mcp:<server>`, and Hermes-style manual callback
  paste parsing. AEGIS also has stdio `elicitation/create` routing
  through captured context/direct approver
  paths and the shared pending approval queue with URL-mode decline, no-approval
  fail-closed behavior, timeout cancel, and request-id collision safety.
  Noninteractive startup now marks OAuth-required HTTP servers as auth-needed
  before touching the endpoint when no cached token exists, while allowing
  metadata/bootstrap flows that need discovery. AEGIS also has a per-client
  lifecycle worker that consumes reconnect signals, uses bounded backoff, parks
  after retry exhaustion, can revive from a later signal, records owner/generation
  metadata for the active transport, suppresses stale stdio/SSE readers from old
  generations, and keeps model-facing calls out of known-dead transports during
  reconnect/park states. Streamable HTTP `text/event-stream` responses now read
  until the matching JSON-RPC id, legacy `transport="sse"` endpoints are
  discovered/read in the background, resource/prompt utility calls share the
  reconnect retry path, and `sampling/createMessage` server requests are routed
  from active MCP tool calls through the current provider context with provider
  tool-call mapping back into MCP content. AEGIS also advertises file roots,
  answers `roots/list`, retains redacted progress/logging notifications, exposes
  `completion/complete`, and keeps bounded idle-sampling snapshots that fail
  closed when expired. AEGIS also has an opt-in SDK transport that keeps
  `ClientSession.__aenter__`, `initialize`, RPCs, and `__aexit__` in one async
  owner task, with SDK-shaped sampling, elicitation, roots, progress/logging,
  tool/resource/prompt/completion result adapters covered by fake-SDK tests.
  Remaining depth: real installed `mcp` package end-to-end proof against an SDK
  server and any future SDK-specific edge cases exposed by that live dependency.
- P0 approval/sandbox/thread context: Hermes has context-local per-session
  approvals, pending approval queues, interrupt-aware waits, thread-context
  propagation, and per-thread interrupts. AEGIS now has Stage Z primitives and
  production wiring for context-local approval metadata, pending approval
  queues, direct/context/queued approval fallback in `PermissionEngine`,
  run-thread interrupt marking on cancel, per-tool approval correlation ids,
  context propagation into parallel tool workers, and gateway replies that
  resolve the shared pending approval queue with prompt/user/session validation
  plus prompt-id matching for multiple pending approvals. AEGIS now also
  propagates backend/task/cwd/surface runtime context into queued approval
  payloads, resolves effective backend/cwd overrides for sandboxed shell/process
  approvals, and includes execution/sandbox metadata for local, container, and
  remote backend decisions. Remaining depth: deeper backend-specific policy
  profiles and persistent audit export for sandbox approvals.
- P0 tool execution persistence/interruption: Hermes flushes session DB after
  each tool result, records cancelled/skipped tool results, propagates context
  into concurrent workers, and persists large outputs through environment-backed
  storage. AEGIS now saves transcript-safe snapshots as each tool result becomes
  durable, records skipped sequential tool calls after cancellation, propagates
  contextvars into parallel workers, and persists oversized outputs through an
  explicit execution environment via `stdin_data` with local fallback and
  largest-first aggregate budget enforcement. Active non-local backend
  environments are now cached/reused by `(backend, task_id)`, so foreground
  `bash`/`run_command`, background `bash`, and `process start` paths can hand
  the later large-result storage layer the same execution environment. AEGIS
  now appends durable tool-result message rows before whole-session snapshots,
  hydrates appended tail rows on load, actively propagates live interrupts
  through local subprocesses, non-local backend cancel hooks, and tracked
  SSH/Singularity-style backend client processes/process groups, and uses
  atomic local fallback writes for persisted outputs. Oversized-output storage
  now writes `.metadata.json` sidecars with SHA-256/byte/char accounting and
  exposes parse/load/rehydrate helpers. `read_file` can page persisted outputs
  from the active non-local backend, and compaction summary input rehydrates
  persisted-output references before pruning. Remaining depth: richer
  user-facing transcript UX for persisted outputs and more provider-specific
  termination/file-sync semantics for live remote sandboxes.
- P0 provider/auth pooling and cross-session rate controls: Hermes tracks
  credential sources, sticky removal/suppression, OAuth refresh races,
  credential leases, source re-seeding, cross-session account breakers, and
  rate-limit headers. AEGIS now preserves Retry-After/reset hints into auth
  reports, records provider cooldown rows, keeps follow-up calls on an active
  fallback while the primary is cooling down, expires cooldowns before
  restoring the primary, tags config/env credential sources, keeps sticky
  source suppression across pool reloads, blocks all-suppressed env sources
  from falling back to raw environment keys, exposes soft nonblocking leases,
  and records provider-wide account breaker state shared through the credential
  state file. Borrowed/reference-only OAuth credentials now resync from
  external token files, Claude Code credentials, and Codex CLI auth without
  persisting raw borrowed secrets, and rotated external fingerprints clear stale
  exhausted/dead state. Claude Code import now stores a reference rather than
  copying Claude access/refresh tokens into AEGIS auth storage. Removal now
  routes through a centralized source-removal registry for borrowed references,
  environment, config/model-config, Codex CLI, Claude Code, manual sources, and
  provider-specific Nous/Codex/xAI/Qwen/MiniMax singleton sources; returns
  structured provider-specific hints; suppresses borrowed/env/config/singleton
  sources where appropriate; filters suppressed pooled OAuth entries; clears
  Hermes-shaped nested `providers.<provider>` singletons; reads Qwen CLI as a
  reference-only borrowed source; and surfaces those hints from the CLI without
  leaking secrets. Remaining depth: richer source pruning/metadata and live
  provider singleton smoke if later Hermes re-audits expose a provider-specific
  edge case beyond the current blocker.
- P0 session/compaction persistence: Hermes has append-message persistence, WAL
  fallback, DB repair, token/cost accumulation, compression locks, robust
  compression-tip resolution, and orphan cleanup. AEGIS has SQLite sessions,
  resume flags, lineage, compression locks, appended message-row hydration,
  tool-result append/update persistence ahead of whole-session snapshots,
  durable active/compacted message-row metadata, and archival in-place
  compaction that keeps old active rows inactive while normal loads hydrate only
  the compacted live transcript. Automatic compaction also records durable
  low-savings/no-progress metadata, skips repeated ineffective automatic passes
  after two persisted low-savings compactions, and keeps manual/overflow
  compaction available to clear the guard when it makes real progress.
  Corrupt JSON snapshots now recover from active message rows and rewrite the
  repaired snapshot when writable. AEGIS now also backs up raw DB/WAL/SHM files
  before repair, falls back from unsupported WAL to DELETE journaling, repairs
  legacy `messages` tables, rebuilds/repairs FTS indexes from canonical message
  rows, records canonical row-count evidence, recovers corrupt WAL/SHM sidecars
  only when the checkpointed main DB is independently healthy, writes
  inspectable `*.repair.json`/status metadata for repair attempts, keeps a
  one-shot guard around unsafe malformed DB repair, serializes sidecar/schema
  repair surgery with a cross-process `state.db.repair.lock`, exposes locked
  manual restore from preserved repair backups while first preserving the
  current DB, and runs bounded multi-pass compaction/recovery with durable pass
  metadata. Remaining depth: true WAL frame replay for damaged WAL-only
  transactions beyond AEGIS' explicit no-replay/manual-restore policy, and
  richer archived transcript surfacing/search recovery.
- P1 prompt/cache and skills catalog: Hermes has fuller system-prompt cache and
  disk-backed skill prompt snapshot behavior with category demotion, external
  dirs, and names-never-hidden guarantees. AEGIS has structured prompt parts,
  skill activation/autoload, and a stable non-volatile prompt snapshot with
  overall/stable/context/skills fingerprints persisted in session metadata and
  prompt-audit cache. Provider-wire volatile context does not churn that stored
  snapshot. AEGIS now also persists a disk-backed skills prompt snapshot,
  invalidates it by discovery signature, records skill category metadata, and
  supports focus-mode category demotion to names-only while keeping every skill
  name visible. Latest pass adds Hermes-style category `DESCRIPTION.md`
  summaries, includes those files in snapshot/signature invalidation, preserves
  local-over-external category-summary precedence, and adds a process-wide
  8-entry rendered skills prompt LRU. Remaining depth: fuller Hermes skills
  corpus breadth, explicit `skills.external_dirs` config-key aliasing if needed,
  and broader prompt-cache live smoke across CLI/TUI/gateway surfaces.
- P1 observability/cost residuals: AEGIS has Stage W usage/cost evidence and
  trace timelines. Hermes still has richer session DB token/cost columns,
  account/credits parsing, and child-attempt/account depth. Account/credits and
  billing product surfaces remain acceptable deferred X/Y/product work.
- Execution environments: Hermes has live Daytona/Modal SDK sandbox support
  with deeper snapshot/file-sync integrations. AEGIS now has fake-SDK-covered
  Daytona creation/resume/list fallback, stopped-sandbox restart, stdin
  wrapping, stop/delete cleanup, Modal persistent sandbox execution, filtered
  workspace upload, remote delete propagation, tar sync-back, safe extraction,
  cleanup-time sync-back, Modal snapshot restore, stale snapshot fallback,
  cleanup-time snapshot save, sync-back warning diagnostics, Daytona tar-upload
  failure handling, Modal bulk upload stdin chunking proof, passive
  `remote_backend_diagnostics()` SDK/credential/config checks, and fail-closed
  Modal creation before live lookup when credentials/config are absent.
  Remaining depth: live-credential integration proof, real-SDK bulk
  upload/download edge cases, and provider-specific interruption semantics under
  real remote runtimes.
- Full Hermes reading coverage remains incomplete. Stage Z must continue from
  Hermes files and tests first, then AEGIS implementation/tests, before any
  "full clone" or parity language is safe.

Preservation note from Stage Z: Claude/Anthropic signed thinking blocks,
provider-wire-only reasoning stripping, prompt-cache reuse, and OAuth protection
look intentionally guarded by current AEGIS tests. Do not regress those while
patching prompt/cache or provider/auth gaps.

## Latest Verified Harness Push

Scope for that verified wave: core harness only. Synth and proprietary Nous
portal/product surfaces remain out of scope. Dashboard, desktop, web/static,
TUI, and packaging are part of the full-harness target and should be handled in
dedicated later lanes.

| File | LOC at last count | Change note |
| --- | ---: | --- |
| `aegis/agent/agent.py` | 1,574 | Stage B stable prompt-cache fix, Stage C `_begin_turn_prologue()` reset state, Stage D provider-wire user-content override, volatile system-context builder, Stage E route/downshift/runtime-selection metadata, Stage J resume one-turn route restore, Stage O output-cap retry reset, Stage R final save/memory/review/trajectory finalization gates, Stage U scoped deferred-candidate helpers, Stage V fallback primary restore, Stage W activity/event/cost metadata, and Stage Z prompt snapshot metadata |
| `aegis/agent/context.py` | 373 | Stage D split between stored prompt parts and provider-wire volatile prompt parts plus Stage Z stable non-volatile prompt/context/skills fingerprints |
| `aegis/agent/events.py` | 61 | Shared event contract now includes Stage E readiness/routing/budget/block events |
| `aegis/agent/loop.py` | 2,402 | Integrated hard-stop finalization, invalid tool-call recovery, verify-after-edit, Stage A early persistence, Stage C turn/API id handling, Stage D request-copy context isolation, Stage E budget/readiness preflight, Stage F wire governance placement, Stage G streamed thinking scrubber placement, Stage J checkpoint gating/destructive shell checkpoint targets/schema argument coercion/sanitized tool exceptions, Stage O output-cap overflow retry, Stage P continuation/empty-response recovery, Stage R canonical turn-result metadata, Stage U read-only `tool_describe` handling, Stage W stamped events/API activity/cost parity, and Stage Z append-row tool-result persistence |
| `aegis/agent/guardrails.py` | 696 | Hermes structured guardrail config/signature/decision/controller metadata, exact Hermes tool-name sets, display-style terminal/memory/error classification, AEGIS executor adapter hard-stop state for no-progress reads/exact repeated failures/varied same-tool failures, canonical JSON result hashing, landed file-mutation success classification, and Stage U read-only `tool_describe` classification |
| `aegis/agent/governance.py` | 263 | Stage F canonical role/tool-call/result hygiene |
| `aegis/agent/invalid_tool_calls.py` | 100 | Bounded invalid/empty tool-call recovery helper |
| `aegis/agent/request_wire.py` | 109 | Stage F provider-copy thinking-only drop and adjacent-user merge |
| `aegis/agent/runtime_readiness.py` | 224 | Stage E provider/model/auth readiness helper |
| `aegis/agent/streaming_think_scrubber.py` | 222 | Stage G stateful streamed reasoning/thinking suppression helper |
| `aegis/agent/compaction_runner.py` | 1,040 | Stage N automatic/overflow compaction metadata persistence, Stage Z archival same-session compaction save path, durable no-progress compaction guard, and multi-pass recovery planning |
| `aegis/agent/compaction.py` | 389 | Stage N reference comparison target for summary/tail behavior and persisted-output summary rehydration |
| `aegis/agent/verification.py` | 259 | Successful code-edit tracking and verification nudge helper |
| `aegis/usage_log.py` | 547 | Stage W canonical-ish usage summaries, cost evidence/status/source/labels, joinable usage ids, report/series rollups |
| `aegis/tracing.py` | 767 | Stage W `TraceStore.timeline()` ordered prompt/provider/attempt/tool/compaction/final audit export |
| `aegis/providers/fallback.py` | 954 | Stage V structured provider taxonomy, fallback eligibility, same-route dedupe, auth error-context reporting, fallback endpoint/key-env overlays, primary-restore hook, and Stage Z Retry-After/reset cooldown routing |
| `aegis/providers/auth.py` | 1,873 | Stage V API-key pool reporting/usage, OAuth refresh/exhaust/quarantine reporting, Stage Z Hermes-style OAuth manual callback parsing/errors/skip tokens, API-key lease acquire/release surface, borrowed OAuth reference resync/removal suppression, centralized source-removal registry, provider-specific singleton removal hints for Nous/Codex/xAI/Qwen/MiniMax, Qwen CLI reference-only import, and Claude Code reference-only import |
| `aegis/providers/base.py` | 213 | Stage V fallback-orchestrated provider-call retry handoff and Stage Z structured error-context handoff |
| `aegis/credentials.py` | 782 | Stage V masked-key `ok`/`exhausted`/`dead` credential-pool state/reset parsing plus Stage Z source-aware seeding/suppression, reset-preserved suppressed sources, soft leases, and provider-wide account breaker state |
| `aegis/providers/chat_completions.py` | 256 | Stage O max-token request payload wiring |
| `aegis/providers/schema.py` | 366 | Stage U provider-safe schema sanitizer for MCP/plugin/deferred tool schemas |
| `aegis/mcp/client.py` | 3,343 | Stage U MCP input schema normalization through the central sanitizer plus Stage Z live refresh/reconnect lifecycle worker/background-notification/OAuth refresh/noninteractive OAuth startup gating/stdio elicitation/sampling/post-tool idle sampling server-request behavior, lifecycle generation ownership, stale-reader suppression, optional SDK same-task `ClientSession` transport/callback adapters, roots/list handling, redacted progress/logging notification retention, and completion helper |
| `aegis/mcp/oauth_manager.py` | 801 | Stage Z MCP OAuth auth-handle cache, auth-store mtime invalidation, same-token 401 recovery dedupe, metadata discovery, dynamic client bootstrap, cache persistence/reuse, discovered-endpoint cold-start refresh, invalid-client dynamic-cache healing, forced-login purge, redirect-stable dynamic registration, and CLI login entrypoint |
| `aegis/mcp/server.py` | 164 | Stage U permission-engine exposure for bridge dispatch in AEGIS-as-MCP mode |
| `aegis/session.py` | 2,594 | Stage S marker-aware branch helper, compression-tip resolver, resume-to-tip API, Stage Z appended tool-row hydration/persistence, active/compacted archival compaction rows, SQLite/FTS/WAL repair backups, corrupt WAL/SHM sidecar recovery, malformed-repair one-shot/status metadata, cross-process repair lock, manual repair-backup restore helper, and canonical repair evidence |
| `aegis/sdk.py` | 604 | SDK resume and branch now use Stage S session resolver/helper |
| `aegis/cli/repl.py` | 3,040 | Terminal `/resume` and `/branch` wired to Stage S resolver/helper |
| `aegis/cli/tui.py` | 148 | TUI `--resume` wired to Stage S resolver |
| `aegis/cli/main.py` | 5,429 | Stage U cost-surface deferred count uses runtime protected bridge/direct-tool rules; Stage Z MCP OAuth login now runs the manager-backed PKCE flow, auth removal/logout surfaces structured borrowed-source hints, and Stage L checkpoint status/list/prune/clear/clear-legacy/rollback CLI output now exposes store/workdir/git/legacy metadata plus `--max-size-mb` pruning |
| `aegis/surface.py` | 1,269 | Surface session loading wired to Stage S resolver |
| `aegis/background.py` | 641 | Stage T background delegation completion/list metadata with duration, session/delegation ids, role, model, interruptible state, and persisted child usage/cost observability |
| `aegis/tools/agentic.py` | 1,101 | Stage T strict child tool scoping, toolset alias/intersection rules, child memory skip, role/depth identity, active subagent list/cancel, Hermes-style top-level default background dispatch, richer background spawn metadata, synchronous child usage/cost completion payloads, and Stage U read-only `tool_describe` child allowance |
| `aegis/tools/devtools.py` | 269 | Stage U scoped deferred bridge: `tool_search`, `tool_describe`, `tool_call`, parameter-aware search, bounded search limits, and protected direct/core tools |
| `tests/test_smoke.py` | 600 | Stage B prompt-cache regression coverage, Stage D volatile system-context wire-copy coverage, and Stage Z prompt snapshot/cache/skills fingerprint coverage |
| `tests/test_resilience.py` | 834 | Invalid tool-call, hard-stop loop, completed Stage A persistence, Stage C prologue coverage, Stage D plugin/middleware wire-copy coverage, and Stage P prior-turn empty-retry guard |
| `tests/test_memory_wiring.py` | 314 | Stage D memory refresh provider-wire coverage |
| `tests/test_governance_stage_f.py` | 88 | Stage F canonical governance coverage |
| `tests/test_request_wire_stage_f.py` | 118 | Stage F wire-copy governance coverage |
| `tests/test_provider_lifecycle_stage_g.py` | 122 | Stage G provider lifecycle and split-stream leak coverage |
| `tests/test_routing_stage_e.py` | 196 | Stage E route/downshift/budget-block regression coverage |
| `tests/test_runtime_readiness.py` | 105 | Stage E provider readiness regression coverage |
| `tests/test_tool_executor_stage_j.py` | 120 | Stage J checkpoint ordering coverage |
| `tests/test_compaction_stage_n.py` | 421 | Stage N compaction durability, Stage Z archival compaction-row coverage, no-progress guard reload coverage, and multi-pass recovery coverage |
| `tests/test_overflow_stage_o.py` | 175 | Stage O overflow recovery coverage |
| `tests/test_continuation_stage_p.py` | 181 | Stage P continuation, empty retry, thinking-only recovery, and max-token boost coverage |
| `tests/test_finalization_stage_r.py` | 255 | Stage R canonical final text, memory sync, empty-final skip, review/trajectory cleanup coverage |
| `tests/test_turn_finalization_stage_r.py` | 251 | Stage R cancelled tool-tail alternation and budget-grace visible-text persistence coverage |
| `tests/test_session_stage_s.py` | 183 | Stage S resume-to-tip and branch/delegate exclusion coverage |
| `tests/test_session_switch_stage_s.py` | 207 | Stage S memory switch, runtime metadata, SDK resume, and terminal picker coverage |
| `tests/test_subagent_stage_t.py` | 227 | Stage T synchronous subagent toolset, blocked-tool, role/depth, event metadata, and child usage/cost completion coverage |
| `tests/test_subagent_background_stage_t.py` | 349 | Stage T background subagent spawn/default-dispatch/completion/list/cancel metadata plus child usage/cost observability coverage |
| `tests/test_stage_u_deferred_bridge.py` | 161 | Stage U scoped deferred bridge coverage |
| `tests/test_stage_u_schema_sanitizer.py` | 175 | Stage U provider schema sanitizer and MCP normalization coverage |
| `tests/test_stage_v_provider_fallback.py` | 351 | Stage V fallback taxonomy, chain, real-provider handoff, and turn-restore coverage |
| `tests/test_stage_v_credentials.py` | 143 | Stage V credential-pool health-state coverage |
| `tests/test_stage_v_auth_integration.py` | 611 | Stage V API-key/OAuth auth-report integration, borrowed OAuth reference resync/removal suppression, source-removal registry coverage, provider-specific singleton/source removal coverage, Qwen CLI reference preservation, suppressed pool filtering, save-clears-suppression behavior, Claude Code reference preservation, and Codex CLI auth reference coverage |
| `tests/test_auth_cli.py` | 126 | CLI auth add/list/remove/reset coverage, including borrowed-source removal hints without secret leakage |
| `tests/test_stage_z_provider_rate_controls.py` | 157 | Stage Z Retry-After handoff, provider cooldown routing, cooldown expiry, and bound-provider structured error-context coverage |
| `tests/test_stage_z_provider_pooling.py` | 152 | Stage Z source suppression/reseed, reset-preserved suppression, all-suppressed env fallback block, soft leases, and provider-wide account breaker coverage |
| `tests/test_stage_z_mcp_noninteractive_oauth.py` | 106 | Stage Z OAuth-required automatic MCP startup gating and cached-token startup coverage |
| `tests/test_stage_z_mcp_sampling.py` | 232 | Stage Z MCP `sampling/createMessage` server-request and streamable HTTP response routing coverage |
| `tests/test_stage_z_mcp_client_capabilities.py` | 106 | Stage Z MCP roots/list, cwd default root, redacted progress/logging notification retention, and completion helper coverage |
| `tests/test_stage_z_mcp_async_sampling.py` | 188 | Stage Z MCP post-tool idle `sampling/createMessage` and expired idle-snapshot fail-closed regression coverage |
| `tests/test_stage_z_mcp_sdk_callbacks.py` | 266 | Stage Z fake-SDK coverage for same-task ClientSession lifecycle/RPC ownership and SDK-native callback/result compatibility |
| `tests/test_stage_z_session_repair.py` | 724 | Stage Z session DB repair coverage for legacy message rows, FTS repair, WAL fallback, corrupt snapshots, raw DB/WAL/SHM backups, sidecar recovery, SQLite-only WAL repair policy, cross-process repair lock refusal, manual repair-backup restore, post-repair health probes, repair metadata/status evidence, one-shot malformed repair refusal, and unsafe replay refusal |
| `tests/test_stage_z_remote_environments.py` | 1,646 | Stage Z Daytona/Modal fake-SDK lifecycle, real installed SDK import-gated readiness proof, SDK API-shape diagnostics, redacted fail-closed creation errors, failed-initial-sync sandbox cleanup, Daytona keyword/query-list resume compatibility, workspace sync, delete propagation, sync-back, persisted output handoff, Modal snapshot restore/migration/save, stale snapshot fallback, sync-back diagnostics, passive remote backend diagnostics, tar upload failure, and bulk upload stdin chunking coverage |
| `tests/test_stage_w_usage_cost.py` | 221 | Stage W usage summary, cost evidence, joinability, and cache-write trace-cost coverage |
| `tests/test_stage_w_activity.py` | 159 | Stage W stamped event, callback, fail-soft, and activity-summary coverage |
| `tests/test_stage_w_trace_timeline.py` | 186 | Stage W trace timeline/audit export coverage |
| `tests/test_memory_lifecycle.py` | 917 | External memory lifecycle coverage, including Stage R empty-final sync skip |
| `tests/test_agent_perms.py` | 425 | Broad agent permission/governance/budget/resume routing coverage |
| `aegis/checkpoints.py` | 931 | Stage L per-turn checkpoint snapshots, project-root detection with broad temp/home parent guard, shared git blob store dedupe with manifest-backed rollback/diff, single-file restore with path validation, workdir manifest metadata, Hermes-style checkpoint status/git-store/legacy-archive sizing, checkpoint history rows, stale/orphan/over-limit prune with git ref cleanup, file-count/per-file/total-size checkpoint caps, auto-prune marker, clear-all, and clear-legacy helpers |
| `aegis/tools/file_state.py` | 268 | Stage L task-aware file freshness registry |
| `aegis/tools/builtin.py` | 1,408 | Stage L file-tool task-id/partial-read file-state integration, active-backend persisted-output read paging, compact read gutters, binary-extension read blocking, internal display-text write rejection, post-write persistence verification, and Hermes-style search pagination/output modes |
| `aegis/tools/backends.py` | 2,931 | Stage Z non-local backend caching, interrupts, Daytona/Modal lifecycle adapters, workspace sync, cleanup-time sync-back, failed-initial-sync sandbox cleanup, Modal snapshot restore/save, sync diagnostics, passive remote backend diagnostics with callable SDK API-shape checks, fail-closed Modal/Daytona credential/config/API-shape/creation gating with redacted errors, Daytona query-object list compatibility, and Daytona tar upload failure handling |
| `aegis/tools/extra_builtin.py` | 492 | Stage L apply-patch task-id file-state integration |
| `tests/test_checkpoint_depth.py` | 335 | Stage L checkpoint batch depth, shared git object-store dedupe/ref cleanup, new-file rollback, single-file restore/removal/path validation, apply-patch path extraction, checkpoint status/project metadata, prune, auto-prune marker, and CLI status/prune/file-rollback coverage |
| `tests/test_checkpoint_policy_stage_l.py` | 131 | Stage L checkpoint policy coverage for nested project-root manifests, max snapshot file-count guard, per-file size-cap skip, total store-size pruning while retaining newest project checkpoint, and recent-first history metadata |
| `tests/test_checkpoint_cli_stage_l.py` | 302 | Stage L checkpoint CLI coverage for status/list store metadata, project workdir rows, prune settings/counts including `--max-size-mb`, legacy archive status/clear-legacy preservation, clear reclaimed-size output, commit/history metadata, and rollback-file evidence |
| `tests/test_file_state_stage_l.py` | 152 | Stage L task-aware stale-write regression coverage |
| `tests/test_file_operations_stage_l.py` | 133 | Stage L file-operation parity coverage for compact read gutters, search pagination metadata, device/binary read blocking, post-write verification, and partial-read stale warnings |
| `tests/test_tools.py` | 1,488 | File-tool safety, search pagination, read-display write rejection, patch, LSP, and freshness regressions |
| `tests/test_hardening.py` | 187 | Stage P length-continuation partial preservation plus hardening checks |
| `tests/test_self_verify.py` | 187 | Verify-after-edit and current-turn self-verify coverage |
| `tests/test_streaming_think_scrubber_stage_g.py` | 72 | Stage G stateful stream-scrubber unit coverage |
| `tests/test_session_config.py` | 313 | Session metadata read coverage |
| `tests/test_upgrades_batch.py` | 290 | Direct guard hard-stop coverage |
| `tests/test_wakeups.py` | 151 | Stage D wakeup wire-only coverage |
| `tests/test_skills_memory.py` | 794 | Stage D skill wire-only coverage |

Current Stage W files put `aegis/usage_log.py` at 547 LOC,
`aegis/tracing.py` at 767 LOC, `aegis/agent/agent.py` at 1,467 LOC,
`aegis/agent/loop.py` at 2,201 LOC, `tests/test_stage_w_usage_cost.py` at
221 LOC, `tests/test_stage_w_activity.py` at 159 LOC, and
`tests/test_stage_w_trace_timeline.py` at 186 LOC. Current Stage W focused
AEGIS files/tests total 5,548 LOC against 8,406 LOC in the direct Hermes Stage
W reference slice read this pass (`usage_pricing.py`, `account_usage.py`,
`credits_tracker.py`, `billing_view.py`, `trajectory.py`, and `run_agent.py`),
about 66.0% by LOC for this slice only. This LOC ratio is not a parity claim;
Hermes' Stage W slice includes product/account/billing surfaces intentionally
kept out of this core-harness pass.

Current Stage V/Z provider files put `aegis/providers/fallback.py` at 954 LOC,
`aegis/credentials.py` at 782 LOC, `aegis/providers/auth.py` at 1,873 LOC,
`aegis/providers/base.py` at 213 LOC, `aegis/agent/agent.py` at 1,551 LOC,
`tests/test_stage_v_provider_fallback.py` at 351 LOC,
`tests/test_stage_v_credentials.py` at 143 LOC,
`tests/test_stage_v_auth_integration.py` at 611 LOC, and
`tests/test_stage_z_provider_rate_controls.py` at 157 LOC. Current
`aegis/providers/*.py` total is 6,556 LOC. Current Stage U/Z MCP files put
`aegis/tools/devtools.py` at 269 LOC,
`aegis/providers/schema.py` at 366 LOC, `aegis/mcp/client.py` at 3,343 LOC,
`aegis/agent/agent.py` at 1,277 LOC, `aegis/agent/loop.py` at 2,184 LOC,
`aegis/agent/guardrails.py` at 696 LOC,
`tests/test_stage_u_deferred_bridge.py` at 219 LOC, and
`tests/test_stage_u_schema_sanitizer.py` at 175 LOC. Current Stage T files put
`aegis/tools/agentic.py` at 1,101 LOC,
`aegis/background.py` at 641 LOC, `tests/test_subagent_stage_t.py` at 227
LOC, and `tests/test_subagent_background_stage_t.py` at 349 LOC. Current
Stage P files put `aegis/agent/loop.py` at 2,133 LOC,
`tests/test_continuation_stage_p.py` at 181 LOC, `tests/test_resilience.py` at
834 LOC, and `tests/test_hardening.py` at 187 LOC. Stage O snapshot put
`aegis/providers/fallback.py` at 353 LOC,
`aegis/agent/loop.py` at 2,028 LOC, `aegis/agent/agent.py` at 1,234 LOC,
`aegis/providers/chat_completions.py` at 256 LOC,
`tests/test_overflow_stage_o.py` at 175 LOC, `tests/test_reliability.py` at
189 LOC, and `tests/test_thinking_recovery.py` at 90 LOC. Current Stage N files put
`aegis/agent/compaction_runner.py` at 568 LOC,
`aegis/agent/compaction.py` at 372 LOC, and
`tests/test_compaction_stage_n.py` at 194 LOC. Current Stage L files put
`aegis/checkpoints.py` at 931 LOC, `aegis/tools/file_state.py` at 268 LOC,
`aegis/tools/builtin.py` at 1,094 LOC, `aegis/tools/extra_builtin.py` at
492 LOC, `tests/test_checkpoint_depth.py` at 335 LOC,
`tests/test_checkpoint_policy_stage_l.py` at 131 LOC,
`tests/test_checkpoint_cli_stage_l.py` at 302 LOC, and
`tests/test_file_state_stage_l.py` at 152 LOC. Current Stage J
files were `aegis/agent/loop.py` at 1,984 LOC, `aegis/agent/agent.py` at
1,232 LOC, and `tests/test_tool_executor_stage_j.py` at 120 LOC before later
Stage N/O growth.

## Verification Log

Latest verified checks:

```bash
python -m pytest -q tests/test_continuation_stage_p.py
python -m pytest -q tests/test_continuation_stage_p.py tests/test_hardening.py::test_length_continuation tests/test_resilience.py::test_prior_turn_tool_use_does_not_trigger_empty_nudge tests/test_memory_lifecycle.py::test_sync_turn_skips_empty_assistant_response
python -m pytest -q tests/test_continuation_stage_p.py tests/test_resilience.py tests/test_agent_perms.py tests/test_thinking_recovery.py tests/test_response_normalization_loop_stage_h.py tests/test_provider_lifecycle_stage_g.py tests/test_overflow_stage_o.py
python -m pytest -q tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py
python -m py_compile aegis/agent/loop.py tests/test_continuation_stage_p.py tests/test_hardening.py tests/test_resilience.py
git diff --check -- BUILD_STATUS.md aegis/agent/loop.py tests/test_continuation_stage_p.py tests/test_hardening.py tests/test_resilience.py
python -m pytest -q tests/test_overflow_stage_o.py tests/test_reliability.py::test_error_classifier_taxonomy_and_actions tests/test_reliability.py::test_context_overflow_retry_trace_is_recovered tests/test_reliability.py::test_long_context_tier_429_reduces_runtime_context_window
python -m pytest -q tests/test_overflow_stage_o.py tests/test_reliability.py tests/test_runtime_readiness.py
python -m pytest -q tests/test_providers.py tests/test_thinking_recovery.py tests/test_agent_perms.py
python -m pytest -q tests/test_overflow_stage_o.py tests/test_reliability.py tests/test_runtime_readiness.py tests/test_resilience.py tests/test_compaction_stage_n.py tests/test_provider_lifecycle_stage_g.py tests/test_response_normalization_loop_stage_h.py
python -m py_compile aegis/providers/fallback.py aegis/agent/agent.py aegis/agent/loop.py aegis/providers/chat_completions.py tests/test_overflow_stage_o.py tests/test_reliability.py tests/test_thinking_recovery.py
git diff --check -- BUILD_STATUS.md aegis/providers/fallback.py aegis/agent/agent.py aegis/agent/loop.py aegis/providers/chat_completions.py tests/test_overflow_stage_o.py tests/test_reliability.py tests/test_thinking_recovery.py
python -m pytest -q tests/test_compaction_stage_n.py
python -m pytest -q tests/test_compaction_stage_n.py tests/test_resilience.py::test_compaction_records_metadata tests/test_resilience.py::test_compaction_split_records_session_provenance tests/test_resilience.py::test_manual_compaction_split_records_session_provenance tests/test_product_surfaces.py::test_terminal_compress_uses_context_engine_hooks
python -m pytest -q tests/test_compaction_stage_n.py tests/test_compaction_boundaries.py tests/test_compaction_curator.py tests/test_compaction_quality.py tests/test_resilience.py tests/test_agent_perms.py
python -m pytest -q tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py
python -m pytest -q tests/test_session_config.py tests/test_memory_wiring.py tests/test_memory_lifecycle.py tests/test_product_surfaces.py::test_terminal_compress_uses_context_engine_hooks tests/test_product_surfaces.py::test_manual_compress_child_inherits_runtime_controls tests/test_product_surfaces.py::test_session_compress_callback_failure_does_not_break_compaction
python -m py_compile aegis/agent/compaction.py aegis/agent/compaction_runner.py tests/test_compaction_stage_n.py
git diff --check -- BUILD_STATUS.md aegis/agent/compaction.py aegis/agent/compaction_runner.py tests/test_compaction_stage_n.py
python -m pytest -q tests/test_file_state_stage_l.py tests/test_tools.py::test_read_write_edit tests/test_tools.py::test_apply_patch_reports_and_refreshes_stale_state tests/test_tools.py::test_apply_patch_records_lsp_delta_and_freshness tests/test_upgrades_batch.py::test_file_state_stale_warning tests/test_tool_executor_stage_j.py
python -m pytest -q tests/test_tools.py tests/test_upgrades_batch.py tests/test_tool_executor_stage_j.py tests/test_checkpoint_depth.py tests/test_hardening.py
python -m py_compile aegis/tools/file_state.py aegis/tools/builtin.py aegis/tools/extra_builtin.py tests/test_file_state_stage_l.py
git diff --check -- BUILD_STATUS.md aegis/tools/file_state.py aegis/tools/builtin.py aegis/tools/extra_builtin.py tests/test_file_state_stage_l.py
python -m pytest -q tests/test_tool_executor_stage_j.py tests/test_checkpoint_depth.py tests/test_resilience.py tests/test_hardening.py tests/test_agent_perms.py tests/test_observability.py tests/test_response_normalization_loop_stage_h.py tests/test_provider_lifecycle_stage_g.py
python -m pytest -q tests/test_resilience.py tests/test_observability.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_smoke.py tests/test_memory_wiring.py tests/test_tool_executor_stage_j.py tests/test_agent_perms.py
python -m py_compile aegis/agent/agent.py aegis/agent/loop.py tests/test_tool_executor_stage_j.py
git diff --check -- BUILD_STATUS.md aegis/agent/agent.py aegis/agent/loop.py tests/test_tool_executor_stage_j.py
python -m pytest -q tests/test_tool_executor_stage_j.py tests/test_agent_perms.py::test_prompt_routing_is_per_prompt_across_resume
python -m pytest -q tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py
python -m pytest -q tests/test_resilience.py tests/test_observability.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_smoke.py tests/test_memory_wiring.py
python -m py_compile aegis/agent/response_normalization.py aegis/agent/loop.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py
git diff --check -- BUILD_STATUS.md aegis/agent/loop.py aegis/agent/streaming_think_scrubber.py aegis/agent/response_normalization.py tests/test_streaming_think_scrubber_stage_g.py tests/test_provider_lifecycle_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py
python -m pytest -q tests/test_streaming_think_scrubber_stage_g.py tests/test_provider_lifecycle_stage_g.py
python -m pytest -q tests/test_observability.py tests/test_resilience.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py
python -m pytest -q tests/test_resilience.py tests/test_observability.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_smoke.py tests/test_memory_wiring.py
python -m py_compile aegis/agent/agent.py aegis/agent/loop.py aegis/agent/context.py aegis/agent/governance.py aegis/agent/runtime_readiness.py aegis/agent/request_wire.py aegis/agent/streaming_think_scrubber.py
git diff --check -- BUILD_STATUS.md aegis/agent/loop.py aegis/agent/streaming_think_scrubber.py tests/test_streaming_think_scrubber_stage_g.py tests/test_provider_lifecycle_stage_g.py
python -m pytest -q tests/test_governance_stage_f.py tests/test_request_wire_stage_f.py tests/test_smoke.py::test_governance_backfills_orphans tests/test_agent_perms.py::test_governance_normalizes tests/test_agent_perms.py::test_governance_strips_interrupted_tool_replay_blocks tests/test_agent_perms.py::test_governance_scrubs_nested_surrogates_and_reasoning_tags tests/test_hardening.py::test_surrogate_sanitization
python -m pytest -q tests/test_resilience.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_request_wire_stage_f.py tests/test_governance_stage_f.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_smoke.py tests/test_memory_wiring.py
python -m py_compile aegis/agent/governance.py aegis/agent/request_wire.py aegis/agent/loop.py tests/test_governance_stage_f.py tests/test_request_wire_stage_f.py
git diff --check -- aegis/agent/governance.py aegis/agent/request_wire.py aegis/agent/loop.py tests/test_governance_stage_f.py tests/test_request_wire_stage_f.py tests/test_agent_perms.py
python -m pytest -q tests/test_routing_stage_e.py tests/test_runtime_readiness.py
python -m pytest -q tests/test_resilience.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_smoke.py tests/test_memory_wiring.py tests/test_agent_perms.py::test_loop_budget_exhaustion_grace tests/test_observability.py::test_budget_exhausted_grace_fires_provider_hooks
python -m pytest -q tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_governor.py tests/test_routing_cli.py tests/test_sdk.py::test_sdk_run_saves_session_and_trace tests/test_sdk.py::test_sdk_provider_metadata_includes_run_id
python -m pytest -q tests/test_agentic_upgrades.py::test_subagent_relays_child_stream_events tests/test_self_learning.py::test_event_contract_known_types
python -m py_compile aegis/agent/agent.py aegis/agent/loop.py aegis/agent/runtime_readiness.py aegis/agent/events.py
python -m pytest -q tests/test_wakeups.py tests/test_skills_memory.py tests/test_memory_lifecycle.py::test_prefetch_is_wire_only_not_persisted tests/test_resilience.py::test_pre_llm_call_in_place_mutation_is_wire_only tests/test_resilience.py::test_pre_llm_call_returned_list_is_wire_only tests/test_resilience.py::test_pre_llm_call_context_return_is_appended_wire_only tests/test_resilience.py::test_llm_middleware_message_mutation_is_wire_only
python -m pytest -q tests/test_smoke.py::test_agent_system_prompt_includes_runtime_auth tests/test_smoke.py::test_system_prompt_metadata_matches_stored_prompt_on_nonforced_ensure tests/test_smoke.py::test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild tests/test_memory_wiring.py::test_message_refresh_mode_remembers_fact_saved_on_previous_turn tests/test_memory_wiring.py::test_default_agent_run_keeps_memory_snapshot_frozen_until_reset
python -m pytest -q tests/test_resilience.py tests/test_self_verify.py tests/test_session_config.py::test_session_read_and_scroll_include_runtime_metadata tests/test_upgrades_batch.py::test_loop_guard_hard_stop_blocks_no_progress_reads tests/test_upgrades_batch.py::test_loop_guard_hard_stop_records_same_tool_halt_reason tests/test_agent_perms.py::test_loop_multi_tool_then_final tests/test_agent_perms.py::test_loop_budget_exhaustion_grace tests/test_agent_perms.py::test_budget_grace_preserves_responses_state tests/test_thinking_recovery.py::test_loop_strips_thinking_on_signature_400_then_succeeds tests/test_wakeups.py tests/test_skills_memory.py tests/test_memory_lifecycle.py::test_prefetch_is_wire_only_not_persisted
python -m py_compile aegis/agent/agent.py aegis/agent/loop.py aegis/agent/context.py tests/test_smoke.py tests/test_memory_wiring.py tests/test_resilience.py
git diff --check -- BUILD_STATUS.md aegis/agent/agent.py aegis/agent/loop.py aegis/agent/context.py tests/test_wakeups.py tests/test_skills_memory.py tests/test_resilience.py tests/test_smoke.py tests/test_memory_wiring.py
python -m pytest -q tests/test_resilience.py tests/test_self_verify.py tests/test_upgrades_batch.py::test_loop_guard_hard_stop_blocks_no_progress_reads tests/test_upgrades_batch.py::test_loop_guard_hard_stop_records_same_tool_halt_reason tests/test_agent_perms.py::test_loop_multi_tool_then_final tests/test_agent_perms.py::test_loop_budget_exhaustion_grace
python -m pytest -q tests/test_resilience.py tests/test_self_verify.py tests/test_session_config.py::test_session_read_and_scroll_include_runtime_metadata tests/test_upgrades_batch.py::test_loop_guard_hard_stop_blocks_no_progress_reads tests/test_upgrades_batch.py::test_loop_guard_hard_stop_records_same_tool_halt_reason tests/test_agent_perms.py::test_loop_multi_tool_then_final tests/test_agent_perms.py::test_loop_budget_exhaustion_grace tests/test_agent_perms.py::test_budget_grace_preserves_responses_state tests/test_thinking_recovery.py::test_loop_strips_thinking_on_signature_400_then_succeeds
git diff --check -- aegis/agent/agent.py aegis/agent/loop.py aegis/agent/guardrails.py aegis/agent/verification.py aegis/agent/invalid_tool_calls.py aegis/config.py aegis/session.py tests/test_smoke.py tests/test_resilience.py tests/test_self_verify.py tests/test_session_config.py tests/test_upgrades_batch.py BUILD_STATUS.md
python -m py_compile aegis/agent/agent.py aegis/agent/loop.py aegis/session.py tests/test_resilience.py tests/test_self_verify.py tests/test_session_config.py
python -m py_compile aegis/agent/loop.py aegis/agent/guardrails.py aegis/agent/verification.py aegis/agent/invalid_tool_calls.py tests/test_self_verify.py tests/test_resilience.py tests/test_upgrades_batch.py
python -m pytest -q tests/test_installers.py tests/test_product_surfaces.py::test_tracked_text_does_not_use_upstream_branding
python -m py_compile scripts/audit_reference_compare.py aegis/bootstrap.py
bash -n install.sh
python scripts/audit_reference_compare.py --aegis-root . --reference-root <local-reference-root> --out docs/audit/reference-compare
```

Results:

- `5 passed` for focused Stage P continuation/empty/thinking-only checks.
- `7 passed` for Stage P plus length-continuation, prior-turn empty guard, and
  memory-sync empty-response checks.
- `63 passed` for Stage P plus resilience, permissions, thinking recovery,
  response-normalization, provider-lifecycle, and Stage O overflow guardrails.
- `52 passed` for hardening, memory lifecycle, runtime-readiness, routing,
  request-wire, and governance guardrails.
- Stage P Python compilation is clean.
- Stage P `git diff --check` is clean.
- `7 passed` for focused Stage O overflow recovery, request-validation, and
  long-context recovery checks.
- `13 passed` for Stage O plus reliability/runtime-readiness guardrails.
- `83 passed` for provider, thinking-recovery, and agent-permission guardrails.
- `46 passed` for Stage O plus reliability, runtime-readiness, resilience,
  compaction, provider-lifecycle, and response-normalization guardrails.
- Stage O Python compilation is clean.
- Stage O `git diff --check` is clean.
- `2 passed` for focused Stage N compaction durability checks.
- `6 passed` for focused Stage N plus existing compaction/session lifecycle
  checks.
- `79 passed` for Stage N plus compaction boundary/curator/quality,
  resilience, and permission guardrails.
- `31 passed` for routing/readiness/request-wire/governance/response/provider
  lifecycle guardrails.
- `68 passed` for session config, memory wiring/lifecycle, and compaction
  product-surface guardrails.
- Stage N Python compilation is clean.
- Stage N `git diff --check` is clean.
- `12 passed` for focused Stage L task-aware file-state/file-tool checks.
- `92 passed` for broader Stage L filesystem/tool/checkpoint/hardening checks.
- Stage L Python compilation is clean.
- Stage L `git diff --check` is clean.
- `101 passed` for Stage J tool-executor/checkpoint/routing broad slice.
- `207 passed` for broad Stage A-J request-boundary, observer, response,
  runtime, routing, memory, wakeup, skills, smoke, and tool-executor guardrails.
- Stage J Python compilation is clean.
- Stage J `git diff --check` is clean.
- `5 passed` for Stage J focused checkpoint ordering plus the resume
  one-turn-routing broad regression.
- `14 passed` for focused Stage H response normalization and loop-boundary
  checks.
- `183 passed` for broad Stage A-H request-boundary, observer, response,
  resilience, memory, wakeup, skills, and smoke guardrails.
- Stage H Python compilation is clean.
- Stage H `git diff --check` is clean.
- `16 passed` for focused Stage G stream scrubber and provider lifecycle checks.
- `73 passed` for Stage G plus observability/resilience guardrails.
- `169 passed` for broad Stage A-G/F request-boundary, observer, resilience,
  memory, wakeup, skills, and smoke guardrails.
- Stage G Python compilation is clean.
- Stage G `git diff --check` is clean.
- `10 passed` for focused Stage F governance and legacy governance checks.
- `122 passed` for broad Stage D/E/F request-boundary guardrails.
- Stage F Python compilation is clean.
- Stage F `git diff --check` is clean.
- `7 passed` for focused Stage E routing/readiness checks.
- `112 passed` for broader Stage A-D plus Stage E grace-readiness guardrails.
- `29 passed` for worker Stage E plus governor/routing CLI/SDK checks.
- `2 passed` for event-contract/subagent relay checks.
- Stage E Python compilation is clean.
- `39 passed` for focused core harness loop/resilience/self-verify/session checks.
- `83 passed` for broader Stage C plus Stage D volatile-context harness checks.
- `44 passed` for focused Stage D wakeup/skill/memory/plugin/middleware wire-copy checks.
- `5 passed` for Stage D volatile system-prompt and memory-refresh provider-wire checks.
- `7 passed` for focused Stage C turn-prologue checks.
- `32 passed` for the prior focused core harness loop/resilience/self-verify checks.
- `3 passed` for focused Stage B prompt-cache checks.
- `2 passed` for the Stage A turn-start persistence focused check.
- `git diff --check` is clean for the current harness slice.
- Harness Python compilation is clean.
- `17 passed` for installer/product guard checks.
- Branding guard was green.
- Neutral inventory regenerated successfully.

Latest Stage S verification completed:

```bash
python -m pytest -q tests/test_session_stage_s.py tests/test_session_switch_stage_s.py
python -m pytest -q tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_learn_recall.py tests/test_resilience.py tests/test_memory_lifecycle.py tests/test_sdk.py tests/test_product_surfaces.py::test_terminal_session_picker_resume_and_branch tests/test_product_surfaces.py::test_terminal_resume_reapplies_session_runtime
python -m pytest -q tests/test_agentic_upgrades.py::test_context_engine_lifecycle_hooks tests/test_agentic_upgrades.py::test_subagent_parallel_and_registry tests/test_agentic_upgrades.py::test_subagent_relays_child_stream_events tests/test_agentic_upgrades.py::test_background_spawn_inherits_parent_runtime_controls
python -m py_compile aegis/session.py aegis/sdk.py aegis/cli/repl.py aegis/cli/tui.py aegis/surface.py aegis/tools/agentic.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py
git diff --check -- BUILD_STATUS.md aegis/session.py aegis/sdk.py aegis/cli/repl.py aegis/cli/tui.py aegis/surface.py aegis/tools/agentic.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py
```

Latest Stage T verification completed:

```bash
python -m pytest -q tests/test_subagent_background_stage_t.py
python -m pytest -q tests/test_typed_subagents.py tests/test_delegation_defaults.py
python -m pytest -q tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py
python -m pytest -q tests/test_typed_subagents.py tests/test_agentic_upgrades.py tests/test_delegation_defaults.py
python -m pytest -q tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_typed_subagents.py tests/test_agentic_upgrades.py tests/test_delegation_defaults.py
python -m pytest -q tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_typed_subagents.py tests/test_agentic_upgrades.py tests/test_delegation_defaults.py tests/test_memory_lifecycle.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_product_surfaces.py::test_terminal_session_picker_resume_and_branch tests/test_product_surfaces.py::test_terminal_resume_reapplies_session_runtime
python -m py_compile aegis/tools/agentic.py aegis/background.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py
git diff --check
```

Results: `3 passed`; `27 passed`; `9 passed`; `47 passed`; `56 passed`;
`96 passed`; Stage T Python compilation is clean; Stage T `git diff --check`
is clean.

Latest Stage U verification completed:

```bash
python -m pytest -q tests/test_deferred_tools.py tests/test_stage_u_deferred_bridge.py tests/test_tool_schema_validation.py tests/test_mcp_catalog.py tests/test_stage_u_schema_sanitizer.py tests/test_mcp_cli.py tests/test_net_and_edit.py::test_schema_sanitizer_strips_dialect_metadata_keeps_annotations_and_structure tests/test_agentic_upgrades.py::test_deferred_tool_selectors_cover_dynamic_sources tests/test_thinking_recovery.py tests/test_response_normalization_stage_h.py::test_existing_structured_reasoning_and_thinking_blocks_are_preserved tests/test_response_normalization_loop_stage_h.py::test_loop_preserves_structured_reasoning_and_anthropic_thinking_blocks tests/test_providers.py::test_anthropic_coalesces_tool_results
python -m pytest -q tests/test_agentic_upgrades.py tests/test_mcp_catalog.py tests/test_mcp_cli.py tests/test_deferred_tools.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py
python -m py_compile aegis/tools/devtools.py aegis/agent/agent.py aegis/providers/schema.py aegis/mcp/client.py aegis/mcp/server.py aegis/cli/main.py aegis/agent/loop.py aegis/agent/guardrails.py aegis/tools/agentic.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py
git diff --check
```

Results: `44 passed`; `46 passed`; final focused rerun `37 passed`;
Stage U Python compilation is clean; Stage U `git diff --check` is clean.

Latest Stage V verification completed:

```bash
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_credential_pools.py tests/test_auth_cli.py tests/test_reliability.py tests/test_self_learning.py tests/test_overflow_stage_o.py tests/test_thinking_recovery.py tests/test_observability.py::test_fallback_attempts_fire_provider_hooks tests/test_providers.py::test_fallback_provider_retries tests/test_providers.py::test_fallback_provider_retries_primary_after_auth_rotation tests/test_providers.py::test_fallback_provider_uses_active_first_after_failover tests/test_providers.py::test_fallback_provider_delegates_cancel_to_active_provider
ruff check aegis/providers/fallback.py aegis/credentials.py aegis/providers/auth.py aegis/providers/base.py aegis/agent/agent.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
python -m py_compile aegis/providers/fallback.py aegis/credentials.py aegis/providers/auth.py aegis/providers/base.py aegis/agent/agent.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
git diff --check -- aegis/providers/fallback.py aegis/credentials.py aegis/providers/auth.py aegis/providers/base.py aegis/agent/agent.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
```

Results: `20 passed`; `68 passed`; ruff clean; Stage V Python compilation is
clean; Stage V `git diff --check` is clean.

Latest Stage W verification completed:

```bash
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_cost_pricing.py tests/test_tracing_evals.py tests/test_rpc_surface.py::test_rpc_server_initializes_runs_and_exposes_session_trace tests/test_observability.py::test_fallback_attempts_fire_provider_hooks
AEGIS_HOME="$(mktemp -d)" pytest -q tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_cost_pricing.py tests/test_tracing_evals.py tests/test_observability.py tests/test_rpc_surface.py tests/test_sdk.py tests/test_live_activity.py tests/test_provider_lifecycle_stage_g.py tests/test_response_normalization_loop_stage_h.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py
python -m py_compile aegis/usage_log.py aegis/agent/agent.py aegis/agent/loop.py aegis/tracing.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py
ruff check aegis/usage_log.py aegis/agent/agent.py aegis/agent/loop.py aegis/tracing.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py
git diff --check -- aegis/usage_log.py aegis/agent/agent.py aegis/agent/loop.py aegis/tracing.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py BUILD_STATUS.md
```

Results: `35 passed`; post-joinability focused rerun `36 passed`; broad
Stage W/V/RPC/SDK/live-activity/provider guardrail slice `105 passed`; ruff
clean; Stage W Python compilation is clean; Stage W `git diff --check` is
clean.

Latest Stage Z provider/session/backend/prompt wave verification completed:

```bash
python -m pytest -q tests/test_stage_z_provider_pooling.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_credential_pools.py
python -m pytest -q tests/test_session_stage_s.py tests/test_compaction_stage_n.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_tools.py
python -m pytest -q tests/test_smoke.py::test_agent_system_prompt_includes_runtime_auth tests/test_smoke.py::test_nonforced_ensure_reuses_stored_system_prompt_without_rebuild tests/test_smoke.py::test_provider_wire_volatile_context_does_not_churn_prompt_snapshot tests/test_smoke.py::test_skills_index_snapshot_fingerprint_tracks_rebuilt_skill_context tests/test_skills_memory.py::test_skill_discovery_refreshes_when_files_change tests/test_skills_memory.py::test_skill_tool_create_updates_same_turn_prompt_index tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python -m pytest -q tests/test_smoke.py tests/test_resilience.py tests/test_agent_perms.py tests/test_memory_wiring.py tests/test_memory_lifecycle.py tests/test_skills_memory.py tests/test_wakeups.py tests/test_runtime_readiness.py tests/test_routing_stage_e.py tests/test_governance_stage_f.py tests/test_request_wire_stage_f.py tests/test_provider_lifecycle_stage_g.py tests/test_streaming_think_scrubber_stage_g.py tests/test_response_normalization_stage_h.py tests/test_response_normalization_loop_stage_h.py tests/test_tool_executor_stage_j.py tests/test_file_state_stage_l.py tests/test_self_verify.py tests/test_compaction_stage_n.py tests/test_overflow_stage_o.py tests/test_continuation_stage_p.py tests/test_finalization_stage_r.py tests/test_turn_finalization_stage_r.py tests/test_session_config.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_provider_fallback.py tests/test_stage_v_credentials.py tests/test_stage_v_auth_integration.py tests/test_stage_w_usage_cost.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_noninteractive_oauth.py tests/test_stage_z_mcp_reconnect_parking.py
ruff check aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python -m py_compile aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
git diff --check -- aegis/agent/context.py aegis/agent/agent.py aegis/credentials.py aegis/providers/auth.py aegis/tools/backends.py aegis/agent/compaction_runner.py tests/test_smoke.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_backend_interrupts.py tests/test_compaction_stage_n.py
python scripts/audit_reference_compare.py
```

Results: `21 passed`; `91 passed`; `19 passed`; broad core sweep `372 passed
in 20.43s`; ruff clean; Python compilation clean; diff whitespace clean;
inventory regenerated as 1,017 AEGIS source-like files / 285,160 LOC vs 5,459
Hermes source-like files / 2,310,169 LOC.

Latest continuation verification, after the session/provider/skills/MCP/tool
result parity patches:

```bash
python -m pytest -q tests/test_skills_memory.py
python -m pytest -q tests/test_coding_context.py tests/test_skills_memory.py
python -m pytest -q tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_stage_z_append_row_persistence.py
python -m pytest -q tests/test_stage_z_provider_rate_controls.py tests/test_stage_v_provider_fallback.py tests/test_stage_z_provider_pooling.py
python -m pytest -q tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_backend_storage_handoff.py
python -m pytest -q tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_smoke.py::test_mcp_client_roundtrip
python -m pytest -q tests/test_smoke.py tests/test_agent_perms.py tests/test_coding_context.py tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_memory_wiring.py tests/test_reliability.py tests/test_resilience.py tests/test_thinking_recovery.py tests/test_bootstrap.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_file_state_stage_l.py tests/test_finalization_stage_r.py tests/test_governance_stage_f.py tests/test_overflow_stage_o.py tests/test_provider_lifecycle_stage_g.py tests/test_request_wire_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_response_normalization_stage_h.py tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_skills_memory.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_w_usage_cost.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_streaming_think_scrubber_stage_g.py tests/test_subagent_background_stage_t.py tests/test_subagent_stage_t.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py
python -m compileall -q aegis
ruff check aegis/skills.py aegis/agent/coding_context.py aegis/agent/agent.py aegis/config.py aegis/session.py aegis/providers/fallback.py aegis/mcp/client.py aegis/tools/tool_result_storage.py aegis/tools/base.py aegis/tools/registry.py aegis/tools/builtin.py tests/test_skills_memory.py tests/test_coding_context.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_backend_storage_handoff.py
git diff --check -- aegis/skills.py aegis/agent/coding_context.py aegis/agent/agent.py aegis/config.py aegis/session.py aegis/providers/fallback.py aegis/mcp/client.py aegis/tools/tool_result_storage.py aegis/tools/base.py aegis/tools/registry.py aegis/tools/builtin.py tests/test_skills_memory.py tests/test_coding_context.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_tool_result_storage.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_backend_storage_handoff.py
python scripts/audit_reference_compare.py
```

Results: skills/coding slices `45 passed`; session repair slice `35 passed`;
provider/rate-control slice `24 passed`; tool-result/backend handoff slice
`15 passed`; MCP slice `83 passed`; combined core harness sweep `458 passed in
31.13s`; `python -m compileall -q aegis` clean; ruff clean; diff whitespace
clean; inventory regenerated as 1,017 AEGIS source-like files / 287,057 LOC vs
5,459 Hermes source-like files / 2,310,169 LOC.

Previous full Stage Z continuation push, after session repair, borrowed-token
resync, persisted-output rehydration, MCP sampling/server requests, multi-pass
compaction planning, and Modal/Daytona-style remote sync:

```bash
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_stage_z_append_row_persistence.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py
python -m pytest -q tests/test_credential_pools.py tests/test_stage_v_auth_integration.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_credentials.py
python -m pytest -q tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_tools.py -k 'backend or docker or process or task_terminal or remote or nonlocal or daytona or modal'
python -m pytest -q tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py
python -m pytest -q tests/test_compaction_stage_n.py tests/test_overflow_stage_o.py tests/test_reliability.py::test_context_overflow_retry_trace_is_recovered tests/test_reliability.py::test_long_context_tier_429_reduces_runtime_context_window
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_smoke.py tests/test_agent_perms.py tests/test_coding_context.py tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_memory_wiring.py tests/test_reliability.py tests/test_resilience.py tests/test_thinking_recovery.py tests/test_bootstrap.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_file_state_stage_l.py tests/test_finalization_stage_r.py tests/test_governance_stage_f.py tests/test_overflow_stage_o.py tests/test_provider_lifecycle_stage_g.py tests/test_request_wire_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_response_normalization_stage_h.py tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_skills_memory.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_w_usage_cost.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_session_repair.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_remote_environments.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_streaming_think_scrubber_stage_g.py tests/test_subagent_background_stage_t.py tests/test_subagent_stage_t.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_credential_pools.py
python -m compileall -q aegis
ruff check aegis/session.py aegis/providers/auth.py aegis/agent/compaction.py aegis/agent/compaction_runner.py aegis/mcp/client.py aegis/mcp/server.py aegis/tools/backends.py aegis/tools/builtin.py aegis/tools/tool_result_storage.py tests/test_stage_z_session_repair.py tests/test_stage_v_auth_integration.py tests/test_observability.py tests/test_compaction_stage_n.py tests/test_stage_z_mcp_*.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py
git diff --check -- aegis/session.py aegis/providers/auth.py aegis/agent/compaction.py aegis/agent/compaction_runner.py aegis/mcp/client.py aegis/mcp/server.py aegis/tools/backends.py aegis/tools/builtin.py aegis/tools/tool_result_storage.py tests/test_stage_z_session_repair.py tests/test_stage_v_auth_integration.py tests/test_observability.py tests/test_compaction_stage_n.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_reconnect_parking.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_tool_result_storage.py
python scripts/audit_reference_compare.py
```

Results: session repair slice `39 passed`; provider/auth slice `27 passed`;
remote backend slice `35 passed`; MCP slice `60 passed`; compaction/overflow
slice `13 passed`; combined core Stage Z sweep `489 passed in 31.00s`;
`python -m compileall -q aegis` clean; ruff clean; diff whitespace clean;
inventory regenerated as 1,020 AEGIS source-like files / 290,667 LOC vs 5,459
Hermes source-like files / 2,310,169 LOC.

Previous integrated verification after MCP roots/notifications/completion,
provider removal suppression, Modal snapshot restore, and WAL sidecar recovery:

```bash
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py tests/test_stage_z_provider_pooling.py tests/test_stage_v_auth_integration.py tests/test_auth_cli.py tests/test_credential_pools.py tests/test_stage_z_remote_environments.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_mcp_cli.py
AEGIS_HOME="$(mktemp -d)" PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_smoke.py tests/test_agent_perms.py tests/test_coding_context.py tests/test_hardening.py tests/test_memory_lifecycle.py tests/test_memory_wiring.py tests/test_reliability.py tests/test_resilience.py tests/test_thinking_recovery.py tests/test_bootstrap.py tests/test_compaction_stage_n.py tests/test_continuation_stage_p.py tests/test_file_state_stage_l.py tests/test_finalization_stage_r.py tests/test_governance_stage_f.py tests/test_overflow_stage_o.py tests/test_provider_lifecycle_stage_g.py tests/test_request_wire_stage_f.py tests/test_response_normalization_loop_stage_h.py tests/test_response_normalization_stage_h.py tests/test_routing_stage_e.py tests/test_runtime_readiness.py tests/test_session_stage_s.py tests/test_session_switch_stage_s.py tests/test_session_config.py tests/test_skills_memory.py tests/test_stage_u_deferred_bridge.py tests/test_stage_u_schema_sanitizer.py tests/test_stage_v_auth_integration.py tests/test_stage_v_credentials.py tests/test_stage_v_provider_fallback.py tests/test_stage_w_activity.py tests/test_stage_w_trace_timeline.py tests/test_stage_w_usage_cost.py tests/test_stage_z_append_row_persistence.py tests/test_stage_z_session_repair.py tests/test_stage_z_approval_wiring.py tests/test_stage_z_backend_interrupts.py tests/test_stage_z_backend_storage_handoff.py tests/test_stage_z_gateway_approval_queue.py tests/test_stage_z_mcp_*.py tests/test_mcp_catalog.py tests/test_stage_z_provider_pooling.py tests/test_stage_z_provider_rate_controls.py tests/test_stage_z_remote_environments.py tests/test_stage_z_sandbox_approval_context.py tests/test_stage_z_thread_context.py tests/test_stage_z_tool_persistence.py tests/test_stage_z_tool_result_storage.py tests/test_streaming_think_scrubber_stage_g.py tests/test_subagent_background_stage_t.py tests/test_subagent_stage_t.py tests/test_tool_executor_stage_j.py tests/test_turn_finalization_stage_r.py tests/test_credential_pools.py tests/test_auth_cli.py tests/test_observability.py tests/test_session_locks.py
python -m compileall -q aegis
python scripts/audit_reference_compare.py
```

Results: focused integration slice `117 passed`; broad core Stage Z sweep `537
passed in 38.40s`; compileall clean; ruff, py_compile, and diff whitespace
checks clean for touched files; inventory regenerated as 1,021 AEGIS
source-like files / 291,681 LOC vs 5,459 Hermes source-like files / 2,310,169
LOC.

## Do-Not-Miss Requirement Ledger

| Requirement | Evidence needed | Current evidence | Status |
| --- | --- | --- | --- |
| Start from the full harness map and proceed by evidence-backed domain | This file has ordered stages and current active domain lanes | Full-harness ledger above | active |
| Read Hermes first | Reading log with direct files/sections | Reading log above; full coverage not yet complete | active |
| Core harness first | Active patches stay in the selected harness lane and do not blur unrelated UI/product changes | Current touched core files listed | active |
| Do not touch Synth | No Synth/synth paths in current patch | Git status must be checked each turn | active |
| Include UI/web surfaces | Dashboard, desktop, TUI, web/static, gateway, CLI, docs, and packaging are full-harness surfaces | Domain map lists owner paths; patch them in explicit lanes | active |
| Exclude proprietary Nous product surfaces | No Nous portal/proprietary product code should be ported | Status scope records open-harness parity only | active |
| Preserve Claude/Anthropic | Do not strip thinking blocks from canonical session; keep provider wire-copy semantics and do not clobber Claude OAuth behavior | `_provider_wire_messages()` keeps thinking mutations wire-only; Stage V OAuth reporting is local to AEGIS auth entries, borrowed Claude Code credentials stay reference-only, and Claude import no longer copies raw Claude access/refresh tokens into AEGIS auth storage | active |
| Use subagents | Descartes/Sagan/Volta/Ohm audit lanes completed; previous workers completed; Stage T workers Aquinas/Leibniz, Stage U workers Banach/Franklin, Stage V workers Aristotle/Hooke, Stage W workers Euclid/Curie/Copernicus, and Stage Z workers Hume/Ptolemy/Faraday/Halley/Russell/Nash/Anscombe/Darwin/Beauvoir/Bohr/Meitner/Dewey/Lovelace/Plato/Mendel/Turing/Socrates/Zeno/Galileo/Lagrange/Einstein/Helmholtz/Averroes/Sartre/Hegel/Herschel/Lorentz/Huygens/Hilbert/Wegener/Tesla/Erdos/Locke/Mencius/Poincare/Kuhn/Heisenberg/Carver/Planck/Avicenna/Rawls landed focused patches | Subagent log above | active |
| Track percentage and LOC | Full matrix and core file LOC tables | Tables above | active |
| Tests prove each stage | Stage-specific verification gates | Stage A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V, W green for first pass; latest Stage Z core wave broad sweep `564 passed` | active |

## Known Dirty Worktree Outside Current Harness Slice

The repository already contains unrelated dirty files. Do not revert them unless
the user explicitly asks. Current harness work should ignore unrelated dirty
files outside the selected lane; dashboard, desktop, web/static, installer, and
packaging may be edited when their dedicated parity lane is active.

Harness files intentionally touched in this pass:

- `BUILD_STATUS.md`
- `aegis/agent/events.py`
- `aegis/agent/guardrails.py`
- `aegis/agent/governance.py`
- `aegis/agent/loop.py`
- `aegis/agent/invalid_tool_calls.py`
- `aegis/agent/request_wire.py`
- `aegis/agent/response_normalization.py`
- `aegis/agent/runtime_readiness.py`
- `aegis/agent/streaming_think_scrubber.py`
- `aegis/agent/compaction_runner.py`
- `aegis/agent/verification.py`
- `aegis/agent/agent.py`
- `aegis/agent/coding_context.py`
- `aegis/agent/context.py`
- `aegis/usage_log.py`
- `aegis/tracing.py`
- `aegis/providers/fallback.py`
- `aegis/providers/auth.py`
- `aegis/providers/base.py`
- `aegis/providers/chat_completions.py`
- `aegis/providers/schema.py`
- `aegis/credentials.py`
- `aegis/mcp/client.py`
- `aegis/mcp/oauth_manager.py`
- `aegis/mcp/server.py`
- `aegis/cli/main.py`
- `aegis/tools/builtin.py`
- `aegis/tools/extra_builtin.py`
- `aegis/tools/base.py`
- `aegis/tools/backends.py`
- `aegis/tools/file_state.py`
- `aegis/tools/devtools.py`
- `aegis/tools/registry.py`
- `aegis/tools/tool_result_storage.py`
- `aegis/tools/thread_context.py`
- `aegis/tools/interrupt.py`
- `aegis/config.py`
- `aegis/skills.py`
- `aegis/session.py`
- `aegis/sdk.py`
- `aegis/cli/repl.py`
- `aegis/cli/tui.py`
- `aegis/surface.py`
- `aegis/background.py`
- `aegis/tools/agentic.py`
- `tests/test_smoke.py`
- `tests/test_memory_wiring.py`
- `tests/test_resilience.py`
- `tests/test_governance_stage_f.py`
- `tests/test_provider_lifecycle_stage_g.py`
- `tests/test_request_wire_stage_f.py`
- `tests/test_response_normalization_stage_h.py`
- `tests/test_response_normalization_loop_stage_h.py`
- `tests/test_routing_stage_e.py`
- `tests/test_runtime_readiness.py`
- `tests/test_tool_executor_stage_j.py`
- `tests/test_compaction_stage_n.py`
- `tests/test_overflow_stage_o.py`
- `tests/test_continuation_stage_p.py`
- `tests/test_agent_perms.py`
- `tests/test_hardening.py`
- `tests/test_file_state_stage_l.py`
- `tests/test_self_verify.py`
- `tests/test_session_config.py`
- `tests/test_session_stage_s.py`
- `tests/test_session_switch_stage_s.py`
- `tests/test_subagent_stage_t.py`
- `tests/test_subagent_background_stage_t.py`
- `tests/test_stage_u_deferred_bridge.py`
- `tests/test_stage_u_schema_sanitizer.py`
- `tests/test_stage_v_provider_fallback.py`
- `tests/test_stage_v_credentials.py`
- `tests/test_stage_v_auth_integration.py`
- `tests/test_stage_w_usage_cost.py`
- `tests/test_stage_w_activity.py`
- `tests/test_stage_w_trace_timeline.py`
- `tests/test_stage_z_approval_wiring.py`
- `tests/test_stage_z_append_row_persistence.py`
- `tests/test_stage_z_backend_interrupts.py`
- `tests/test_stage_z_backend_storage_handoff.py`
- `tests/test_stage_z_gateway_approval_queue.py`
- `tests/test_stage_z_sandbox_approval_context.py`
- `tests/test_stage_z_tool_result_storage.py`
- `tests/test_stage_z_mcp_background_notifications.py`
- `tests/test_stage_z_mcp_elicitation.py`
- `tests/test_stage_z_mcp_lifecycle.py`
- `tests/test_stage_z_mcp_oauth.py`
- `tests/test_stage_z_mcp_oauth_login.py`
- `tests/test_stage_z_mcp_oauth_manager.py`
- `tests/test_stage_z_mcp_oauth_metadata.py`
- `tests/test_stage_z_mcp_oauth_invalid_client.py`
- `tests/test_stage_z_mcp_noninteractive_oauth.py`
- `tests/test_stage_z_mcp_reconnect_parking.py`
- `tests/test_stage_z_mcp_sampling.py`
- `tests/test_stage_z_provider_pooling.py`
- `tests/test_stage_z_provider_rate_controls.py`
- `tests/test_stage_z_remote_environments.py`
- `tests/test_stage_z_session_repair.py`
- `tests/test_stage_z_thread_context.py`
- `tests/test_stage_z_tool_persistence.py`
- `tests/test_streaming_think_scrubber_stage_g.py`
- `tests/test_upgrades_batch.py`
- `tests/test_wakeups.py`
- `tests/test_coding_context.py`
- `tests/test_skills_memory.py`
- `tests/test_credential_pools.py`
- `tests/test_observability.py`

## Latest Agent Init Provider Header/Override Wave - 2026-07-01

This wave continued the file-by-file Hermes-first pass from
`agent/agent_init.py` into the provider startup/header lane, using the
Hermes Codex Responses transport and Anthropic adapter as companion references.
Hermes was read only from `/tmp/aegis-hermes-agent-reference`; all tests and
patches ran only in AEGIS. Synth/synth and proprietary Nous portal/product
surfaces stayed out of scope.

Hermes/reference read before patching:

- `agent/agent_init.py` custom provider `extra_body` merge range `147-163`.
- `agent/agent_init.py` provider default-header startup range `824-975`.
- `agent/agent_init.py` provider/cache/tool/status banner range `1021-1093`.
- `agent/agent_init.py` context-limit startup print range `1815-1845`.
- `agent/transports/codex.py` Responses request kwargs/cache/header range
  `240-385`.
- `agent/anthropic_adapter.py` common/OAuth beta constants and client-header
  wiring ranges `338-386` and `680-835`.
- Hermes provider tests for request overrides, xAI service-tier stripping,
  Anthropic betas, and Codex Cloudflare account headers.

Patches landed:

- `aegis/providers/chat_completions.py` now owns shared request override
  helpers: deep payload merge, `extra_headers`, per-request timeout, and
  defensive xAI `service_tier` stripping.
- `aegis/providers/base.py` exposes `request_overrides` on the transport
  contract and forwards provider-level overrides only into transports that
  declare support.
- `aegis/providers/responses.py` now accepts request overrides, merges
  override headers/body fields without leaking `extra_headers`/`extra_body` into
  JSON, strips xAI `service_tier` regardless of source, and adds xAI Responses
  cache routing with `x-grok-conv-id` plus a stable `prompt_cache_key`.
- `aegis/providers/anthropic.py` now accepts request overrides, preserves
  prompt-cache markers while merging override body/header fields, honors
  per-request timeout, and falls back invalid `prompt_caching.cache_ttl` values
  to Hermes' default `5m`.
- `aegis/providers/registry.py` now carries Anthropic common beta headers,
  OAuth-only Claude Code identity headers, `model.default_headers`,
  `model.request_overrides`, custom-provider `extra_body`, and custom-provider
  `extra_headers` into the bound provider.
- `aegis/providers/auth.py` now uses canonical `ChatGPT-Account-ID` casing for
  Codex direct backend and OpenAI-Codex OAuth account attribution.
- `tests/test_provider_request_overrides.py` was added for the focused parity
  slice, including provider forwarding, Responses merge/no-leak behavior, xAI
  service-tier stripping, xAI cache routing, Anthropic merge/cache/TTL behavior,
  config default headers/request overrides, and custom-provider `extra_body`
  merge precedence.

Focused verification:

```bash
python -m pytest -q tests/test_provider_request_overrides.py
# 8 passed

python -m pytest -q tests/test_providers.py -k "service_tier or account_header or custom_provider or codex or auth"
# 21 passed, 39 deselected

python -m pytest -q tests/test_stage_v_auth_integration.py -k "api_extra_headers or oauth or codex"
# 13 passed, 7 deselected

python -m pytest -q tests/test_provider_request_overrides.py tests/test_providers.py -k "provider_request_overrides or service_tier or account_header or custom_provider or codex or auth"
# 29 passed, 39 deselected

python -m pytest -q tests/test_provider_request_overrides.py tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py tests/test_context_engine_parity.py tests/test_stage_v_auth_integration.py -k "not slow"
# 49 passed

python -m ruff check aegis/providers/base.py aegis/providers/chat_completions.py aegis/providers/responses.py aegis/providers/anthropic.py aegis/providers/registry.py aegis/providers/auth.py aegis/config.py tests/test_provider_request_overrides.py tests/test_providers.py
# All checks passed

python -m py_compile aegis/providers/base.py aegis/providers/chat_completions.py aegis/providers/responses.py aegis/providers/anthropic.py aegis/providers/registry.py aegis/providers/auth.py aegis/config.py tests/test_provider_request_overrides.py tests/test_providers.py

git diff --check -- aegis/providers/base.py aegis/providers/chat_completions.py aegis/providers/responses.py aegis/providers/anthropic.py aegis/providers/registry.py aegis/providers/auth.py aegis/config.py tests/test_provider_request_overrides.py tests/test_providers.py BUILD_STATUS.md
# clean
```

## Latest Agent Init Prompt-Control/Retry/Ollama/Context-Engine Wave - 2026-07-01

This wave continued the file-by-file Hermes-first pass with
`agent/agent_init.py` and the matching prompt-control callsites in
`agent/system_prompt.py`, then completed the same file's Ollama `num_ctx`
startup lane using Hermes `agent/model_metadata.py` and
`agent/transports/chat_completions.py` as the reference. The next pass in the
same Hermes file completed the context-engine selection/lifecycle lane using
`plugins/context_engine/__init__.py` as the companion reference. It did not
touch Synth/synth or proprietary Nous portal/product surfaces. Dashboard,
desktop, web/static, TUI, and packaging remain in full-harness scope for
dedicated lanes.

Hermes/reference read before patching:

- `agent/agent_init.py` full file, 1,888 LOC.
- `agent/system_prompt.py` prompt-control ranges `64-110`, `160-250`,
  `332-408`.
- `agent/conversation_loop.py` API retry-count callsite `970-1000`.
- `agent/model_metadata.py` local endpoint helper `499` and
  `query_ollama_num_ctx` helper `1234`.
- `agent/transports/chat_completions.py` request-overrides/Ollama `num_ctx`
  build paths around `450`, `533`, and `564`.
- `agent/agent_init.py` context-engine selection/lifecycle range `1440-1760`.
- `plugins/context_engine/__init__.py` full file, 285 LOC, especially loader
  ranges `1-90` and collector/command registration ranges `180-260`.

Patches landed:

- `aegis/agent/context.py` now splits the agentic prompt into separate
  AEGIS-native blocks for tool-use enforcement, tool verification,
  task-completion/no-fabrication guidance, and parallel-tool-call guidance.
- `agent.tool_use_enforcement` now supports Hermes-style `auto`, true, false,
  and model-substring list behavior. Default `auto` remains model-family gated.
- `agent.task_completion_guidance`, `agent.parallel_tool_call_guidance`, and
  `agent.environment_probe` now control prompt assembly and provider-wire
  volatile environment context.
- `platform_hints` now supports bare-string append, `{append: ...}`, and
  `{replace: ...}` per platform without leaking hints to other platforms.
- `aegis/agent/agent.py` stores the same init-time prompt-control attributes
  Hermes keeps on the agent, and passes active model/tool availability into the
  prompt builder.
- `agent.api_max_retries` now controls total model API attempts per call
  (default 3, minimum 1, invalid values fall back to 3). Retry is limited to
  provider failures classified with recovery action `retry`; context compaction,
  output-cap reduction, and thinking-strip recoveries keep their existing
  bounded paths.
- `aegis/model_meta.py` now includes the Hermes-style local endpoint detector
  and `query_ollama_num_ctx()` helper. It strips `ollama/`, `ollama:`, and
  `local:` prefixes, calls Ollama `/api/show`, prefers explicit Modelfile
  `num_ctx`, and falls back to `model_info.*context_length`.
- `aegis/agent/agent.py` now resolves `_ollama_num_ctx` during init, caps
  auto-detected values to explicit `model.context_length`, honors explicit
  `model.ollama_num_ctx` without capping, and stores
  `request_overrides.extra_body.options.num_ctx` on the provider.
- `aegis/providers/base.py` passes provider-level `request_overrides` into
  transports that accept them, and `aegis/providers/chat_completions.py` merges
  `request_overrides.extra_body` directly into the outgoing JSON payload.
- `aegis/agent/context_engine.py` now accepts Hermes-style `context.engine`
  when the AEGIS `agent.context_engine` value is default/compressor, treats
  `compressor` as the built-in default alias, deep-copies registered engine
  instances, and loads context-engine plugin directories from configured,
  user-home, and project roots.
- `aegis/agent/context_engine.py` also supports plugin-style `register(ctx)`
  loading and class fallback loading for context-engine plugin directories,
  while preserving the existing `agent.context_engine` registry API.
- `aegis/agent/agent.py` now skips duplicate context-engine tool names before
  registry registration, records `_context_engine_tool_names` only for newly
  visible tools, and passes richer optional `on_session_start` metadata without
  breaking one-argument hooks.

Focused verification:

```bash
pytest -q tests/test_agent_init_prompt_controls.py
# 9 passed

pytest -q tests/test_ollama_num_ctx.py
# 5 passed

pytest -q tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py
# 14 passed

pytest -q tests/test_context_engine_parity.py
# 7 passed

pytest -q tests/test_context_engine_parity.py tests/test_agentic_upgrades.py::test_context_engine_default_and_register tests/test_agentic_upgrades.py::test_context_engine_lifecycle_hooks tests/test_product_surfaces.py::test_terminal_compress_uses_context_engine_hooks tests/test_compaction_quality.py::test_aux_compression_preflight_lowers_live_threshold
# 11 passed

pytest -q tests/test_smoke.py -k "system_prompt or provider_wire_volatile or nonforced_ensure"
# 5 passed, 27 deselected

pytest -q tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py tests/test_reliability.py tests/test_hardening.py::test_provider_retries_transient tests/test_thinking_recovery.py tests/test_overflow_stage_o.py
# 28 passed

ruff check aegis/model_meta.py aegis/providers/base.py aegis/providers/chat_completions.py aegis/agent/agent.py aegis/agent/context_engine.py aegis/config.py aegis/agent/context.py aegis/agent/loop.py tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py tests/test_context_engine_parity.py tests/test_smoke.py
python -m py_compile aegis/model_meta.py aegis/providers/base.py aegis/providers/chat_completions.py aegis/agent/agent.py aegis/agent/context_engine.py aegis/config.py aegis/agent/context.py aegis/agent/loop.py tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py tests/test_context_engine_parity.py tests/test_smoke.py
git diff --check -- aegis/model_meta.py aegis/providers/base.py aegis/providers/chat_completions.py aegis/agent/agent.py aegis/agent/context_engine.py aegis/config.py aegis/agent/context.py aegis/agent/loop.py tests/test_ollama_num_ctx.py tests/test_agent_init_prompt_controls.py tests/test_context_engine_parity.py tests/test_smoke.py BUILD_STATUS.md
# clean
```

## Latest System Prompt Profile-Safety Wave - 2026-07-01

This wave continued the file-by-file Hermes-first pass with
`agent/system_prompt.py`. It stayed in core runtime prompt construction and did
not touch dashboard, desktop, web static bundles, Synth/synth, or proprietary
Nous portal/product surfaces. UI/web/desktop remain in full-harness scope for
dedicated later lanes; only Synth and proprietary Nous product surfaces are
excluded.

Hermes/reference read before patching:

- `agent/system_prompt.py` full file, 536 LOC.

Patches landed:

- `aegis/agent/agent.py` runtime prompt now includes the active AEGIS runtime
  profile label and home path.
- The runtime prompt now warns not to modify another profile's skills, plugins,
  cron jobs, memories, or config unless the user explicitly directs it.
- The patch preserves AEGIS' existing stable/context/volatile tiers, stored
  nonvolatile prompt reuse, provider-wire volatile context, and prompt metadata
  fingerprints.

Focused verification:

```bash
pytest -q tests/test_smoke.py::test_agent_system_prompt_includes_runtime_auth tests/test_smoke.py::test_system_prompt_names_active_runtime_profile
# 2 passed

pytest -q tests/test_smoke.py -k "system_prompt or prompt_audit or provider_wire or skills_index"
# 8 passed, 24 deselected

ruff check aegis/agent/agent.py tests/test_smoke.py
python -m py_compile aegis/agent/agent.py tests/test_smoke.py
git diff --check -- aegis/agent/agent.py tests/test_smoke.py aegis/skill_preprocessing.py aegis/skills.py aegis/config.py tests/test_skill_preprocessing_parity.py tests/test_skills_memory.py
# clean
```

## Latest Skills Prompt/Catalog/Preprocessing Wave - 2026-07-01

This wave continued the file-by-file Hermes-first pass and avoided the live
Hermes session. Reference reads used the isolated mirror:
`/tmp/aegis-hermes-agent-reference`.

Hermes/reference read before patching:

- `agent/prompt_builder.py` skills prompt cache/index ranges `1250-1715`.
- `agent/skill_utils.py` discovery/exclusion/frontmatter ranges `1-260`.
- `agent/skill_preprocessing.py` full file, 144 LOC.
- `agent/system_prompt.py` skills prompt callsite range `260-330`.

Patches landed:

- `aegis/skills.py` now scans category-level `DESCRIPTION.md` files, includes
  them in the discovery signature, persists `category_descriptions` in the
  disk-backed skills prompt snapshot, and invalidates/re-renders after category
  summary edits.
- The rendered skills index now emits category summaries before visible skills,
  keeps focus-mode category demotion names-only, and preserves higher-precedence
  local category summaries over lower-precedence configured external dirs.
- A process-wide 8-entry rendered skills prompt LRU now mirrors the Hermes
  prompt-builder cache shape while keying on AEGIS discovery signatures,
  skill-policy filters, compact categories, runtime env/bin gates, and category
  metadata.
- Volta the 2nd added the initial failing category-summary/signature tests; the
  main agent added refresh and local-over-external precedence regressions.
- `aegis/skill_preprocessing.py` keeps AEGIS-native placeholders while accepting
  Hermes-style `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}`, retains
  unresolved placeholders for debugging, and preserves the default-off
  `skills.inline_shell` gate.
- `aegis/skills.py` and `aegis/agent/agent.py` now carry the active session id
  into activation, slash, and preload skill bodies so session placeholders have
  a concrete value.
- Confucius the 2nd provided the tests-only preprocessing sidecar; the main
  agent reconciled the final regression file and verified the integrated
  workspace.

Focused verification:

```bash
pytest -q tests/test_skills_memory.py -k "category_description or skill_index_refreshes or local_category"
# 4 passed, 35 deselected

pytest -q tests/test_skills_memory.py
# 39 passed

pytest -q tests/test_skill_manage_parity.py tests/test_tools.py -k skill
# 5 passed, 59 deselected

pytest -q tests/test_skill_preprocessing_parity.py
# 5 passed

pytest -q tests/test_skills_memory.py tests/test_skill_preprocessing_parity.py tests/test_skill_manage_parity.py tests/test_tools.py -k skill
# 49 passed, 59 deselected

python -m py_compile aegis/skills.py
ruff check aegis/skills.py tests/test_skills_memory.py
git diff --check -- aegis/skills.py tests/test_skills_memory.py
# clean

python -m py_compile aegis/skill_preprocessing.py aegis/skills.py aegis/agent/agent.py aegis/config.py tests/test_skill_preprocessing_parity.py
ruff check aegis/skill_preprocessing.py aegis/skills.py aegis/agent/agent.py aegis/config.py tests/test_skill_preprocessing_parity.py tests/test_skills_memory.py
git diff --check -- aegis/skill_preprocessing.py aegis/skills.py aegis/agent/agent.py aegis/config.py tests/test_skill_preprocessing_parity.py tests/test_skills_memory.py
# clean
```

Current file sizes for this lane:

| File | LOC |
| --- | ---: |
| `aegis/skills.py` | 1,116 |
| `aegis/skill_preprocessing.py` | 103 |
| `tests/test_skills_memory.py` | 1,075 |
| `tests/test_skill_preprocessing_parity.py` | 88 |
| Hermes `agent/prompt_builder.py` | 1,971 |
| Hermes `agent/skill_utils.py` | 767 |
| Hermes `agent/skill_preprocessing.py` | 144 |
| Hermes `agent/system_prompt.py` | 536 |

## Latest Stage Z Verification Wave - 2026-07-01

This wave started from the remaining Stage Z proof gaps and kept Synth/synth
and proprietary Nous product surfaces out of scope. It did not edit dashboard
app, desktop app, web static bundles, or release packaging in that specific
wave, but those surfaces remain in full-harness scope for dedicated lanes. It
used two worker subagents plus the main MCP lane.

Hermes/reference read before patching:

- Main MCP lane read `/home/alienai/.hermes/hermes-agent/tools/mcp_tool.py`
  targeted SDK/capability/sampling/elicitation/lifecycle ranges:
  `190-285`, `838-980`, `980-1220`, `1227-1418`, `1420-1625`.
- Archimedes read `hermes_state.py` `120-640`, `790-905`, `1048-1156`,
  `tests/test_hermes_state_wal_fallback.py` `1-385`,
  `tests/test_state_db_malformed_repair.py` `1-380`, and the doctor/session
  repair CLI ranges before patching session repair.
- Maxwell read `tools/environments/daytona.py` `1-270`,
  `tools/environments/modal.py` `1-478`, `tools/environments/file_sync.py`
  `1-403`, `tools/terminal_tool.py` `1360-1510`, `2760-2855`, plus Modal,
  Daytona, and file-sync tests before patching remote proof readiness.

Patches landed:

- `pyproject.toml` now exposes real optional SDK extras:
  `mcp>=1.28,<2`, `modal>=1.5,<2`, and `daytona>=0.192,<1`.
- `tests/test_stage_z_mcp_sdk_callbacks.py` now includes a real installed
  MCP SDK stdio E2E proof using `mcp.server.fastmcp.FastMCP`, `sdk=True`,
  tool call rendering, resources, and prompts. The proof ran locally after
  installing `mcp 1.28.1`.
- `aegis/session.py` now records WAL evidence with header/page/frame/commit
  accounting and attempts safe replay through SQLite on a scratch backup copy
  when the backed-up WAL has complete frames. It installs the checkpointed
  main DB only after health checks pass. It still does not hand-decode WAL
  frames; unsafe/no-complete-frame cases preserve sidecars and require manual
  restore/external recovery.
- `aegis/tools/backends.py` now exposes `remote_backend_live_proof()`, gated by
  explicit env/config flags. It refuses local fallback, reports SDK API-shape
  and credential evidence, sanitizes output, and does not start Modal/Daytona
  sandboxes unless the gate and credentials are present.
- Remote backend edge fixes include Modal `drain.aio()` support, remote stderr
  surfacing on tar failures, Daytona mkdir failure surfacing, and
  `upload_files()` to `upload_file()` fallback.
- Daytona resume/list handling now supports both Hermes-era
  `list(labels=..., limit=...)` and installed Daytona `0.192.0`
  `ListSandboxesQuery(labels=..., limit=...)` SDK shapes.
- Generated docs and public docs counts were refreshed after the tool registry
  grew to 128 tools with `tool_call` and `tool_describe`.
- Runtime comments/docstrings were scrubbed of upstream brand wording while
  preserving explicit reference naming in tests and this build ledger.

Focused verification:

```bash
python -m pytest -q tests/test_stage_z_mcp_sdk_callbacks.py tests/test_stage_z_mcp_lifecycle.py tests/test_stage_z_mcp_async_sampling.py tests/test_stage_z_mcp_sampling.py tests/test_stage_z_mcp_elicitation.py tests/test_stage_z_mcp_background_notifications.py tests/test_stage_z_mcp_client_capabilities.py
python -m pytest -q tests/test_stage_z_remote_environments.py
python -m pytest -q tests/test_stage_z_session_repair.py tests/test_session_locks.py
python -m pytest -q tests/test_stage_z_mcp_sdk_callbacks.py tests/test_stage_z_remote_environments.py tests/test_stage_z_session_repair.py tests/test_session_locks.py
python scripts/generate_reference_docs.py --check
python scripts/audit_reference_compare.py
python -m pytest -q
```

Results:

- MCP SDK focused slice: `36 passed`.
- Remote backend focused slice: `27 passed`.
- Session repair/lock focused slice: `15 passed`.
- Combined Stage Z proof slice: `42 passed`.
- Real installed SDK passive proof: Modal `1.5.1` and Daytona `0.192.0` report
  compatible API shapes; `remote_backend_live_proof(config=Config({}))` blocks
  without starting a sandbox because credentials/config are absent.
- Previously failing broad-suite drift was fixed: cost-safe live-tool count is
  now 32, SDK extras are native optional deps instead of empty aliases,
  generated docs are current, public docs count is 128 tools, TUI resume has a
  mocked-store fallback, and non-interactive setup tests use explicit
  non-interactive flags.
- Final full suite: `2153 passed, 3 skipped in 275.85s`.
- `ruff check`, `python -m py_compile`, and `git diff --check` are clean for
  touched core/test/docs files.
- Mechanical inventory: `1,026` AEGIS source-like files / `300,050` LOC vs
  `5,459` Hermes source-like files / `2,310,169` LOC, or `18.8%` by files and
  `13.0%` by LOC.

## Stage Z Remote Proof Readiness Patch - 2026-07-01

- Read Hermes remote references first: current `tools/environments/daytona.py`
  `1-270`, `tools/environments/modal.py` `1-478`,
  `tools/environments/file_sync.py` `1-403`, and `tools/terminal_tool.py`
  `1360-1510`, `2760-2855`.
- `aegis/tools/backends.py` now fails closed on incompatible credentialed
  Modal/Daytona SDK API shape before live lookup/client construction.
- Modal creation failures and Daytona client/sandbox construction failures now
  return backend errors instead of local fallback, with configured credential
  values redacted from diagnostics.
- Failed Modal/Daytona initial workspace sync after sandbox creation now
  triggers immediate sandbox cleanup and redacted live-proof failure evidence.
- Focused verification: remote backend file `33 passed`, focused redaction and
  failed-initial-sync cleanup tests passed, and ruff, py_compile, and diff whitespace checks are clean
  for `aegis/tools/backends.py` and `tests/test_stage_z_remote_environments.py`.

## Stage L Checkpoint Maintenance Patch - 2026-07-01

This patch followed the Stage L Hermes-first rule and stayed in the core
harness/CLI checkpoint surface. It did not touch Synth/synth or proprietary
Nous product surfaces; dashboard, desktop, web static bundles, and release
packaging were simply not part of that specific Stage L lane.

Hermes/reference read before patching:

- `/home/alienai/.hermes/hermes-agent/tools/checkpoint_manager.py` full file
  `1-1675`, covering shared shadow-git store layout, project metadata,
  checkpoint listing/diff/restore validation, pruning, auto-prune markers,
  status, clear helpers, and size/file-count guards.
- `/home/alienai/.hermes/hermes-agent/tools/checkpoint_manager.py` re-read at
  `145-195` and `794-849`, covering commit/path validation and single-file
  restore result shape.
- `/home/alienai/.hermes/hermes-agent/tools/checkpoint_manager.py` re-read at
  `220-520` and `840-1260`, covering isolated shared git store init, git env
  isolation, project refs/indexes, commit-tree snapshots, pruning, GC, and
  formatted single-file rollback UX.
- `/home/alienai/.hermes/hermes-agent/tools/checkpoint_manager.py` re-read at
  `540-620`, `612-660`, `851-930`, `1008-1220`, and `1570-1625`, covering
  project-root detection, file-count skipping, per-file size caps, total
  store-size pruning, status rows, and size reporting.
- `/home/alienai/.hermes/hermes-agent/tools/checkpoint_manager.py` re-read at
  `690-735`, `1208-1240`, and `1625-1675`, covering recent checkpoint log
  rows, formatted rollback list guidance, `clear_all()`, `clear_legacy()`,
  and legacy archive accounting.
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_checkpoint_manager.py`
  full file `1-1051`, covering shared store metadata, max snapshot pruning,
  tilde path normalization, restore/diff validation, malformed metadata,
  global git-config isolation, auto-prune, status, and clear helpers.
- `/home/alienai/.hermes/hermes-agent/tests/tools/test_checkpoint_manager.py`
  re-read at `640-708`, covering restore argument-injection, absolute-path,
  traversal, and valid file-path regressions.
- `/home/alienai/.hermes/hermes-agent/hermes_cli/checkpoints.py` `1-244`,
  covering checkpoint status/list/prune/clear CLI behavior.
- `/home/alienai/.hermes/hermes-agent/hermes_cli/checkpoints.py` re-read at
  `1-244`, covering status/list/prune/clear/clear-legacy CLI behavior,
  legacy archive reporting, and reclaimed-byte messages.

Patches landed:

- `aegis/checkpoints.py` now records `workdir` in checkpoint manifests and
  exposes Hermes-style operational helpers: `store_status()`,
  `prune_checkpoints()`, `maybe_auto_prune_checkpoints()`, and `clear_all()`.
  The existing per-turn manifest snapshot/rollback behavior is preserved.
- `aegis/checkpoints.py` now stores existing-file checkpoint bytes in a shared
  git object store under `~/.aegis/checkpoints/store/` when git is available,
  with per-checkpoint refs/indexes, git-store size reporting, and ref cleanup
  during stale/orphan/keep-limit pruning. If git is unavailable, it falls back
  to the older per-checkpoint shadow-copy path.
- `aegis/checkpoints.py` now also exposes structured `restore(..., file_path=...)`
  and keeps `rollback()` backward compatible while allowing one relative file
  to be restored or removed from a checkpoint. Absolute paths and `..`
  traversal outside the checkpoint workdir fail closed.
- `aegis/checkpoints.py` now resolves nested cwd snapshots to a project root
  using Hermes-style markers, while refusing to let broad temp/home parents
  such as `/tmp/.git` swallow unmarked scratch projects.
- `aegis/checkpoints.py` now accepts `max_snapshot_files`,
  `max_file_size_mb`, and `max_total_size_mb` policy knobs, skips oversized
  files, refuses huge trees, and prunes oldest checkpoints under a total-size
  cap while keeping the newest checkpoint for each project.
- `aegis/checkpoints.py` now exposes `history()` rows with full checkpoint id,
  short git commit prefix, timestamp, reason, file count, workdir, and live
  state so the CLI can render Hermes-style recent checkpoint history.
- `aegis/checkpoints.py` now reports legacy checkpoint archive count/size in
  `store_status()` and exposes `clear_legacy()` that deletes only `legacy-*`
  archive directories while preserving active checkpoint dirs and the git
  store.
- `aegis/cli/main.py` now supports `aegis checkpoints status`, richer
  `aegis checkpoints list`, `aegis checkpoints prune --older-than-days ...
  --keep ... --keep-orphans --max-size-mb ...`, `aegis checkpoints clear`, and
  `aegis checkpoints clear-legacy` so checkpoint maintenance can run without
  the agent process. `aegis checkpoints rollback <id> --file <relative-path>`
  now restores only that checkpointed file and prints checkpoint/workdir/file
  evidence.
- `tests/test_checkpoint_depth.py` now covers checkpoint project metadata,
  status rows, orphan/stale/over-limit pruning, auto-prune marker skipping,
  CLI status/prune output, single-file restore, single-file new-file removal,
  path traversal rejection, CLI single-file rollback, shared git blob dedupe,
  and git-ref cleanup during manual and automatic pruning.
- `tests/test_checkpoint_policy_stage_l.py` now covers nested project-root
  manifests, max snapshot file-count guard, per-file size-cap skip, and
  total-size pruning while retaining the newest project checkpoint, plus
  recent-first history metadata.
- `tests/test_checkpoint_cli_stage_l.py` now covers checkpoint status/list
  workdir/git metadata, prune settings/counts including `--max-size-mb`,
  legacy archive status, `clear-legacy` preservation, clear reclaimed-size
  output, commit/history metadata, and rollback-file evidence.

Focused verification:

```bash
python -m pytest -q tests/test_tool_executor_stage_j.py tests/test_agent_perms.py::test_checkpoint_snapshot_rollback tests/test_checkpoint_policy_stage_l.py tests/test_checkpoint_cli_stage_l.py tests/test_checkpoint_depth.py tests/test_smoke.py::test_checkpoint_rollback
python -m ruff check aegis/checkpoints.py aegis/cli/main.py tests/test_checkpoint_policy_stage_l.py tests/test_checkpoint_cli_stage_l.py tests/test_checkpoint_depth.py
python -m py_compile aegis/checkpoints.py aegis/cli/main.py tests/test_checkpoint_policy_stage_l.py tests/test_checkpoint_cli_stage_l.py tests/test_checkpoint_depth.py
git diff --check -- aegis/checkpoints.py aegis/cli/main.py tests/test_checkpoint_policy_stage_l.py tests/test_checkpoint_cli_stage_l.py tests/test_checkpoint_depth.py
python scripts/audit_reference_compare.py
```

Results:

- Checkpoint focused/integration slice: `34 passed`.
- Ruff, py_compile, and diff whitespace checks are clean for the touched
  checkpoint files.
- Mechanical inventory regenerated: `1,026` AEGIS source-like files /
  `300,050` LOC vs `5,459` Hermes source-like files / `2,310,169` LOC, or
  `18.8%` by files and `13.0%` by LOC.
- Remaining Stage L depth not yet ported: broader file-operation policy only
  if later Hermes re-reads find gaps; checkpoint lower-depth gaps are now
  mostly migration/edge-policy polish.

## Stage L File Operations Parity Patch - 2026-07-01

This patch followed the Hermes-first Stage L rule and stayed in the core file
tool surface. It did not touch Synth/synth or proprietary Nous product
surfaces; dashboard, desktop, web static bundles, and release packaging were
simply not part of that specific Stage L lane.

Hermes/reference read before patching:

- `/home/alienai/.hermes/hermes-agent/tools/file_operations.py` `1-520`,
  `521-1040`, `1041-1580`, `1581-2110`, and `2111-2440`, covering result
  metadata classes, compact line-number rendering, binary detection, read/raw
  read behavior, atomic writes, line-ending/BOM preservation, post-write
  verification, lint/LSP delta shaping, and search pagination/output modes.
- `/home/alienai/.hermes/hermes-agent/tools/file_tools.py` `1-420`,
  `421-960`, `961-1500`, and `1501-1945`, covering path resolution,
  blocked-device reads, read dedupe/stale tracking, internal file-tool display
  write rejection, write/patch locking, search loop detection, schemas, and
  handler argument compatibility.
- `/home/alienai/.hermes/hermes-agent/tools/binary_extensions.py` `1-42`,
  covering pure extension-based binary read refusal.
- Subagent McClintock the 2nd independently re-read
  `file_operations.py` `155-310`, `698-722`, `846-1138`, `1193-1565`,
  `1962-2176`, `2179-2423`, and `file_tools.py` `340-380`, `984-1245`,
  `1285-1390`, `1416-1668`, `1673-1737` before adding focused tests.

Patches landed:

- `aegis/tools/builtin.py` now emits compact `LINE|CONTENT` read gutters,
  matching the Hermes token-saving format while preserving offset/limit paging.
- `aegis/tools/builtin.py` now refuses text reads for known binary extensions
  such as archives and media before UTF-8 decoding, in addition to existing
  NUL-byte and device/proc read guards.
- `aegis/tools/builtin.py` now refuses `write_file` payloads dominated by
  `read_file` display gutters so the agent cannot accidentally persist
  line-numbered tool output as source bytes.
- `aegis/tools/builtin.py` now verifies local write/edit persistence by
  re-reading the file after the atomic swap and failing closed when the
  on-disk content does not match the intended write after BOM/line-ending
  normalization.
- `aegis/tools/builtin.py` search now accepts Hermes-style `search_files`
  arguments: `target=files`, `target=content`, `file_glob`, `limit`, `offset`,
  `output_mode=files_only`, `output_mode=count`, `context`, and structured
  JSON metadata with `truncated` and `next_offset`.
- `tests/test_file_operations_stage_l.py` now covers compact read windows,
  search pagination metadata, device alias and binary extension blocking,
  post-write verification failure detection, and partial-read stale warning
  refresh.
- `tests/test_tools.py` now covers read-display write rejection and
  Hermes-style search pagination/count/file-target behavior through the
  existing AEGIS tool path.

Focused verification:

```bash
python -m pytest -q tests/test_file_operations_stage_l.py tests/test_tools.py tests/test_file_state_stage_l.py tests/test_tool_executor_stage_j.py
python -m ruff check aegis/tools/builtin.py tests/test_file_operations_stage_l.py tests/test_tools.py tests/test_file_state_stage_l.py tests/test_tool_executor_stage_j.py
python -m py_compile aegis/tools/builtin.py tests/test_file_operations_stage_l.py tests/test_tools.py tests/test_file_state_stage_l.py tests/test_tool_executor_stage_j.py
git diff --check -- aegis/tools/builtin.py tests/test_tools.py tests/test_file_operations_stage_l.py BUILD_STATUS.md
python scripts/audit_reference_compare.py
```

Results:

- File-operation focused slice: `68 passed`.
- Ruff, py_compile, and diff whitespace checks are clean for the touched
  file-operation files.
- Mechanical inventory regenerated: `1,026` AEGIS source-like files /
  `300,050` LOC vs `5,459` Hermes source-like files / `2,310,169` LOC, or
  `18.8%` by files and `13.0%` by LOC.

Current remaining blockers after this wave:

1. Credentialed live Modal/Daytona proof is still not executed in this
   environment. With `modal 1.5.1` and `daytona 0.192.0` installed,
   `remote_backend_live_proof(config=Config({}))` reports both backends
   `blocked`, `live_sandbox_started=false`, with credential/config evidence as
   the only blocker. The installable extras and gated proof harness now exist;
   a paid/live sandbox run must be deliberately enabled with real credentials.
2. Full Hermes reading coverage is still incomplete. The coverage table above
   remains the authoritative read log.

Resolved non-blocker from the session repair lane:

- Manual WAL-frame decoding is not a Hermes parity gap. Re-reading
  `hermes_state.py` WAL/checkpoint/repair sections plus
  `tests/test_hermes_state_wal_fallback.py` and
  `tests/test_state_db_malformed_repair.py` found no hand decoder: Hermes uses
  SQLite journal/WAL pragmas, SQLite checkpoints, raw backups,
  `writable_schema` repair, and FTS rebuild/drop. AEGIS matches that policy
  and adds safe SQLite checkpoint replay on a scratch backup copy when complete
  WAL frames exist; otherwise it preserves sidecars and fails closed.

## Immediate Next Actions

1. Continue Stage Z from the reduced blocker set: credentialed live
   Modal/Daytona integration proof and full Hermes reading coverage. Read the
   Hermes files/tests named in the Stage Z audit first, then patch AEGIS with
   focused tests.
2. Start the next patch wave from credentialed live remote sandbox proof,
   because borrowed-token resync/removal suppression, malformed
   SQLite/FTS/WAL repair backups and sidecar recovery, multi-pass compaction
   planning, persisted-output rehydration, Daytona/Modal fake-SDK sync plus
   Modal snapshot restore, MCP sampling/server requests and roots/notification/
   completion helpers, optional MCP SDK fake-SDK lifecycle/callback parity plus
   real installed SDK stdio proof, source suppression/leases, centralized source-removal registry,
   provider-specific singleton/source removal, rate-limit bucket account
   breakers, backend SSH/Singularity interrupts, MCP lifecycle/SSE stale-reader
   suppression, noninteractive OAuth startup, skills prompt disk snapshots/
   category demotion/category `DESCRIPTION.md` summaries/rendered LRU,
   tool-result metadata persistence, malformed-repair status
   metadata, cross-process repair locks, manual repair-backup restore, safe
   SQLite WAL checkpoint replay, remote backend diagnostics/live-proof
   readiness, and archival compaction now have green focused coverage.
3. Preserve the Stage V/W provider/auth/observability contracts during fixes;
   do not let audit fixes mutate Claude/Anthropic thinking/cache/OAuth behavior.
4. Keep Synth/synth and proprietary Nous product surfaces out of scope. Keep
   dashboard, desktop, web/static, TUI, and packaging in the full-harness map
   and patch them only when their dedicated lane is active.
5. Keep `BUILD_STATUS.md` current after every verified Stage Z finding/fix and
   every major Hermes file coverage update.

## Stage T/W Subagent Usage Activity Note

- Landed focused Hermes-parity patches for synchronous and background subagent
  completion activity plus default dispatch: `subagent_done` events, async
  delegation events, subagent registry rows, and background task list rows now
  carry child token/cache totals plus cost evidence from the child turn, and
  real top-level parent tool contexts now default to background delegation.
- Focused checks passed: `tests/test_subagent_stage_t.py`,
  `tests/test_subagent_background_stage_t.py`, `tests/test_stage_w_usage_cost.py`,
  and py_compile for the touched Stage T files.
- Integrated checkpoint/remote/subagent verification passed:
  `python -m pytest -q tests/test_stage_z_remote_environments.py tests/test_checkpoint_depth.py tests/test_smoke.py::test_checkpoint_rollback tests/test_subagent_stage_t.py tests/test_subagent_background_stage_t.py tests/test_stage_w_usage_cost.py`
  -> `68 passed` with Daytona SDK deprecation warnings only. Ruff,
  py_compile, and `git diff --check` are clean for the touched remote,
  checkpoint, subagent, and status files.
