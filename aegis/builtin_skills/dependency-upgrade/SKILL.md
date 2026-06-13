---
name: dependency-upgrade
description: Safely upgrade project dependencies — audit what's outdated and why, upgrade incrementally, read breaking changes, and verify each step. Use when bumping libraries, patching CVEs, or unsticking a stale lockfile.
version: 1.0.0
metadata:
  category: maintenance
  tags: [dependencies, upgrade, security, cve, maintenance]
---

## When to Use
Dependencies are outdated, a security advisory landed, or a lockfile is stale. The risk is a silent breaking change, so upgrade in **small, verified steps** — never one giant bump.

## Procedure
1. **Inventory.** List what's outdated and how far (patch / minor / major), and flag anything with a known vulnerability. Patches and security fixes are low-risk and go first; majors are projects of their own.
2. **Prioritize by risk.** Order: security patches → patch/minor bumps of direct deps → major bumps (one at a time) → transitive-only pins. Don't mix a routine bump with a risky major in the same change.
3. **Upgrade one meaningful unit at a time.** One library (or one cohesive group) per commit. After each: install, run the full test suite, run the app's smoke path. A green checkpoint per step means a failure points at exactly one cause.
4. **Read the changelog for majors.** Before a major bump, read its release notes / migration guide and `grep` the codebase for the APIs it removed or renamed. Apply the documented migration; don't guess.
5. **Pin and lock.** Commit the updated lockfile so the result is reproducible. Keep sane version ceilings (don't float to "latest") to avoid the next silent break.
6. **Re-audit at the end.** Confirm the vulnerability that prompted this is actually gone, and no new advisory was pulled in by a transitive dependency.

## Quick Reference
```bash
# node
npm outdated ;  npm audit ;  npm audit fix         # safe fixes
npm install pkg@latest                              # one major, deliberately
# python
pip list --outdated ;  pip-audit                    # or `uv pip ...`
pip install -U pkg && pytest                         # one at a time
# rust / go
cargo update -p pkg ;  cargo audit
go get pkg@latest ;  go mod tidy ;  govulncheck ./...
```

## Pitfalls
- One mega-upgrade of everything at once — when tests break, you can't tell which bump did it.
- `npm audit fix --force` blindly — it can pull in breaking majors and "fix" by downgrading you.
- Bumping a major without reading its migration guide, then guessing at the new API.
- Updating `package.json`/`requirements` but not committing the lockfile — non-reproducible builds.
- Removing version ceilings ("just use latest") — trades today's chore for tomorrow's surprise outage.
- Declaring a CVE fixed without re-running the audit to confirm.

## Verification
- Full test suite **and** the app's smoke path pass after every step, not just at the end.
- The advisory/CVE that triggered the work no longer appears in a fresh audit; no new high-severity advisory was introduced.
- The lockfile is updated and committed; a clean install reproduces the exact versions.
- For each major bump, the documented migration was applied and removed/renamed APIs no longer appear in the code.
