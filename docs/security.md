# Security

AEGIS can read/write files, run shell commands, browse the web, and connect to messaging
platforms. Treat it like any tool with system access.

## Defenses

- **Permission cascade** — `hardline blocklist → deny_groups → exec_mode → allowlist →
  approval`. Read-only tools are always allowed.
- **Hardline blocklist** — `rm -rf /`, fork bombs, `curl|bash`, disk wipes are refused
  even in `--yolo`/`full`.
- **Pre-execution scanning** — `security.scan_enabled` + `aegis security audit`
  (deps/MCP/plugins/skills).
- **Fail-closed sandboxing** — docker/ssh/singularity/modal backends refuse to run on the
  host when unavailable unless `tools.allow_local_fallback: true`.
- **Secret hygiene** — keys in `~/.aegis/.env` (0600), OAuth tokens in `auth.json` (0600);
  `execute_code` strips secrets from the child env; learn candidates + debug bundles are
  redacted.
- **Gateway** — DM pairing, mention gating, per-user allowlists.

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
