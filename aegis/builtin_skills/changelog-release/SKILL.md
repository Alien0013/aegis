---
name: changelog-release
description: Cut a release: derive a changelog from git history, bump the version, tag, and draft release notes (semver + conventional commits). Use for releases.
version: 1.0.0
metadata:
  category: release
  tags: [git, changelog, semver, versioning]
requires:
  bins: [git]
---

## When to Use
Cutting a new release: you need a changelog from commits, a correct version bump, a git tag, and release notes. Assumes conventional-commit-ish history (feat/fix/...) and semver.

## Procedure
1. Find last release: `git describe --tags --abbrev=0` (if none, use the repo's first commit). With bash, get the range `<last-tag>..HEAD`.
2. Collect commits: `git log <range> --pretty=format:'%s|%h'` (add `%b` if you need BREAKING CHANGE footers). read_file the project manifest to learn current version.
3. Decide bump (semver): any `BREAKING CHANGE`/`!` -> major; any `feat:` -> minor; only `fix:`/`perf:`/`chore:`/`docs:` -> patch. Pre-1.0: breaking -> minor, feat -> patch. State the decision before applying.
4. Group commits into sections: Features (`feat`), Fixes (`fix`), Performance (`perf`), Breaking Changes, Other. Drop `chore`/CI noise unless user wants it. Strip the type prefix; keep scope in `(scope)`.
5. Bump version: edit_file the manifest (package.json / pyproject.toml / Cargo.toml / VERSION). For npm prefer `npm version <major|minor|patch> --no-git-tag-version` to avoid hand-editing.
6. Prepend a section to `CHANGELOG.md` (Keep a Changelog style): `## [X.Y.Z] - YYYY-MM-DD` then grouped bullets. Create the file if absent.
7. Commit + tag: `git add -A && git commit -m "chore(release): vX.Y.Z"` then `git tag -a vX.Y.Z -m "vX.Y.Z"`. Do NOT push unless asked.
8. Draft release notes (reuse the changelog section) for GitHub. If `gh` exists and user asks: `gh release create vX.Y.Z --notes-file <file>`.

## Quick Reference
```bash
git describe --tags --abbrev=0                 # last tag
git log <last>..HEAD --pretty=format:'%s|%h'   # commits since
npm version minor --no-git-tag-version         # bump (node)
git tag -a vX.Y.Z -m "vX.Y.Z"                  # annotated tag
gh release create vX.Y.Z --notes-file NOTES.md # publish (optional)
```

## Pitfalls
- Use annotated tags (`-a`), not lightweight, so `git describe` and release tooling work.
- Tag prefix must match repo convention (`v1.2.3` vs `1.2.3`) — check existing tags first.
- Pre-1.0 semver differs (breaking != major). Confirm with the user near 1.0.
- Don't push or publish without explicit consent; commit and tag locally first.
- Version in manifest, tag, and changelog header must all agree.
- Merge commits and squash-merge noise can pollute the log; consider `--no-merges`.

## Verification
- `git tag --list | tail` shows the new tag; `git show vX.Y.Z` points at the release commit.
- Manifest version == tag version == top `CHANGELOG.md` header.
- `git log <last>..HEAD --oneline` count roughly matches changelog bullet count (minus dropped chores).
- Re-running `git describe --tags --abbrev=0` returns the new tag.
