# Releasing AEGIS to PyPI

The package builds cleanly and the GitHub Actions workflow is ready. Publishing needs
**your** PyPI account + an API token (I can't create those for you). Three steps:

## 1. Create a PyPI account + token (one time, ~2 min)

1. Sign up at <https://pypi.org/account/register/> and verify your email.
2. Go to **Account settings → API tokens → Add API token**.
   - Token name: `aegis`  ·  Scope: **Entire account** (you can narrow it after the first upload).
3. Copy the token (starts with `pypi-…`). You only see it once.

## 2. Add the token to GitHub (so releases auto-publish)

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `PYPI_API_TOKEN`
- Value: the `pypi-…` token

(Optional: make a TestPyPI token the same way at <https://test.pypi.org> and add it as
`TEST_PYPI_API_TOKEN` to dry-run first.)

## 3. Publish

**Automatic (recommended)** — tag a version and push; CI runs the tests then publishes:

```bash
git tag v0.1.0
git push origin v0.1.0
```

**Manual (from your machine)** — no GitHub needed:

```bash
pip install build twine
python -m build
twine upload dist/*          # paste your pypi-… token when asked (username: __token__)
```

**Dry-run on TestPyPI first** (optional): in GitHub → Actions → *Release* → *Run workflow*
with `target = testpypi`, then `pip install -i https://test.pypi.org/simple/ aegis-agent-harness`.

## After publishing

```bash
pip install aegis-agent-harness     # the literal install now works
aegis --version
```

The `aegis update --check` command will then track PyPI releases too.

> The PyPI **distribution name** is `aegis-agent-harness` (the short `aegis-agent` was
> already taken). The **command** is still `aegis` and the **import** is still `aegis`.
> To use a different name, change `[project].name` in `pyproject.toml` and re-tag.
