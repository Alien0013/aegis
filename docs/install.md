# Install

## One line (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
```

Finds Python 3.10+, builds an isolated venv at `~/.aegis/venv`, installs the full
curated stack (`.[all]`) from git, installs Playwright Chromium for browser tools,
puts a global `aegis` launcher on your PATH, then starts guided onboarding when a
terminal is available. Prompts are read from `/dev/tty`, so `curl | bash` works.
The wizard offers OAuth or API-key auth first, then model selection, web tools,
inline channel selection, and starter files in `~/.aegis/workspace`. Set
`AEGIS_ONBOARD_DIALOGS=1` for fullscreen prompt-toolkit selectors. Skip
prompts for headless automation with `--no-prompt` or `--non-interactive`; this
still runs safe default onboarding and prints JSON. Skip onboarding entirely with
`--skip-onboard` or `AEGIS_ONBOARD=0`. Use `--core` for a smaller CLI-only install, or
`--skip-browser` to skip the Chromium download. Windows: `irm …/install.ps1 | iex`.

Everything in one go (browser, computer-use, Discord, Slack, Matrix, memory backends):

```bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --advanced
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --verify
curl -fsSL https://raw.githubusercontent.com/Alien0013/aegis/main/install.sh | bash -s -- --core
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
