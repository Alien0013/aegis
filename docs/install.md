# Install

## One line (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
```

Finds Python 3.10+, builds an isolated venv at `~/.aegis/venv`, installs from git, and
puts a global `aegis` on your PATH. Windows: `irm …/install.ps1 | iex`.

Everything in one go (browser, computer-use, Discord, Slack, Matrix, memory backends):

```bash
AEGIS_EXTRAS=all curl -fsSL …/install.sh | bash
playwright install chromium     # if you took the browser extra
```

## From a clone / for development

```bash
git clone https://github.com/Alien0013/aegis && cd aegis
./install.sh                    # or: python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[all]"
aegis doctor
```

## Android (Termux)

```bash
pkg install python git
curl -fsSL …/install.sh | bash  # detects Termux, installs into $PREFIX/bin
```

## Optional extras

`.[browser]`, `.[computer]`, `.[discord]`, `.[slack]`, `.[matrix]`, `.[honcho]`,
`.[mem0]`, `.[all]`. Core (providers, OAuth, MCP, marketplace, gateway, serve, learn,
voice) needs no extras.

Keep current with `aegis update` (`--check`, `--branch`). Remove with `./uninstall.sh`.
