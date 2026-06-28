# Sessions and Recovery

Sessions persist messages, summaries, trace/run links, metadata, and search terms. Recovery means a later AEGIS run can locate and continue work after a crash using session search, resume, latest descendant, and run timelines.

Run:

```bash
aegis sessions list
aegis sessions search "dashboard token"
aegis maturity --check
```
