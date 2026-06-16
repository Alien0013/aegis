"""Email channel adapter (IMAP poll + SMTP reply), stdlib only.

Env: EMAIL_IMAP_HOST, EMAIL_SMTP_HOST, EMAIL_ADDRESS, EMAIL_PASSWORD
     (optional EMAIL_IMAP_PORT=993, EMAIL_SMTP_PORT=465, EMAIL_POLL=20)
"""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
import time
from email.message import EmailMessage
from email.utils import parseaddr

from .base import BasePlatformAdapter, Dispatch, MessageEvent


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

    def _body(self, msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode("utf-8", "replace")
            return ""
        return msg.get_payload(decode=True).decode("utf-8", "replace")

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
                    subject = msg.get("Subject", "")
                    text = (subject + "\n\n" + self._body(msg)).strip()
                    imap.store(num, "+FLAGS", "\\Seen")
                    ev = MessageEvent(platform="email", chat_id=sender, text=text,
                                      user_id=sender, thread_id=subject,
                                      timestamp=msg.get("Date"))
                    self._submit_inbound(ev)
                imap.logout()
            except Exception:  # noqa: BLE001 — keep the poller alive
                pass
            time.sleep(self.poll)

    def send(self, chat_id: str, text: str, subject: str = "Message from AEGIS") -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = chat_id
        msg["Subject"] = subject
        msg.set_content(text)
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as s:
                s.login(self.address, self.password)
                s.send_message(msg)
        except Exception:  # noqa: BLE001
            pass

    def _deliver_reply(self, ev: MessageEvent, reply: str, state=None) -> None:  # noqa: ANN001
        if reply:
            self.send(ev.chat_id, reply, subject=f"Re: {ev.thread_id or 'Message from AEGIS'}")
