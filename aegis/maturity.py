"""Full-agent maturity matrix and live QA accounting for AEGIS.

This module is intentionally AEGIS-native: it records local architecture layers,
proofs, docs, and external validation targets without claiming that a live third-
party integration is ready unless a credentialed/manual smoke can prove it.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ArchitectureLayer:
    id: str
    title: str
    purpose: str
    source_paths: tuple[str, ...]
    doc: str
    local_proofs: tuple[str, ...]
    live_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveQATarget:
    id: str
    surface: str
    kind: str
    local_proof: str
    live_proof_command: str
    credential_envs: tuple[str, ...] = ()
    manual_runner: str = ""


ARCHITECTURE_LAYERS: tuple[ArchitectureLayer, ...] = (
    ArchitectureLayer(
        "runtime_loop",
        "Runtime loop",
        "Bounded conversation execution, provider calls, tool dispatch, continuation, compression, and role hygiene.",
        ("aegis/agent/agent.py", "aegis/agent/loop.py", "aegis/agent/governance.py"),
        "docs/maturity.md",
        ("tests/test_agentic_upgrades.py", "tests/test_ultracode_loop.py", "bash scripts/run_tests.sh"),
    ),
    ArchitectureLayer(
        "prompt_context",
        "Prompt context",
        "Stable/context/volatile prompt assembly, project references, context limits, scanner boundaries, and compression metadata.",
        ("aegis/agent/context.py", "aegis/context_refs.py", "aegis/agent/compaction.py"),
        "docs/maturity.md",
        ("tests/test_context_command.py", "tests/test_coding_context.py", "tests/test_compaction_boundaries.py"),
    ),
    ArchitectureLayer(
        "tool_registry",
        "Tool registry",
        "Tool schema registry, toolsets, permission metadata, dynamic integration tools, and portable schema normalization.",
        ("aegis/tools/registry.py", "aegis/tools/builtin.py", "aegis/tools/schema_validation.py"),
        "docs/maturity.md",
        ("tests/test_tools.py", "tests/test_generated_reference_docs.py", "docs/tools-reference.md"),
    ),
    ArchitectureLayer(
        "terminal_processes",
        "Terminal processes",
        "Foreground commands, background processes, process registry actions, PTY-adjacent execution, and backend isolation.",
        ("aegis/tools/process.py", "aegis/tools/process_registry.py", "aegis/tools/backends.py"),
        "docs/maturity.md",
        ("tests/test_tools.py", "tests/test_phases.py", "tests/test_agent_perms.py"),
    ),
    ArchitectureLayer(
        "memory_layers",
        "Memory layers",
        "Separate durable user facts, operator notes, session recall, external memory providers, and procedural skills.",
        ("aegis/memory.py", "aegis/memory_providers.py", "aegis/tools/recall.py"),
        "docs/maturity.md",
        ("tests/test_memory_provider_surfaces.py", "tests/test_memory_provider_cli.py", "docs/memory-skills.md"),
    ),
    ArchitectureLayer(
        "sessions_history",
        "Sessions and history",
        "SQLite-backed sessions, search, lineage, run linkage, trace metadata, export, and crash recovery UX.",
        ("aegis/session.py", "aegis/session_checks.py", "aegis/runs.py"),
        "docs/maturity.md",
        ("tests/test_session_checks.py", "tests/test_sessions_cli.py", "tests/test_memory_wiring.py"),
    ),
    ArchitectureLayer(
        "skills_curator",
        "Skills lifecycle",
        "SKILL.md packages, skill management, usage tracking, curation, archive/pin transitions, and self-learning candidates.",
        ("aegis/skills.py", "aegis/curator.py", "aegis/tools/skill_manage.py"),
        "docs/maturity.md",
        ("tests/test_skills_parity_cli.py", "tests/test_compaction_curator.py", "tests/test_overhaul_wave1.py"),
    ),
    ArchitectureLayer(
        "gateway_messaging",
        "Gateway messaging",
        "Long-running multi-channel gateway, pairing, allowlists, session isolation, attachments, outbox, and fake/live adapter split.",
        ("aegis/gateway/runner.py", "aegis/gateway/base.py", "aegis/platforms/helpers.py"),
        "docs/maturity.md",
        ("tests/test_gateway_adapter_contract.py", "tests/test_gateway_cli.py", "docs/gateway.md"),
        ("credentialed platform smoke per channel",),
    ),
    ArchitectureLayer(
        "cron_background",
        "Cron semantics",
        "Durable scheduled jobs, scripts, delivery sinks, background jobs, dry-runs, previews, and dashboard controls.",
        ("aegis/cron.py", "aegis/tools/cronjob_tool.py", "aegis/dashboard_routes/cron_jobs.py"),
        "docs/maturity.md",
        ("tests/test_cronjob_tool.py", "tests/test_cron_cli.py", "tests/test_dashboard_fastapi.py"),
    ),
    ArchitectureLayer(
        "delegation_subagents",
        "Delegation",
        "Isolated subagent spawning, background summaries, parent verification, kanban workers, and task orchestration.",
        ("aegis/tools/agentic.py", "aegis/background.py", "aegis/kanban_auto.py"),
        "docs/maturity.md",
        ("tests/test_agentic_upgrades.py", "tests/test_typed_subagents.py", "tests/test_kanban_kernel.py"),
    ),
    ArchitectureLayer(
        "providers_auth",
        "Provider routing",
        "Provider registry, OAuth/API-key auth, credential pools, fallback routing, auxiliary routing, and redacted status.",
        ("aegis/providers/registry.py", "aegis/providers/auth.py", "aegis/providers/fallback.py"),
        "docs/maturity.md",
        ("tests/test_providers.py", "tests/test_auth_cli.py", "docs/providers.md"),
        ("credentialed provider smoke",),
    ),
    ArchitectureLayer(
        "cli_tui",
        "CLI and TUI",
        "Argparse command tree, slash registry, classic terminal, Ink TUI, generated docs, and surface command aliases.",
        ("aegis/cli/main.py", "aegis/cli/repl.py", "aegis/cli/tui.py"),
        "docs/maturity.md",
        ("tests/test_generated_reference_docs.py", "tests/test_tui_ink.py", "docs/cli-reference.md"),
    ),
    ArchitectureLayer(
        "dashboard_desktop",
        "Desktop dashboard",
        "FastAPI dashboard, React/Vite UI, Electron shell, backend readiness, update status, and packaged release checks.",
        ("aegis/dashboard_fastapi.py", "web/src/App.tsx", "desktop/electron/main.js"),
        "docs/maturity.md",
        ("tests/test_dashboard_fastapi.py", "desktop", "web"),
        ("cross-OS desktop installer smoke",),
    ),
    ArchitectureLayer(
        "security_privacy",
        "Security approvals",
        "Command approvals, redaction, dashboard token minimization, WebSocket tickets, file safety, and policy simulation.",
        ("aegis/tools/permissions.py", "aegis/redact.py", "aegis/tools/file_safety.py"),
        "docs/maturity.md",
        ("tests/test_smoke.py", "tests/test_dashboard_web_token_safety.py", "docs/security.md"),
    ),
    ArchitectureLayer(
        "extensibility",
        "Extension ladder",
        "Native extension points for plugins, MCP, webhooks, skills, tool gating, dashboards, and generated references.",
        ("aegis/plugins.py", "aegis/dashboard_routes/tools_mcp.py", "aegis/webhook.py"),
        "docs/maturity.md",
        ("tests/test_plugins_update_cli.py", "tests/test_mcp_cli.py", "tests/test_webhook_cli.py"),
    ),
)

ARCHITECTURE_LAYER_IDS: tuple[str, ...] = tuple(row.id for row in ARCHITECTURE_LAYERS)


LIVE_QA_TARGETS: tuple[LiveQATarget, ...] = (
    LiveQATarget("api_server", "gateway", "local_api", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=api_server AEGIS_LIVE_API_SERVER=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("API_SERVER_KEY",)),
    LiveQATarget("webhook", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=webhook AEGIS_LIVE_WEBHOOK=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("WEBHOOK_SECRET", "WEBHOOK_URL")),
    LiveQATarget("telegram", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=telegram AEGIS_LIVE_TELEGRAM=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
    LiveQATarget("discord", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=discord AEGIS_LIVE_DISCORD=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID")),
    LiveQATarget("slack", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=slack AEGIS_LIVE_SLACK=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")),
    LiveQATarget("matrix", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=matrix AEGIS_LIVE_MATRIX=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD")),
    LiveQATarget("signal", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=signal AEGIS_LIVE_SIGNAL=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("SIGNAL_CLI_ACCOUNT",)),
    LiveQATarget("email", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=email AEGIS_LIVE_EMAIL=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("EMAIL_IMAP_HOST", "EMAIL_SMTP_HOST", "EMAIL_ADDRESS", "EMAIL_PASSWORD")),
    LiveQATarget("sms", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=sms AEGIS_LIVE_SMS=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")),
    LiveQATarget("whatsapp", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=whatsapp AEGIS_LIVE_WHATSAPP=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("WHATSAPP_BRIDGE_URL",)),
    LiveQATarget("whatsapp_cloud", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=whatsapp_cloud AEGIS_LIVE_WHATSAPP_CLOUD=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("WHATSAPP_CLOUD_TOKEN", "WHATSAPP_CLOUD_PHONE_ID")),
    LiveQATarget("ntfy", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=ntfy AEGIS_LIVE_NTFY=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("NTFY_TOPIC",)),
    LiveQATarget("mattermost", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=mattermost AEGIS_LIVE_MATTERMOST=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("MATTERMOST_URL", "MATTERMOST_BOT_TOKEN")),
    LiveQATarget("homeassistant", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=homeassistant AEGIS_LIVE_HOMEASSISTANT=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("HOMEASSISTANT_CHANNEL_OUTBOUND_URL",)),
    LiveQATarget("dingtalk", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=dingtalk AEGIS_LIVE_DINGTALK=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("DINGTALK_CLIENT_ID", "DINGTALK_TOKEN")),
    LiveQATarget("feishu", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=feishu AEGIS_LIVE_FEISHU=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("FEISHU_APP_ID", "FEISHU_APP_TOKEN")),
    LiveQATarget("wecom", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=wecom AEGIS_LIVE_WECOM=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("WECOM_CORP_ID", "WECOM_AGENT_ID")),
    LiveQATarget("weixin", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=weixin AEGIS_LIVE_WEIXIN=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("WEIXIN_APP_ID", "WEIXIN_TOKEN")),
    LiveQATarget("bluebubbles", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=bluebubbles AEGIS_LIVE_BLUEBUBBLES=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("BLUEBUBBLES_CHANNEL_OUTBOUND_URL",)),
    LiveQATarget("qqbot", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=qqbot AEGIS_LIVE_QQBOT=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("QQBOT_APP_ID", "QQBOT_TOKEN")),
    LiveQATarget("yuanbao", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=yuanbao AEGIS_LIVE_YUANBAO=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("YUANBAO_SESSION",)),
    LiveQATarget("relay", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=relay AEGIS_LIVE_RELAY=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("RELAY_CHANNEL_OUTBOUND_URL",)),
    LiveQATarget("msgraph_webhook", "gateway", "messaging", "tests/test_gateway_adapter_contract.py", "AEGIS_LIVE_TARGET=msgraph_webhook AEGIS_LIVE_MSGRAPH_WEBHOOK=1 bash scripts/run_tests.sh tests/live/test_gateway_smoke.py", ("MSGRAPH_WEBHOOK_CHANNEL_OUTBOUND_URL",)),
    LiveQATarget("openai_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=openai AEGIS_LIVE_OPENAI=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("OPENAI_API_KEY",)),
    LiveQATarget("anthropic_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=anthropic AEGIS_LIVE_ANTHROPIC=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("ANTHROPIC_API_KEY",)),
    LiveQATarget("google_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=google AEGIS_LIVE_GOOGLE=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    LiveQATarget("openrouter_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=openrouter AEGIS_LIVE_OPENROUTER=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("OPENROUTER_API_KEY",)),
    LiveQATarget("groq_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=groq AEGIS_LIVE_GROQ=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("GROQ_API_KEY",)),
    LiveQATarget("deepseek_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=deepseek AEGIS_LIVE_DEEPSEEK=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("DEEPSEEK_API_KEY",)),
    LiveQATarget("xai_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=xai AEGIS_LIVE_XAI=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("XAI_API_KEY",)),
    LiveQATarget("mistral_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=mistral AEGIS_LIVE_MISTRAL=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("MISTRAL_API_KEY",)),
    LiveQATarget("together_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=together AEGIS_LIVE_TOGETHER=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("TOGETHER_API_KEY",)),
    LiveQATarget("huggingface_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=huggingface AEGIS_LIVE_HUGGINGFACE=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("HF_TOKEN", "HUGGINGFACE_API_KEY")),
    LiveQATarget("novita_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=novita AEGIS_LIVE_NOVITA=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("NOVITA_API_KEY",)),
    LiveQATarget("qwen_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=qwen AEGIS_LIVE_QWEN=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("QWEN_API_KEY", "DASHSCOPE_API_KEY")),
    LiveQATarget("nvidia_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=nvidia AEGIS_LIVE_NVIDIA=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("NVIDIA_API_KEY",)),
    LiveQATarget("dashscope_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=dashscope AEGIS_LIVE_DASHSCOPE=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("DASHSCOPE_API_KEY",)),
    LiveQATarget("cerebras_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=cerebras AEGIS_LIVE_CEREBRAS=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("CEREBRAS_API_KEY",)),
    LiveQATarget("perplexity_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=perplexity AEGIS_LIVE_PERPLEXITY=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("PERPLEXITY_API_KEY",)),
    LiveQATarget("fireworks_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=fireworks AEGIS_LIVE_FIREWORKS=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("FIREWORKS_API_KEY",)),
    LiveQATarget("sambanova_provider", "provider", "model", "tests/test_providers.py", "AEGIS_LIVE_PROVIDER=sambanova AEGIS_LIVE_SAMBANOVA=1 bash scripts/run_tests.sh tests/live/test_provider_smoke.py", ("SAMBANOVA_API_KEY",)),
    LiveQATarget("desktop_linux", "desktop", "installer", "desktop", "AEGIS_LIVE_DESKTOP_LINUX=1 bash scripts/run_tests.sh tests/live/test_desktop_linux.py", manual_runner="Linux desktop runner"),
    LiveQATarget("desktop_windows", "desktop", "installer", "desktop", "AEGIS_LIVE_DESKTOP_WINDOWS=1 pwsh -File tests/live/test_desktop_windows.ps1", manual_runner="Windows desktop runner"),
    LiveQATarget("desktop_macos", "desktop", "installer", "desktop", "AEGIS_LIVE_DESKTOP_MACOS=1 bash tests/live/test_desktop_macos.sh", manual_runner="macOS notarized desktop runner"),
    LiveQATarget("docker_install", "install", "container", "scripts/verify_all.sh", "AEGIS_LIVE_DOCKER=1 bash tests/live/test_docker_install.sh", manual_runner="Docker runner"),
)

LIVE_QA_TARGET_IDS: tuple[str, ...] = tuple(row.id for row in LIVE_QA_TARGETS)


REMAINING_GAP_BUCKETS: tuple[dict[str, str], ...] = (
    {"id": "public_docs_i18n", "status": "covered-by-docs-site", "next_proof": "mkdocs.yml + site-next + docs/i18n/"},
    {"id": "public_docs", "status": "covered-by-docs", "next_proof": "docs/maturity.md + docs/user-guide/"},
    {"id": "user_guides", "status": "covered-by-docs", "next_proof": "docs/user-guide/*.md"},
    {"id": "plugin_integration_docs", "status": "covered-by-docs", "next_proof": "docs/operations-contracts.md"},
    {"id": "developer_guides", "status": "covered-by-docs", "next_proof": "docs/developer-guide/*.md"},
    {"id": "operations_contracts", "status": "covered-by-docs", "next_proof": "docs/operations-contracts.md"},
    {"id": "external_live_qa", "status": "requires-credentials", "next_proof": "docs/live-qa-matrix.md"},
    {"id": "file_family_depth", "status": "tracked-by-maturity-matrix", "next_proof": "aegis maturity --check"},
)


def _exists(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def _command_repo_paths(command: str) -> list[str]:
    paths: list[str] = []
    for token in shlex.split(command):
        if token.startswith(("tests/", "scripts/", "docs/", "aegis/", "desktop/", "web/", "site-next/")):
            paths.append(token)
    return paths


def _proof_exists(root: Path, proof: str) -> bool:
    proof = str(proof or "").strip()
    if not proof:
        return False
    paths = _command_repo_paths(proof)
    if paths:
        return all(_exists(root, path) for path in paths)
    if proof.startswith(("tests/", "scripts/", "docs/", "aegis/", "desktop", "web", "site-next/")):
        return _exists(root, proof)
    return True


def _command_path_status(root: Path, command: str) -> dict[str, bool]:
    return {path: _exists(root, path) for path in _command_repo_paths(command)}


def _count_markdown(root: Path, rel: str) -> int:
    base = root / rel
    if not base.exists():
        return 0
    if base.is_file():
        return 1 if base.suffix.lower() == ".md" else 0
    return sum(1 for path in base.rglob("*.md") if path.is_file())


def _count_locale_dirs(root: Path) -> int:
    base = root / "docs" / "i18n"
    if not base.exists():
        return 0
    return sum(1 for path in base.iterdir() if path.is_dir() and (path / "index.md").is_file())


def _layer_row(layer: ArchitectureLayer, root: Path) -> dict[str, Any]:
    source_exists = {path: _exists(root, path) for path in layer.source_paths}
    doc_exists = _exists(root, layer.doc)
    local_proof_exists = {proof: _proof_exists(root, proof) for proof in layer.local_proofs}
    local_ready = all(source_exists.values()) and doc_exists and bool(local_proof_exists) and all(local_proof_exists.values())
    row = asdict(layer)
    row.update(
        {
            "source_paths": list(layer.source_paths),
            "local_proofs": list(layer.local_proofs),
            "live_requirements": list(layer.live_requirements),
            "source_exists": source_exists,
            "doc_exists": doc_exists,
            "local_proof_exists": local_proof_exists,
            "status": "local-ready" if local_ready else "needs-local-proof",
        }
    )
    return row


def _live_row(target: LiveQATarget, root: Path) -> dict[str, Any]:
    local_exists = _exists(root, target.local_proof)
    live_command_paths_exist = _command_path_status(root, target.live_proof_command)
    live_command_paths_ready = bool(live_command_paths_exist) and all(live_command_paths_exist.values())
    if target.manual_runner:
        status = "manual-os-runner"
    elif target.credential_envs:
        status = "requires-credentials"
    else:
        status = "mocked-local" if local_exists else "requires-credentials"
    row = asdict(target)
    row.update(
        {
            "credential_envs": list(target.credential_envs),
            "local_proof_exists": local_exists,
            "live_command_paths_exist": live_command_paths_exist,
            "live_command_paths_ready": live_command_paths_ready,
            "status": status,
            "claims_live_ready": False,
        }
    )
    return row


def build_maturity_report(root: str | Path | None = None) -> dict[str, Any]:
    repo = Path(root) if root is not None else REPO_ROOT
    layers = [_layer_row(layer, repo) for layer in ARCHITECTURE_LAYERS]
    live = [_live_row(target, repo) for target in LIVE_QA_TARGETS]
    local_ready = sum(1 for row in layers if row["status"] == "local-ready")
    claimed_live = sum(1 for row in live if row["claims_live_ready"])
    live_command_ready = sum(1 for row in live if row["live_command_paths_ready"])
    requires_credentials = sum(1 for row in live if row["status"] == "requires-credentials")
    manual_os = sum(1 for row in live if row["status"] == "manual-os-runner")
    public_docs_pages = _count_markdown(repo, "docs")
    i18n_locales = _count_locale_dirs(repo)
    developer_guides = _count_markdown(repo, "docs/developer-guide")
    user_guides = _count_markdown(repo, "docs/user-guide")
    ok = local_ready == len(layers) and claimed_live == 0
    return {
        "object": "aegis.maturity.report",
        "ok": ok,
        "repo_root": str(repo),
        "summary": {
            "architecture_layers": len(layers),
            "local_ready_layers": local_ready,
            "live_targets": len(live),
            "live_claimed_ready": claimed_live,
            "live_command_path_ready_targets": live_command_ready,
            "requires_credentials": requires_credentials,
            "manual_os_runners": manual_os,
            "gap_buckets": len(REMAINING_GAP_BUCKETS),
            "public_docs_pages": public_docs_pages,
            "i18n_locales": i18n_locales,
            "developer_guides": developer_guides,
            "user_guides": user_guides,
        },
        "architecture_layers": layers,
        "live_qa_matrix": live,
        "remaining_gap_buckets": list(REMAINING_GAP_BUCKETS),
        "notes": [
            "Local-ready means source paths, docs, and automated proofs exist in this checkout.",
            "External live targets are not counted as ready until a credentialed or OS-runner smoke records evidence.",
            "The model-visible surface ledger is separate from this maturity matrix.",
        ],
    }


def build_live_qa_matrix(root: str | Path | None = None) -> dict[str, Any]:
    repo = Path(root) if root is not None else REPO_ROOT
    targets = [_live_row(target, repo) for target in LIVE_QA_TARGETS]
    return {
        "object": "aegis.live_qa.matrix",
        "ok": True,
        "total": len(targets),
        "claimed_ready": sum(1 for row in targets if row["claims_live_ready"]),
        "live_command_path_ready_targets": sum(1 for row in targets if row["live_command_paths_ready"]),
        "requires_credentials": sum(1 for row in targets if row["status"] == "requires-credentials"),
        "manual_os_runners": sum(1 for row in targets if row["status"] == "manual-os-runner"),
        "targets": targets,
    }


def render_maturity_markdown(report: dict[str, Any] | None = None) -> str:
    payload = report or build_maturity_report()
    lines = [
        "# AEGIS maturity report",
        "",
        "This report is generated from the AEGIS-native maturity matrix.",
        "It records local proof and live-QA requirements without overclaiming external platform readiness.",
        "",
        "## Summary",
        "",
        f"- Architecture layers: {payload['summary']['local_ready_layers']}/{payload['summary']['architecture_layers']} local-ready",
        f"- Live QA targets: {payload['summary']['live_targets']}",
        f"- Credentialed targets still requiring external proof: {payload['summary']['requires_credentials']}",
        f"- Manual OS runners still requiring proof: {payload['summary']['manual_os_runners']}",
        f"- Live targets claimed ready without proof: {payload['summary']['live_claimed_ready']}",
        f"- Public docs pages: {payload['summary'].get('public_docs_pages', 0)}",
        f"- User-guide pages: {payload['summary'].get('user_guides', 0)}",
        f"- Developer-guide pages: {payload['summary'].get('developer_guides', 0)}",
        f"- Localized snapshot locales: {payload['summary'].get('i18n_locales', 0)}",
        "",
        "## Architecture layers",
        "",
        "| Layer | Status | Doc | Local proofs |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["architecture_layers"]:
        lines.append(
            f"| {row['title']} | {row['status']} | `{row['doc']}` | "
            f"{', '.join('`' + proof + '`' for proof in row['local_proofs'])} |"
        )
    lines.extend([
        "",
        "## Live QA targets",
        "",
        "| Target | Surface | Status | Required proof |",
        "| --- | --- | --- | --- |",
    ])
    for row in payload["live_qa_matrix"]:
        lines.append(
            f"| {row['id']} | {row['surface']} | {row['status']} | `{row['live_proof_command']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def write_maturity_markdown(path: str | Path, root: str | Path | None = None) -> Path:
    target = Path(path)
    report = build_maturity_report(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_maturity_markdown(report), encoding="utf-8")
    return target


def dumps_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
