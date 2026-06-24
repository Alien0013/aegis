# Security

AEGIS can read/write files, run shell commands, browse the web, and connect to messaging
platforms. Treat it like any tool with system access.

## Defenses

- **Permission cascade** — `hardline blocklist → deny_groups → exec_mode → allowlist →
  approval`. Read-only tools are always allowed.
- **Hardline blocklist** — `rm -rf /`, fork bombs, `curl|bash`, disk wipes are refused
  even in `--yolo`/`full`.
- **Pre-execution scanning** — `security.scan_enabled` + `aegis security audit`
  (deps/MCP/plugins/skills) with Markdown or JSON output.
- **Fail-closed sandboxing** — docker/ssh/singularity/modal backends refuse to run on the
  host when unavailable unless `tools.allow_local_fallback: true`.
- **Secret hygiene** — keys in `~/.aegis/.env` (0600), OAuth tokens in `auth.json` (0600);
  `execute_code` strips secrets from the child env; learn candidates + debug bundles are
  redacted.
- **Gateway** — DM pairing, mention gating, per-user allowlists.

## Audit command

Run the audit before releases or after installing plugins/MCP servers:

```bash
aegis security audit --markdown
aegis security audit --json
```

The JSON form is machine-readable for CI. Both forms redact secret-like values
from config, plugin, MCP, skill, and dependency findings. `--fail-on any` exits
non-zero when a finding is present.

## Policy simulator

The dashboard Security page calls `POST /api/security/policy-simulate` to dry-run
file, shell, network, and tool decisions without executing the action. It reports
the decision, contributing checks, profile/workspace boundary status, network
safety, command-scan reasons, and redacted input values.

## Recommended posture

```yaml
tools:
  exec_mode: ask              # or smart
  terminal_backend: docker
  allow_local_fallback: false
security:
  scan_enabled: true
```

Report vulnerabilities privately — see
[SECURITY.md](https://github.com/Alien0013/aegis/blob/main/SECURITY.md).
