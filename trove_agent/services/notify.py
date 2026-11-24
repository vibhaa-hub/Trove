"""Delivery of queued notifications (transactional outbox -> SMTP / SMS).

Configure with environment variables:

  SMTP_HOST, SMTP_PORT (587), SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM,
  SMTP_STARTTLS (true)
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM       (SMS)
  or SMS_WEBHOOK_URL + SMS_WEBHOOK_TOKEN                     (any SMS gateway)

A message is attempted up to 5 times. After a successful send the body is
wiped from the database; only the redacted body remains as a delivery log.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trove_agent.core.store import TroveService, now

log = logging.getLogger("trove.notify")
MAX_ATTEMPTS = 5


class Channel(Protocol):
    def send(self, destination: str, subject: str, body: str) -> None: ...


class SmtpEmail:
    def __init__(self) -> None:
        self.host = os.environ["SMTP_HOST"]
        self.port = int(os.getenv("SMTP_PORT", "587"))
        self.username = os.getenv("SMTP_USERNAME")
        self.password = os.getenv("SMTP_PASSWORD")
        self.sender = os.getenv("SMTP_FROM", "Trove <no-reply@localhost>")
        self.starttls = os.getenv("SMTP_STARTTLS", "true").casefold() != "false"

    def send(self, destination: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, destination, subject
        message.set_content(body)
        context = ssl.create_default_context()
        if self.port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=20)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
        with server:
            if self.starttls and self.port != 465:
                server.starttls(context=context)
            if self.username:
                server.login(self.username, self.password or "")
            server.send_message(message)


class TwilioSms:
    def __init__(self) -> None:
        self.sid = os.environ["TWILIO_ACCOUNT_SID"]
        self.token = os.environ["TWILIO_AUTH_TOKEN"]
        self.sender = os.environ["TWILIO_FROM"]

    def send(self, destination: str, subject: str, body: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        request = Request(url, data=urlencode({"To": destination, "From": self.sender,
                                               "Body": body[:1500]}).encode(),
                          headers={"Authorization": f"Basic {auth}"}, method="POST")
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https URL
            if response.status >= 300:
                raise RuntimeError(f"Twilio returned {response.status}")


class WebhookSms:
    """Generic gateway: POST {"to": ..., "message": ...} with a bearer token."""

    def __init__(self) -> None:
        self.url = os.environ["SMS_WEBHOOK_URL"]
        if not self.url.startswith("https://"):
            raise RuntimeError("SMS_WEBHOOK_URL must use https")
        self.token = os.getenv("SMS_WEBHOOK_TOKEN", "")

    def send(self, destination: str, subject: str, body: str) -> None:
        request = Request(self.url, data=json.dumps({"to": destination, "message": body}).encode(),
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {self.token}"}, method="POST")
        with urlopen(request, timeout=20) as response:  # noqa: S310 - https enforced above
            if response.status >= 300:
                raise RuntimeError(f"SMS gateway returned {response.status}")


def channels_from_env() -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    if os.getenv("SMTP_HOST"):
        channels["email"] = SmtpEmail()
    if os.getenv("TWILIO_ACCOUNT_SID"):
        channels["phone"] = TwilioSms()
    elif os.getenv("SMS_WEBHOOK_URL"):
        channels["phone"] = WebhookSms()
    return channels


SUBJECTS = {"otp": "Your Trove verification code", "owner_alert": "Important: Trove legacy vault alert",
            "nominee_alert": "Trove legacy vault update", "reminder": "Trove: please confirm you are well"}


def deliver_pending(service: TroveService, channels: dict[str, Channel] | None = None,
                    limit: int = 50) -> dict[str, int]:
    """Send queued messages. Safe to call from request handlers and the worker."""
    channels = channels if channels is not None else channels_from_env()
    sent = failed = 0
    with service._connect() as db:
        rows = db.execute(
            """SELECT id, channel, destination_enc, body, purpose, attempts FROM legacy_outbox
               WHERE status = 'pending' AND attempts < ? ORDER BY created_at LIMIT ?""",
            (MAX_ATTEMPTS, limit)).fetchall()
    for row in rows:
        channel = channels.get(row["channel"])
        error = None
        if channel is None:
            error = f"No {row['channel']} provider configured"
        else:
            try:
                channel.send(service.decrypt_field(row["destination_enc"]),
                             SUBJECTS.get(row["purpose"], "Trove notification"),
                             service.decrypt_field(row["body"]))
            except Exception as exc:  # provider errors must not break callers
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
        with service._connect() as db:
            if error is None:
                sent += 1
                db.execute(
                    """UPDATE legacy_outbox SET status = 'sent', sent_at = ?, body = '',
                       destination_enc = NULL, attempts = attempts + 1 WHERE id = ?""",
                    (now(), row["id"]))
            else:
                failed += 1
                attempts = row["attempts"] + 1
                db.execute(
                    """UPDATE legacy_outbox SET attempts = ?, last_error = ?,
                       status = CASE WHEN ? >= ? THEN 'failed' ELSE 'pending' END WHERE id = ?""",
                    (attempts, error, attempts, MAX_ATTEMPTS, row["id"]))
                log.warning("notification delivery failed", extra={"outbox_id": row["id"],
                                                                   "error": error})
    return {"sent": sent, "failed": failed}
