"""Outbound email (M8).

Two consumers — scheduled reports and recurring-date greetings — and one
deliberate default: **with no SMTP host configured, nothing is sent.**

That is not a placeholder. A developer running the stack locally has a copy of
production-shaped data often enough that "the scheduler mailed a real customer
from my laptop" is a plausible accident, and a test suite that quietly required
a live SMTP server would be worse than one that has none. `RecordingEmailSender`
keeps what it would have sent, so local work and tests can assert on it.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

__all__ = [
    "Attachment",
    "EmailSender",
    "Outgoing",
    "RecordingEmailSender",
    "SmtpEmailSender",
    "build_sender",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content: bytes
    media_type: str = "text/csv"


@dataclass(frozen=True, slots=True)
class Outgoing:
    to: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[Attachment, ...] = ()


class EmailSender(Protocol):
    """The seam. Deliberately one method — M8 needs no more."""

    async def send(self, message: Outgoing) -> None: ...


@dataclass
class RecordingEmailSender:
    """Records instead of sending. The default when no host is configured."""

    sent: list[Outgoing] = field(default_factory=list)

    async def send(self, message: Outgoing) -> None:
        self.sent.append(message)
        logger.info(
            "email.suppressed",
            extra={
                "recipients": len(message.to),
                "subject": message.subject,
                "attachments": len(message.attachments),
            },
        )


class SmtpEmailSender:
    """Real delivery.

    `smtplib` is synchronous and this runs inside an async worker, so the send
    is pushed to a thread. A scheduler tick that blocked the event loop for the
    length of an SMTP handshake would stall every other schedule behind it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: Outgoing) -> None:
        import anyio

        await anyio.to_thread.run_sync(self._send_blocking, message)

    def _send_blocking(self, message: Outgoing) -> None:
        settings = self._settings
        assert settings.smtp_host is not None

        mail = EmailMessage()
        mail["From"] = settings.smtp_from_address
        mail["To"] = ", ".join(message.to)
        mail["Subject"] = message.subject
        mail.set_content(message.body)

        for attachment in message.attachments:
            maintype, _, subtype = attachment.media_type.partition("/")
            mail.add_attachment(
                attachment.content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=attachment.filename,
            )

        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
        ) as client:
            if settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username and settings.smtp_password:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(mail)


def build_sender(settings: Settings) -> EmailSender:
    """The configured sender, or the recording one when SMTP is not set up."""
    if settings.smtp_host:
        return SmtpEmailSender(settings)
    return RecordingEmailSender()
