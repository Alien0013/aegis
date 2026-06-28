
"""Credential-gated gateway live-smoke preflight tests.

These tests are intentionally skipped during normal local gates. They become
active only when a live command sets AEGIS_LIVE_TARGET and the matching
AEGIS_LIVE_* opt-in variable. The assertions validate that the configured
credential/environment contract is present before a human records external
delivery evidence.
"""

from __future__ import annotations

import os

import pytest


REQUIRED_ENV_GROUPS = {
    "api_server": (("API_SERVER_KEY", "API_SERVER_API_KEY"),),
    "webhook": (("WEBHOOK_URL",), ("WEBHOOK_SECRET",)),
    "telegram": (("TELEGRAM_BOT_TOKEN",), ("TELEGRAM_CHAT_ID",)),
    "discord": (("DISCORD_BOT_TOKEN",), ("DISCORD_CHANNEL_ID",)),
    "slack": (("SLACK_BOT_TOKEN",), ("SLACK_APP_TOKEN",)),
    "matrix": (("MATRIX_HOMESERVER",), ("MATRIX_USER",), ("MATRIX_PASSWORD",)),
    "signal": (("SIGNAL_CLI_ACCOUNT",),),
    "email": (("EMAIL_IMAP_HOST",), ("EMAIL_SMTP_HOST",), ("EMAIL_ADDRESS",), ("EMAIL_PASSWORD",)),
    "sms": (("TWILIO_ACCOUNT_SID",), ("TWILIO_AUTH_TOKEN",), ("TWILIO_FROM",)),
    "whatsapp": (("WHATSAPP_BRIDGE_URL",),),
    "whatsapp_cloud": (("WHATSAPP_CLOUD_TOKEN",), ("WHATSAPP_CLOUD_PHONE_ID",)),
    "ntfy": (("NTFY_TOPIC",),),
    "mattermost": (("MATTERMOST_URL",), ("MATTERMOST_BOT_TOKEN",)),
    "homeassistant": (("HOMEASSISTANT_CHANNEL_OUTBOUND_URL", "HOMEASSISTANT_URL"),),
    "dingtalk": (("DINGTALK_CLIENT_ID",), ("DINGTALK_TOKEN",)),
    "feishu": (("FEISHU_APP_ID",), ("FEISHU_APP_TOKEN",)),
    "wecom": (("WECOM_CORP_ID",), ("WECOM_AGENT_ID",)),
    "weixin": (("WEIXIN_APP_ID",), ("WEIXIN_TOKEN",)),
    "bluebubbles": (("BLUEBUBBLES_CHANNEL_OUTBOUND_URL", "BLUEBUBBLES_URL"),),
    "qqbot": (("QQBOT_APP_ID",), ("QQBOT_TOKEN",)),
    "yuanbao": (("YUANBAO_SESSION",),),
    "relay": (("RELAY_CHANNEL_OUTBOUND_URL", "RELAY_URL"),),
    "msgraph_webhook": (("MSGRAPH_WEBHOOK_CHANNEL_OUTBOUND_URL", "MSGRAPH_WEBHOOK_URL"),),
}


def _missing(groups: tuple[tuple[str, ...], ...]) -> list[str]:
    missing = []
    for alternatives in groups:
        if not any(os.getenv(name) for name in alternatives):
            missing.append(" or ".join(alternatives))
    return missing


def test_live_gateway_preflight_env_contract():
    target = os.getenv("AEGIS_LIVE_TARGET", "").strip()
    if not target:
        pytest.skip("set AEGIS_LIVE_TARGET through a matrix live_proof_command to run a gateway live preflight")
    assert target in REQUIRED_ENV_GROUPS
    opt_in = f"AEGIS_LIVE_{target.upper()}"
    assert os.getenv(opt_in) == "1", f"{opt_in}=1 is required for live gateway preflight"
    missing = _missing(REQUIRED_ENV_GROUPS[target])
    assert not missing, f"missing required environment for {target}: {', '.join(missing)}"
