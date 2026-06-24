# Releasing AEGIS to PyPI

The package builds cleanly and the GitHub Actions workflow uses **Trusted Publishing**
(OIDC) — **no API token to copy or store**. Two steps:

## 1. Register the trusted publisher (one time, ~2 min)

1. Sign up at <https://pypi.org/account/register/> and verify your email.
2. Go to <https://pypi.org/manage/account/publishing/> → **Add a new pending publisher**:
   - PyPI Project Name: `aegis-agent-harness`
   - Owner: `Alien0013`  ·  Repository: `aegis`  ·  Workflow name: `release.yml`
   - Environment: *(leave blank)*
3. (Optional, to dry-run first) do the same at <https://test.pypi.org/manage/account/publishing/>.

That's it — no secret in GitHub, nothing to paste to anyone.

## 2. Publish

**Automatic** — tag a version; CI runs the tests then publishes via OIDC:

```bash
git tag v0.1.0
git push origin v0.1.0
```

**Dry-run on TestPyPI first** (optional): GitHub → Actions → *Release* → *Run workflow*
with `target = testpypi`, then `pip install -i https://test.pypi.org/simple/ aegis-agent-harness`.

**Manual, no GitHub** (uses a token instead of OIDC):

```bash
pip install build twine
scripts/check_web_dist.sh
python -m build
twine upload dist/*          # username: __token__ · password: a pypi-… token you create
```

## Release verification checklist

Before any production tag, the local and CI gates should agree:

```bash
bash scripts/verify_all.sh
python scripts/release_provenance.py --artifact-dir dist --out release-provenance/python
python scripts/release_provenance.py --artifact-dir dist --out release-provenance/python --check
```

`scripts/verify_all.sh` runs the parity ledger checker, generated-reference
drift check, Python tests, release-provenance smoke, web typecheck/build,
desktop tests, Python compile checks, and `git diff --check`.

For desktop artifacts, CI generates the same proof inside each
`desktop/release` folder:

- `SHA256SUMS`
- `sbom.cdx.json`
- `release-summary.json`

The desktop release jobs verify those files before uploading artifacts or
attaching files to a GitHub Release. The packaged backend manifest also includes
per-file hashes for the staged backend.

## Desktop packaged smoke

Desktop release builds must stage a backend unless they explicitly declare an
external backend dependency:

```bash
cd desktop
AEGIS_RELEASE=1 AEGIS_DESKTOP_BACKEND_SOURCE=build/ci-backend npm run dist:linux
```

Packaging fails if the staged backend command cannot run `--version`, if unsafe
symlinks are present, or if a release build is missing required signing and
notarization inputs without an explicit override.

## Signing and unsigned overrides

Signed/notarized artifacts are only claimed when credentials are present:

- Windows: `DESKTOP_WINDOWS_CSC_LINK`/`DESKTOP_WINDOWS_CSC_NAME` plus password
  as needed.
- macOS: signing material plus either Apple ID notarization credentials or App
  Store Connect API key credentials.

If credentials are unavailable, set `AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE=1` only
for internal testing. Unsigned builds are not equivalent to signed/notarized
release artifacts, and release notes must say they are unsigned.

## After publishing

```bash
pip install aegis-agent-harness     # the literal install now works
aegis --version
```

The `aegis update --check` command will then track PyPI releases too.

> The PyPI **distribution name** is `aegis-agent-harness` (the short `aegis-agent` was
> already taken). The **command** is still `aegis` and the **import** is still `aegis`.
> To use a different name, change `[project].name` in `pyproject.toml` and re-tag.
