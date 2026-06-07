---
name: github-actions
description: Set up GitHub Actions CI/CD: lint, test, build, and release workflows in .github/workflows. Use when asked to add CI or automate a pipeline.
version: 1.0.0
metadata:
  category: ci
  tags: [github, ci-cd, automation, workflows]
---

## When to Use
When asked to add CI, automate lint/test/build, or create release pipelines for a repo hosted on GitHub. Skip if the repo uses another CI (GitLab CI, CircleCI, Jenkins).

## Procedure
1. Detect the stack: `read_file` package.json / pyproject.toml / go.mod / Cargo.toml to learn the language, package manager, and existing scripts (test, lint, build).
2. Reuse existing commands — don't invent. Mirror what `npm test`, `pytest`, `make`, etc. already do locally.
3. `write_file` `.github/workflows/ci.yml`. One job per concern (lint, test, build) or a matrix across versions/OS. Always `actions/checkout@v4` first, then the language setup action with dependency caching.
4. Set sensible triggers: `on: [push, pull_request]`, optionally scope branches (`main`) and add `workflow_dispatch` for manual runs.
5. For releases, add a separate `release.yml` triggered `on: push: tags: ['v*']` using `softprops/action-gh-release` or `actions/upload-artifact`.
6. Pin actions to a major tag (`@v4`), not `@latest`. Use least-privilege `permissions:` at the top.
7. Validate YAML locally: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`. Optionally dry-run with `act` if installed.
8. Commit and push; confirm the run on GitHub via `gh run list` / `gh run watch`.

## Quick Reference
```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix: { node: [18, 20] }   # or python-version, go-version
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '${{ matrix.node }}', cache: 'npm' }
      - run: npm ci
      - run: npm run lint
      - run: npm test
```
- List/watch runs: `gh run list`, `gh run watch`, `gh run view --log-failed`
- Secrets: `gh secret set NAME`; reference as `${{ secrets.NAME }}`.

## Pitfalls
- Indentation: workflows are YAML — tabs break them; use 2 spaces.
- `npm install` is non-deterministic in CI; use `npm ci` (needs a lockfile).
- Forgetting dependency caching makes runs slow and flaky — set `cache:` in the setup action.
- Secrets are NOT available to workflows from forked-PR triggers; design accordingly.
- Default `GITHUB_TOKEN` may lack write scope — add explicit `permissions:` for releases/PR comments.

## Verification
- YAML parses (step 7) and `gh workflow list` shows the new workflow.
- Push a commit/open a PR and confirm the run goes green: `gh run watch` exits 0.
- For releases, push a test tag and verify the GitHub release/artifact appears.
