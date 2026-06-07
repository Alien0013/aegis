# Quickstart

```bash
aegis setup                                   # interactive: provider + key + exec mode
# or pick directly:
aegis config set ANTHROPIC_API_KEY sk-ant-…   # API key
aegis auth login anthropic                     # …or OAuth
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

`/help /status /model /think <level> /tools /skills /memory /usage /compress`
`/retry /undo /learn /background <prompt> /tasks /rollback /personality /save`
`/sessions /new /quit`. Reference a file with `@path`.

## Next

- Make it a bot: `aegis gateway --channels telegram,discord`
- Let it learn: set `learn.auto: true`, then `aegis learn list`
- Use it as a backend: `aegis serve` (OpenAI API) or `aegis mcp serve` (MCP)
- Recover from a bad edit: `/rollback`
