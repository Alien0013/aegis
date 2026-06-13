# Quickstart

```bash
aegis setup                                   # interactive: provider + key/OAuth + exec mode
aegis setup --non-interactive --accept-risk --json
# or pick directly:
aegis config set ANTHROPIC_API_KEY sk-ant-…   # API key
aegis config set OPENAI_API_KEY sk-…          # OpenAI API key
aegis config set QWEN_API_KEY sk-…            # Qwen / DashScope-compatible API key
codex login                                    # ChatGPT subscription auth
aegis model set codex gpt-5.5                  # stateless Codex backend
aegis model set qwen qwen-max                  # Qwen API-compatible provider
aegis model set codex-app-server gpt-5.5       # optional Codex-native runtime
aegis model set ollama llama3.1                # …or fully local
```

Then:

```bash
aegis                       # interactive REPL (streaming, slash commands)
aegis chat -q "summarize this folder"
aegis chat --continue       # resume the last session
aegis chat --image plot.png "what's wrong with this chart?"
aegis batch prompts.txt     # one prompt per line
```

## Slash commands (REPL)

`/help /status /model /provider /think <level> /tools /skills /memory /usage /compress`
`/retry /undo /learn /background <prompt> /tasks /rollback /personality /save`
`/sessions /new /quit`. Reference a file with `@path`.

## Next

- Make it a bot: `aegis gateway --channels telegram,discord`
- Let it learn: set `learn.auto: true`, then `aegis learn list`
- Use it as a backend: `aegis serve` (OpenAI API) or `aegis mcp serve` (MCP)
- Recover from a bad edit: `/rollback`
