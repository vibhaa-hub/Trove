"""Discovery connectors (Trove.txt mechanisms 2-4).

Implemented, working connectors:

* ``imap``   any mailbox over IMAPS (Gmail / Outlook.com / Yahoo / Zoho / ISP)
             using an app password. Read-only: the folder is opened with
             ``readonly=True`` and messages are fetched with BODY.PEEK so
             nothing is marked as read. Headers are fetched first; only
             messages whose sender/subject look financial are downloaded.
             The password is used for this one scan and never stored.
* ``folder`` device / USB / NAS scan of a directory mounted under
             ``TROVE_IMPORT_ROOT`` (path traversal blocked, size-limited).

Every connector yields ``(descriptor, text, hint)`` items into the same
extraction pipeline as uploaded exports, so raw content is never stored.
OAuth connectors (Gmail API, Microsoft Graph, Google Drive, Dropbox) plug in
the same way; they need an OAuth client registered by the operator.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import os
import re
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from trove_agent.discovery.bootstrap import INSTITUTION_PATTERNS, CUE_PATTERNS, DiscoveryBootstrap, _message_text, _norm
from trove_agent.core.store import TroveService, ValidationError

MAX_IMAP_MESSAGES = 500
MAX_FOLDER_FILES = 2000
MAX_FOLDER_FILE_BYTES = 3 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".csv", ".eml", ".json", ".pdf"}
FINANCIAL_HINT = re.compile(
    r"statement|premium|policy|folio|account|deposit|dividend|contract note|pension|"
    r"provident|insurance|invest|demat|loan|emi|credit card|tax|portfolio|nav|sip|wallet",
    re.IGNORECASE)

KNOWN_IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com", "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.co.in": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com",
    "zoho.com": "imap.zoho.com",
}


def looks_financial(header_text: str) -> bool:
    haystack = _norm(header_text)
    return bool(FINANCIAL_HINT.search(header_text)
                or any(p.search(haystack) for _, _, p in INSTITUTION_PATTERNS)
                or any(p.search(haystack) for p in CUE_PATTERNS.values()))


class ImapConnector:
    id = "imap"
    source_type = "email_export"

    def __init__(self, factory: Callable[..., Any] | None = None) -> None:
        self._factory = factory or (lambda host, port: imaplib.IMAP4_SSL(
            host, port, ssl_context=ssl.create_default_context(), timeout=30))

    def scan(self, params: dict[str, Any]) -> tuple[str, Iterator[tuple[str, str, str]]]:
        username = params.get("username")
        password = params.get("app_password")
        if not isinstance(username, str) or "@" not in username:
            raise ValidationError("username (the mailbox email address) is required")
        if not isinstance(password, str) or not password:
            raise ValidationError("app_password is required (never your normal password)")
        host = params.get("host") or KNOWN_IMAP_HOSTS.get(username.split("@")[-1].casefold())
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", host):
            raise ValidationError("host is required for this email provider (e.g. imap.example.com)")
        port = int(params.get("port") or 993)
        folder = str(params.get("folder") or "INBOX")
        days = max(1, min(int(params.get("since_days") or 730), 3650))
        limit = max(1, min(int(params.get("max_messages") or MAX_IMAP_MESSAGES), MAX_IMAP_MESSAGES))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")

        client = self._factory(host, port)
        try:
            client.login(username, password)
        except imaplib.IMAP4.error as exc:
            raise ValidationError("Mailbox sign-in failed. Use an app password and enable IMAP.") from exc
        status, _ = client.select(f'"{folder}"', readonly=True)
        if status != "OK":
            client.logout()
            raise ValidationError(f"Folder {folder!r} not found")
        status, data = client.search(None, "SINCE", since)
        ids = data[0].split()[-limit * 4:] if status == "OK" and data and data[0] else []
        label = f"IMAP {username.split('@')[-1]} {folder} since {since}"

        def items() -> Iterator[tuple[str, str, str]]:
            try:
                picked = 0
                for message_id in reversed(ids):
                    if picked >= limit:
                        break
                    status, header = client.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                    if status != "OK" or not header or not isinstance(header[0], tuple):
                        continue
                    header_text = header[0][1].decode("utf-8", "replace")
                    if not looks_financial(header_text):
                        continue  # data minimisation: skip personal mail entirely
                    status, full = client.fetch(message_id, "(BODY.PEEK[])")
                    if status != "OK" or not full or not isinstance(full[0], tuple):
                        continue
                    picked += 1
                    message = email.message_from_bytes(full[0][1], policy=email.policy.compat32)
                    sender = str(message.get("From", ""))[:120]
                    subject = str(message.get("Subject", ""))[:120]
                    body, attachments = _message_text(message)
                    domain = re.search(r"@([A-Za-z0-9.-]+)", sender)
                    yield (f"Email from {domain.group(1) if domain else sender[:40]} · "
                           f"{str(message.get('Date', ''))[:16]} · “{subject[:70]}”",
                           body, f"{sender} {subject} {' '.join(attachments)}")
            finally:
                try:
                    client.logout()
                except Exception:
                    pass

        return label, items()


class FolderConnector:
    id = "folder"
    source_type = "cloud_export"

    def __init__(self, service: TroveService) -> None:
        self.service = service

    @staticmethod
    def root() -> Path:
        root = os.getenv("TROVE_IMPORT_ROOT")
        if not root:
            raise ValidationError("Folder scanning is disabled: set TROVE_IMPORT_ROOT on the server")
        return Path(root).resolve()

    def scan(self, params: dict[str, Any]) -> tuple[str, Iterator[tuple[str, str, str]]]:
        root = self.root()
        relative = str(params.get("path") or ".")
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValidationError("Path must stay inside the import root")
        if not target.is_dir():
            raise ValidationError("Folder not found under the import root")

        def items() -> Iterator[tuple[str, str, str]]:
            count = 0
            for path in sorted(target.rglob("*")):
                if count >= MAX_FOLDER_FILES:
                    break
                if path.is_symlink() or not path.is_file():
                    continue  # never follow links out of the root
                count += 1
                rel = path.relative_to(root).as_posix()
                text = ""
                if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= MAX_FOLDER_FILE_BYTES:
                    try:
                        text = self.service._document_text(path.name, "", path.read_bytes())
                    except Exception:
                        text = ""
                yield (f"Device file {rel[-90:]}", text, f"file:{rel}")

        return f"Folder {relative}", items()


class ConnectorHub:
    def __init__(self, service: TroveService, discovery: DiscoveryBootstrap,
                 imap_factory: Callable[..., Any] | None = None) -> None:
        self.discovery = discovery
        self.connectors = {"imap": ImapConnector(imap_factory), "folder": FolderConnector(service)}

    def catalog(self) -> list[dict[str, Any]]:
        folder_enabled = bool(os.getenv("TROVE_IMPORT_ROOT"))
        return [
            {"id": "imap", "name": "Email mailbox (IMAP)", "status": "available",
             "providers": sorted(set(KNOWN_IMAP_HOSTS.values())),
             "needs": ["username", "app_password", "host (optional for known providers)"],
             "stores_credentials": False},
            {"id": "folder", "name": "Device / USB / NAS folder",
             "status": "available" if folder_enabled else "disabled (set TROVE_IMPORT_ROOT)",
             "needs": ["path relative to import root"], "stores_credentials": False},
            {"id": "gmail_api", "name": "Gmail API (OAuth)", "status": "planned",
             "needs": ["Google OAuth client"]},
            {"id": "microsoft_graph", "name": "Outlook / OneDrive (Microsoft Graph)",
             "status": "planned", "needs": ["Entra ID app registration"]},
            {"id": "google_drive", "name": "Google Drive (OAuth)", "status": "planned",
             "needs": ["Google OAuth client"]},
        ]

    def run(self, case_id: str, connector_id: str, params: dict[str, Any]) -> dict[str, Any]:
        connector = self.connectors.get(connector_id)
        if connector is None:
            raise ValidationError(f"Unknown or unavailable connector: {connector_id}")
        label, items = connector.scan(params)
        digest = hashlib.sha256(
            f"{connector_id}:{label}:{datetime.now(timezone.utc).date()}".encode()).hexdigest()
        result = self.discovery.scan_items(case_id, connector.source_type, label, items, digest)
        result["connector"] = connector_id
        return result
