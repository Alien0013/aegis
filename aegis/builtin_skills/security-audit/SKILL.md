---
name: security-audit
description: "Audit code/configs for vulnerabilities: injection, secrets, authz, unsafe deserialization, SSRF, dependency CVEs. Use when asked to security-review."
version: 1.0.0
metadata:
  category: security
  tags: [audit, vulnerabilities, sast, cve]
---

## When to Use
When asked to security-review code, configs, or a diff before merge/deploy. Covers injection, hardcoded secrets, broken authz, unsafe deserialization, SSRF, and vulnerable dependencies.

## Procedure
1. Scope it: `git diff --name-only main...HEAD` (or read_file on named paths). Audit only the relevant surface unless a full review is requested.
2. Secrets: grep for hardcoded credentials — `grep -rnE '(api[_-]?key|secret|token|password|aws_access)' --include='*.{py,js,ts,go,env,yml,yaml,json}' .` and flag committed `.env` files.
3. Injection: trace user input (request params, args, stdin) to sinks. SQL → string-built queries; OS → `os.system`/`subprocess(shell=True)`/`exec`/`eval`; template/XSS → unescaped output.
4. Deserialization: search `pickle.load`, `yaml.load` (non-safe), `marshal`, Java/PHP native deserialize on untrusted data.
5. SSRF: outbound HTTP using user-controlled URLs/hosts with no allowlist (`requests.get(user_url)`, fetch).
6. Authz/authn: endpoints/handlers missing auth checks; IDOR (object id from request used without ownership check); weak/absent session or JWT validation.
7. Dependency CVEs: run the ecosystem auditor (Quick Reference). Report fixed-in versions.
8. Rank findings by severity (Critical/High/Med/Low) with file:line, impact, and a concrete fix. Use execute_code only to confirm an exploit path when ambiguous.

## Quick Reference
- Python deps: `pip-audit` (or `pip install pip-audit && pip-audit -r requirements.txt`)
- Node deps: `npm audit --omit=dev` / `yarn npm audit`
- Multi-lang SAST: `semgrep --config auto .`
- Secret scan: `gitleaks detect --no-banner` or `trufflehog filesystem .`
- Static (py): `bandit -r . -ll`

## Pitfalls
- Don't trust framework defaults to sanitize — verify the actual sink (ORM `.raw()`, f-string SQL bypass parameterization).
- A grep hit for "password" may be a variable name, not a secret; confirm the literal value.
- `yaml.safe_load` is safe; `yaml.load` without `Loader=SafeLoader` is not.
- Validate SSRF allowlists against DNS-rebind and redirect bypasses.
- Don't paste real secrets you find into output; redact them.

## Verification
- Every Critical/High has file:line, concrete impact, and a remediation.
- Auditor tools actually ran (capture exit code/output), not assumed.
- Re-run `semgrep`/`pip-audit` after fixes to confirm findings cleared.
- No false positive left unmarked: each flagged item is either confirmed exploitable or annotated as needs-review.
