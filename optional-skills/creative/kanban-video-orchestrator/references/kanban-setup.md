# AEGIS Kanban pipeline setup

AEGIS includes native kanban primitives for plan tracking and worker orchestration.
Use the CLI and Python modules directly instead of a separate external bootstrap:

```bash
aegis kanban --help
python -m aegis.kanban --help
```

Programmatic users should import `aegis.kanban` or `aegis.kanban_auto`.
