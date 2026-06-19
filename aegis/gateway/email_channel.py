"""Email channel adapter (IMAP poll + SMTP reply), stdlib only.

Env: EMAIL_IMAP_HOST, EMAIL_SMTP_HOST, EMAIL_ADDRESS, EMAIL_PASSWORD
     (optional EMAIL_IMAP_PORT=993, EMAIL_SMTP_PORT=465, EMAIL_POLL=20)
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import parseaddr

from .base import BasePlatformAdapter, Dispatch, MessageEvent

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)


class EmailAdapter(BasePlatformAdapter):
    name = "email"

    def __init__(self):
        self.address = os.environ.get("EMAIL_ADDRESS")
        self.password = os.environ.get("EMAIL_PASSWORD")
        self.imap_host = os.environ.get("EMAIL_IMAP_HOST")
        self.smtp_host = os.environ.get("EMAIL_SMTP_HOST")
        if not all((self.address, self.password, self.imap_host, self.smtp_host)):
            raise RuntimeError("Email channel needs EMAIL_ADDRESS, EMAIL_PASSWORD, "
                               "EMAIL_IMAP_HOST, EMAIL_SMTP_HOST.")
        self.imap_port = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
        self.smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "465"))
        self.poll = int(os.environ.get("EMAIL_POLL", "20"))
        allowed = os.environ.get("EMAIL_ALLOWED_SENDERS", "").strip()
        self.allowed_senders = {item.strip().lower() for item in allowed.split(",") if item.strip()} if allowed else None

    def _normalized_subject(self, subject: str | None) -> str:
        text = str(subject or "").strip()
        while True:
            updated = _SUBJECT_PREFIX_RE.sub("", text).strip()
            if updated == text:
                return updated
            text = updated

    def _conversation_key(self, ev: MessageEvent) -> str:
        subject = self._normalized_subject(ev.thread_id)
        return f"{ev.chat_id}:thread:{subject}" if subject else ev.chat_id

    def _reply_subject(self, metadata: dict | None = None, *, default: str = "Message from AEGIS") -> str:
        source = metadata or {}
        subject = self._normalized_subject(
            str(source.get("thread_id") or source.get("subject") or default)
        ) or default
        return f"Re: {subject}"

    def _body(self, msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, "replace")
            return ""
        payload = msg.get_payload(decode=True)
        if payload is None:
            payload = str(msg.get_payload() or "").encode("utf-8", "replace")
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, "replace")

    def _attachments(self, msg) -> list[dict]:
        rows: list[dict] = []
        for part in msg.walk() if msg.is_multipart() else []:
            filename = part.get_filename()
            disposition = str(part.get("Content-Disposition") or "").lower()
            if not filename and "attachment" not in disposition:
                continue
            payload = part.get_payload(decode=True) or b""
            content_id = str(part.get("Content-ID") or "").strip("<>")
            rows.append({
                "id": content_id or filename or part.get_content_type(),
                "type": part.get_content_type(),
                "media_type": part.get_content_type(),
                "filename": filename or "attachment",
                "size": len(payload),
                "source": "email",
            })
        return rows

    def _attachment_reference_text(self, attachments: list[dict]) -> str:
        labels = []
        for attachment in attachments:
            kind = str(attachment.get("type") or "file").strip()
            name = str(attachment.get("filename") or attachment.get("id") or "attachment").strip()
            labels.append(f"[{kind} attached: {name}]")
        return "\n".join(labels)

    def _clean_waiter_answer(self, ev: MessageEvent) -> str:
        text = str(ev.text or "").strip()
        if not text:
            return ""
        lines = text.splitlines()
        subject = self._normalized_subject(ev.thread_id)
        if lines and self._normalized_subject(lines[0]) == subject:
            lines = lines[1:]
        cleaned: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if cleaned:
                    break
                continue
            if stripped.startswith(">"):
                break
            lowered = stripped.lower()
            if lowered.startswith(("on ", "from:", "sent:", "subject:", "to:")):
                break
            cleaned.append(stripped)
        return "\n".join(cleaned).strip() or text

    def _resolve_clarify_waiter(self, ev: MessageEvent) -> bool:
        self._ensure_inbound_queue()
        if getattr(ev, "internal", False):
            return False
        key = self._conversation_key(ev)
        with self._qlock:
            waiters = self._clarify_waiters.get(key) or []
            waiter = waiters.pop(0) if waiters else None
            if not waiters:
                self._clarify_waiters.pop(key, None)
        if waiter is None:
            return False
        waiter["answer"] = self._clean_waiter_answer(ev)
        waiter["event"].set()
        return True

    def start(self, dispatch: Dispatch) -> None:
        self._init_inbound_queue(dispatch)
        while True:
            try:
                imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
                imap.login(self.address, self.password)
                imap.select("INBOX")
                _, data = imap.search(None, "UNSEEN")
                for num in data[0].split():
                    _, raw = imap.fetch(num, "(RFC822)")
                    msg = email.message_from_bytes(raw[0][1])
                    sender = parseaddr(msg.get("From"))[1]
                    if self.allowed_senders and sender.lower() not in self.allowed_senders:
                        imap.store(num, "+FLAGS", "\\Seen")
                        continue
                    subject = msg.get("Subject", "")
                    attachments = self._attachments(msg)
                    body = self._body(msg)
                    text = (subject + "\n\n" + body).strip()
                    if not text and attachments:
                        text = self._attachment_reference_text(attachments)
                    imap.store(num, "+FLAGS", "\\Seen")
                    ev = MessageEvent(platform="email", chat_id=sender, text=text,
                                      user_id=sender, thread_id=subject,
                                      message_id=str(msg.get("Message-ID") or "") or None,
                                      reply_to_message_id=str(msg.get("In-Reply-To") or "") or None,
                                      timestamp=msg.get("Date"),
                                      attachments=attachments,
                                      metadata={
                                          "subject": subject,
                                          "message_id": str(msg.get("Message-ID") or ""),
                                          "in_reply_to": str(msg.get("In-Reply-To") or ""),
                                          "references": str(msg.get("References") or ""),
                                      })
                    self._submit_inbound(ev)
                imap.logout()
            except Exception:  # noqa: BLE001 — keep the poller alive
                pass
            time.sleep(self.poll)

    def send(
        self,
        chat_id: str,
        text: str,
        subject: str = "Message from AEGIS",
        *,
        metadata: dict | None = None,
    ) -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = chat_id
        msg["Subject"] = subject
        in_reply_to = str((metadata or {}).get("message_id") or (metadata or {}).get("in_reply_to") or "").strip()
        references = str((metadata or {}).get("references") or "").strip()
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = f"{references} {in_reply_to}".strip()
        elif references:
            msg["References"] = references
        msg.set_content(text)
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as s:
                s.login(self.address, self.password)
                s.send_message(msg)
        except Exception:  # noqa: BLE001
            pass

    def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: list[str] | None = None,
        *,
        metadata: dict | None = None,
    ) -> None:
        rendered = str(question or "").strip()
        for i, choice in enumerate(choices or [], 1):
            rendered += f"\n  {i}. {choice}"
        if choices:
            rendered += "\n\nReply with the number or exact choice."
        self.send(
            chat_id,
            rendered,
            subject=self._reply_subject(metadata, default="AEGIS clarification"),
            metadata=metadata,
        )

    def send_exec_approval(
        self,
        chat_id: str,
        prompt: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        rendered = str(prompt or "").strip()
        if rendered:
            rendered += "\n\n"
        rendered += "Reply approve, always, or deny."
        self.send(
            chat_id,
            rendered,
            subject=self._reply_subject(metadata, default="AEGIS approval"),
            metadata=metadata,
        )

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        if reply:
            self.send(
                ev.chat_id,
                reply,
                subject=self._reply_subject(ev.metadata, default=ev.thread_id or "Message from AEGIS"),
                metadata=ev.metadata,
            )
