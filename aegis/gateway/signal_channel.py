"""Signal channel adapter via the `signal-cli` binary.

Needs SIGNAL_CLI_ACCOUNT (the registered phone number, e.g. +15551234567).
The `signal-cli` binary must be installed and the account already registered/linked.

Optional SIGNAL_ALLOWED_USERS (comma-separated source numbers) restricts access.
SIGNAL_CLI_BIN overrides the binary path (default: "signal-cli").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .base import BasePlatformAdapter, Dispatch, MessageEvent


class SignalAdapter(BasePlatformAdapter):
    """Polls ``signal-cli receive --output=json`` and replies via ``signal-cli send``."""

    name = "signal"
    renders_tables = False
    transport = "signal_cli"

    def __init__(self, account: str | None = None):
        self.account = account or os.environ.get("SIGNAL_CLI_ACCOUNT")
        if not self.account:
            raise RuntimeError("SIGNAL_CLI_ACCOUNT (phone number) is not set.")
        self.bin = os.environ.get("SIGNAL_CLI_BIN", "signal-cli")
        if not shutil.which(self.bin):
            raise RuntimeError(
                f"`{self.bin}` binary not found. Install signal-cli and ensure it is on PATH."
            )
        allowed = os.environ.get("SIGNAL_ALLOWED_USERS", "").strip()
        self.allowed = {u.strip() for u in allowed.split(",") if u.strip()} if allowed else None

    def _run(self, *args: str, timeout: int | None = None) -> str:
        proc = subprocess.run(
            [self.bin, "-a", self.account, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"signal-cli {args[0]} failed: {proc.stderr.strip()}")
        return proc.stdout

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        while True:
            try:
                # --timeout blocks up to N seconds waiting for messages, then returns.
                out = self._run("receive", "--output=json", "--timeout", "10", timeout=60)
            except subprocess.TimeoutExpired:
                continue
            except RuntimeError:
                continue  # keep the poller alive across transient signal-cli errors
            for ev in self._parse(out):
                if self.allowed and ev.user_id not in self.allowed:
                    self.send(ev.chat_id, "not authorized.")
                    continue
                self._submit_inbound(ev)

    def _parse(self, out: str) -> list[MessageEvent]:
        """signal-cli emits one JSON object per line (JSON-RPC envelope)."""
        events: list[MessageEvent] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            envelope = obj.get("envelope") or obj.get("params", {}).get("envelope") or {}
            data = envelope.get("dataMessage")
            if not data:
                continue  # ignore receipts, typing indicators, sync messages, etc.
            attachments = self._attachments_from_data(data)
            text = str(data.get("message") or "").strip()
            if not text and attachments:
                text = self._attachment_reference_text(attachments)
            if not text and not attachments:
                continue
            source = envelope.get("sourceNumber") or envelope.get("source")
            if not source:
                continue
            group = (data.get("groupInfo") or {}).get("groupId")
            # Reply target: the group if present, else the individual sender.
            chat_id = f"group:{group}" if group else source
            timestamp = envelope.get("timestamp") or data.get("timestamp")
            events.append(
                MessageEvent(
                    platform="signal",
                    chat_id=chat_id,
                    text=text,
                    user_id=source,
                    user_name=envelope.get("sourceName"),
                    message_id=str(timestamp or "") or None,
                    timestamp=timestamp,
                    attachments=attachments,
                    metadata={
                        "group_id": group,
                        "source_uuid": envelope.get("sourceUuid") or envelope.get("sourceUUID"),
                        "source_device": envelope.get("sourceDevice"),
                        "server_guid": envelope.get("serverGuid") or envelope.get("serverGUID"),
                    },
                )
            )
        return events

    def _attachments_from_data(self, data: dict) -> list[dict]:
        rows: list[dict] = []
        for item in data.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            content_type = str(item.get("contentType") or item.get("content_type") or "").strip()
            filename = str(item.get("filename") or item.get("fileName") or "").strip()
            attachment_id = item.get("id") or item.get("pointer") or item.get("digest")
            row = {
                "id": str(attachment_id or "").strip(),
                "type": content_type or "file",
                "media_type": content_type,
                "filename": filename or "attachment",
                "size": int(item.get("size") or 0),
                "source": "signal",
            }
            for source, target in (
                ("width", "width"),
                ("height", "height"),
                ("caption", "caption"),
                ("blurHash", "blur_hash"),
            ):
                value = item.get(source)
                if value not in (None, ""):
                    row[target] = value
            rows.append(row)
        return rows

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("id") or "attachment").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:  # noqa: ARG002
        if not text:
            return
        if chat_id.startswith("group:"):
            target = ["-g", chat_id[len("group:"):]]
        else:
            target = [chat_id]
        try:
            self._run("send", "-m", text, *target, timeout=60)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
