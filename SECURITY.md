# Security Policy

AEGIS is an agent that can read/write files, run shell commands, browse the web, and
connect to messaging platforms. Treat it like any tool with system access.

## Reporting a vulnerability

Please open a **private** report via GitHub Security Advisories (Security → Report a
vulnerability), or email the maintainer. Do not file public issues for vulnerabilities.
We aim to acknowledge within 72 hours.

## Security model

- **Permission cascade** — every side-effecting tool flows through
  `deny_groups → exec_mode (deny|allowlist|ask|smart|auto|full) → allowlist → approval`.
  Read-only tools are always allowed.
- **Hardline blocklist** — catastrophic commands (`rm -rf /`, fork bombs, `curl|bash`,
  disk wipes) are refused **even in `--yolo`/`full` mode**.
- **Pre-execution scanning** — commands and installed skills are scanned for injection,
  exfiltration, and obfuscation (`security.scan_enabled`, `aegis security audit`).
- **Fail-closed sandboxing** — when `tools.terminal_backend` is `docker`/`ssh` and the
  backend is unavailable, AEGIS **refuses to run on the host** unless you set
  `tools.allow_local_fallback: true`.
- **Secret hygiene** — API keys live in `~/.aegis/.env` (chmod 0600); OAuth tokens in
  `~/.aegis/auth.json` (chmod 0600); `execute_code` strips secrets from the child env;
  learning candidates and debug bundles are redacted.
- **Gateway authorization** — unknown users must pair (`aegis pairing`); group mention
  gating is available.

## Hardening tips

- Run with `tools.exec_mode: ask` (default) or `smart`; reserve `auto`/`full` for trusted,
  sandboxed contexts.
- Use `tools.terminal_backend: docker` with `tools.allow_local_fallback: false`.
- Keep `security.scan_enabled: true`; run `aegis security audit` before installing skills.
- Restrict gateway access with `*_ALLOWED_USERS` and `require_mention`.
