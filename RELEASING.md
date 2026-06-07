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
python -m build
twine upload dist/*          # username: __token__ · password: a pypi-… token you create
```

## After publishing

```bash
pip install aegis-agent-harness     # the literal install now works
aegis --version
```

The `aegis update --check` command will then track PyPI releases too.

> The PyPI **distribution name** is `aegis-agent-harness` (the short `aegis-agent` was
> already taken). The **command** is still `aegis` and the **import** is still `aegis`.
> To use a different name, change `[project].name` in `pyproject.toml` and re-tag.
