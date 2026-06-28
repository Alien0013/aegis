# Messaging Gateway

The gateway connects channel adapters to the same AEGIS runtime used by terminal, dashboard, cron, and APIs. Local adapter tests prove message normalization; credentialed smoke tests prove real delivery.

Supported channel families include Telegram, Discord, Slack, Signal, Matrix, email, SMS bridges, Mattermost, ntfy, WhatsApp bridge variants, Feishu/Lark, WeCom, Weixin, DingTalk, QQBot, and Yuanbao-style bridge adapters.

Run:

```bash
aegis gateway --channels telegram,discord
aegis maturity --json --check
```
