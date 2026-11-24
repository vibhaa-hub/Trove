"""Discovery bootstrap: build an asset picture from whatever the family has.

Families often start with almost nothing: a phone, an email address, one bank
statement. This module turns any one or two of those into a growing set of
corroborated leads:

  seeds (name / DOB / email / phone / ID)      -> a starting plan
  email export (.eml / .mbox)                   -> sender + subject + body signals
  phone SMS backup (.xml / .csv / .txt)         -> bank / insurer / broker alerts
  cloud export (.zip) or file listing (.txt)    -> file names + text files
  one or two documents                          -> recurring debits, references

Every item is parsed in memory. Only structured signals (institution, asset
type, masked reference, sender/date descriptor) are stored, never raw text.
Each lead is cross-checked across sources; leads seen in several independent
sources get higher confidence. A coverage map shows which asset classes are
still missing and where to look next.
"""

from __future__ import annotations

import base64
import csv
import email
import email.policy
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable

from trove_agent.agents.rules import mask_reference
from trove_agent.core.db import IntegrityError as DBIntegrityError
from trove_agent.core.store import ConflictError, NotFoundError, TroveService, ValidationError, new_id, now

MAX_UPLOAD = 5 * 1024 * 1024
MAX_ITEMS = 5000
ZIP_MAX_ENTRIES = 1000
ZIP_MAX_TOTAL = 50 * 1024 * 1024
ZIP_MAX_FILE = 3 * 1024 * 1024
ZIP_MAX_RATIO = 100

CATEGORIES = {
    "bank_account": "Bank accounts & deposits",
    "insurance": "Insurance & annuities",
    "investment": "Investments (shares, funds, brokerage)",
    "pension": "Pension, provident & employer benefits",
    "property": "Property & land",
    "crypto": "Crypto & digital wallets",
    "business": "Business holdings",
    "trust": "Trusts",
    "digital_account": "Online accounts (email, cloud, subscriptions)",
    "liability": "Loans, credit cards & liabilities",
}

# name, default category, aliases (names, SMS sender IDs, email domains)
INSTITUTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("State Bank of India", "bank_account", ("state bank of india", "sbiinb", "sbipsg", "sbi.co.in", "onlinesbi")),
    ("HDFC Bank", "bank_account", ("hdfc bank", "hdfcbk", "hdfcbank")),
    ("ICICI Bank", "bank_account", ("icici bank", "icicib", "icicibank.com")),
    ("Axis Bank", "bank_account", ("axis bank", "axisbk", "axisbank.com")),
    ("Kotak Mahindra Bank", "bank_account", ("kotak mahindra bank", "kotakb", "kotak.com")),
    ("Punjab National Bank", "bank_account", ("punjab national bank", "pnbsms", "pnbindia.in")),
    ("Bank of Baroda", "bank_account", ("bank of baroda", "bobtxn", "bankofbaroda")),
    ("Canara Bank", "bank_account", ("canara bank", "canbnk", "canarabank")),
    ("IDFC First Bank", "bank_account", ("idfc first", "idfcfb", "idfcfirstbank")),
    ("India Post", "bank_account", ("india post", "post office savings", "ippb")),
    ("Barclays", "bank_account", ("barclays",)),
    ("HSBC", "bank_account", ("hsbc",)),
    ("Lloyds Bank", "bank_account", ("lloyds bank", "lloydsbank")),
    ("NatWest", "bank_account", ("natwest",)),
    ("Santander", "bank_account", ("santander",)),
    ("Nationwide", "bank_account", ("nationwide building society",)),
    ("Chase", "bank_account", ("chase.com", "jpmorgan chase", "chase bank")),
    ("Bank of America", "bank_account", ("bank of america", "bankofamerica")),
    ("Wells Fargo", "bank_account", ("wells fargo", "wellsfargo")),
    ("Citibank", "bank_account", ("citibank", "citi.com")),
    ("LIC", "insurance", ("life insurance corporation", "licind", "licindia", " lic ")),
    ("HDFC Life", "insurance", ("hdfc life", "hdfclife")),
    ("ICICI Prudential Life", "insurance", ("icici prudential life", "iciciprulife", "ipru")),
    ("SBI Life", "insurance", ("sbi life", "sbilife")),
    ("Max Life", "insurance", ("max life", "maxlifeinsurance")),
    ("Tata AIA", "insurance", ("tata aia", "tataaia")),
    ("Bajaj Allianz", "insurance", ("bajaj allianz", "bajajallianz")),
    ("Star Health", "insurance", ("star health", "starhealth")),
    ("Aviva", "insurance", ("aviva",)),
    ("Legal & General", "insurance", ("legal & general", "legalandgeneral")),
    ("Prudential", "insurance", ("prudential",)),
    ("MetLife", "insurance", ("metlife",)),
    ("Zerodha", "investment", ("zerodha", "zerodh")),
    ("Groww", "investment", ("groww",)),
    ("Upstox", "investment", ("upstox",)),
    ("Angel One", "investment", ("angel one", "angelbroking", "angelone")),
    ("CAMS (mutual fund registrar)", "investment", ("camsonline", "cams mf", "camsmf", "computer age management")),
    ("KFintech (mutual fund registrar)", "investment", ("kfintech", "karvy")),
    ("NSDL (depository)", "investment", ("nsdl",)),
    ("CDSL (depository)", "investment", ("cdsl",)),
    ("Fidelity", "investment", ("fidelity",)),
    ("Vanguard", "investment", ("vanguard",)),
    ("Charles Schwab", "investment", ("schwab",)),
    ("Hargreaves Lansdown", "investment", ("hargreaves lansdown", "hl.co.uk")),
    ("EPFO (provident fund)", "pension", ("epfo", "epfindia", "employees provident fund", "uan ")),
    ("NPS (National Pension System)", "pension", ("national pension system", " nps ", "npscra", "pran")),
    ("Coinbase", "crypto", ("coinbase",)),
    ("Binance", "crypto", ("binance",)),
    ("WazirX", "crypto", ("wazirx",)),
    ("CoinDCX", "crypto", ("coindcx",)),
    ("Kraken", "crypto", ("kraken.com",)),
    ("Google account", "digital_account", ("accounts.google.com", "google account")),
    ("Apple ID / iCloud", "digital_account", ("apple id", "icloud")),
    ("Microsoft account", "digital_account", ("microsoft account", "onedrive")),
    ("Dropbox", "digital_account", ("dropbox",)),
)

CUES: dict[str, tuple[str, ...]] = {
    "bank_account": ("a/c", "account no", "savings account", "fixed deposit", " fd ", "credited to",
                     "debited from", "available balance", "avl bal", "passbook", "term deposit",
                     "recurring deposit", "sort code", "ifsc"),
    "insurance": ("premium", "policy no", "policy number", "sum assured", "life insurance",
                  "insurance policy", "annuity", "maturity value", "nominee"),
    "investment": ("mutual fund", "folio", " sip ", "sip installment", "demat", "brokerage",
                   "contract note", "portfolio", "dividend", "nav ", "units allotted", "shares",
                   "consolidated account statement"),
    "pension": ("pension", "provident fund", " pf ", " uan", "superannuation", "gratuity",
                "retirement", "401(k)", " ppf", "salary credited", "salary credit"),
    "property": ("property tax", "title deed", "sale deed", "land registry", "khata", "mutation",
                 "plot no", "survey no", "municipal", "society maintenance", "registry office"),
    "crypto": ("bitcoin", "ethereum", "wallet address", "seed phrase", "usdt", "crypto",
               "private key", "keystore"),
    "business": ("shareholder", "director", "partnership deed", "gst return", "company registry",
                 "llp", "annual return"),
    "trust": ("trust deed", "trustee", "settlor", "beneficiary of the trust"),
    "liability": ("emi", "loan account", "outstanding", "credit card", "card ending", "overdue",
                  "home loan", "personal loan", "minimum amount due"),
}

FILE_NAME_CUES: dict[str, tuple[str, ...]] = {
    "trust": ("trust",),
    "property": ("deed", "property", "khata", "registry", "lease", "mortgage"),
    "insurance": ("policy", "insurance", "premium", "lic_"),
    "investment": ("demat", "cas_", "portfolio", "folio", "contract_note", "mutual"),
    "pension": ("pension", "epf", "uan", "pf_", "gratuity", "ppf"),
    "bank_account": ("statement", "passbook", "fd_", "deposit"),
    "crypto": ("wallet", "keystore", "seed", ".dat"),
    "business": ("gst", "partnership", "moa", "aoa", "shareholding"),
}
WILL_NAMES = ("will", "testament", "codicil")

ACCOUNT_PATTERN = re.compile(
    r"\b(?:a/c|acct|account|card|policy|folio|loan|pran|uan)\s*(?:no\.?|number|ending|#)?\s*"
    r"[:#-]?\s*((?:[Xx*]{2,}|\d)[\dXx*-]{2,})", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?\d{10}(?!\d)")


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", text.casefold()) + " "


def _word_pattern(terms: Iterable[str]) -> re.Pattern[str]:
    # whole-word match so "emi" does not fire inside "premium"
    alternatives = "|".join(re.escape(t.strip()) for t in terms)
    return re.compile(rf"(?<![a-z])(?:{alternatives})(?![a-z])")


INSTITUTION_PATTERNS = [(name, category, _word_pattern(aliases))
                        for name, category, aliases in INSTITUTIONS]
CUE_PATTERNS = {category: _word_pattern(cues) for category, cues in CUES.items()}


class SignalExtractor:
    def extract(self, text: str, descriptor: str, subject_name: str | None = None,
                extra_hint: str = "") -> list[dict[str, Any]]:
        haystack = _norm(f"{extra_hint} {text}")
        institutions = [(name, category) for name, category, pattern in INSTITUTION_PATTERNS
                        if pattern.search(haystack)]
        cue_types = [category for category, pattern in CUE_PATTERNS.items()
                     if pattern.search(haystack)]
        refs = list(dict.fromkeys(
            r for r in (mask_reference(m.group(1)) for m in ACCOUNT_PATTERN.finditer(text)) if r
        ))[:5]
        if re.search(r"(?<![a-z])salary(?![a-z])", haystack) and "pension" not in cue_types:
            cue_types.append("pension")
        signals = []
        covered: set[str] = set()
        for name, default in institutions[:3]:
            signals.append(self._signal(default, name, refs, descriptor))
            covered.add(default)
            if default == "bank_account" and "liability" in cue_types:
                # loan / card held at the bank itself
                signals.append(self._signal("liability", name, refs, descriptor))
                covered.add("liability")
        # Cues with no matching institution (e.g. a premium debit to an unnamed
        # payee) still become leads that another source can corroborate.
        for asset_type in cue_types:
            if asset_type in covered:
                continue
            if asset_type == "bank_account" and (institutions or not refs):
                continue  # too noisy without an account reference
            signals.append(self._signal(asset_type, None, refs if not institutions else [],
                                        descriptor))
        name_hit = bool(subject_name and subject_name.casefold() in haystack)
        for signal in signals:
            signal["subject_name_found"] = name_hit
        return signals

    @staticmethod
    def _signal(asset_type: str, institution: str | None, refs: list[str],
                descriptor: str) -> dict[str, Any]:
        return {"asset_type": asset_type, "institution": institution,
                "masked_refs": refs, "evidence": descriptor[:140]}

    def from_file_name(self, path: str) -> list[dict[str, Any]]:
        name = PurePosixPath(path).name.casefold()
        signals = []
        if any(re.search(rf"(^|[^a-z]){w}([^a-z]|$)", name) for w in WILL_NAMES):
            signals.append(self._signal("trust", None, [], f"Possible will/testament file: {name[:80]}"))
        for asset_type, cues in FILE_NAME_CUES.items():
            if any(cue in name for cue in cues):
                signals.append(self._signal(asset_type, None, [], f"Cloud file name: {name[:80]}"))
        hint = self.extract("", f"Cloud file name: {name[:80]}", extra_hint=name.replace("_", " "))
        return signals + [s for s in hint if s["institution"]]


# ----------------------------------------------------------------- parsers
def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _message_text(message: email.message.Message) -> tuple[str, list[str]]:
    parts, attachments = [], []
    for part in message.walk() if message.is_multipart() else [message]:
        filename = part.get_filename()
        if filename:
            attachments.append(filename)
            continue
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html"):
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            text = _decode(payload)
            if ctype == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append(text[:50_000])
    return "\n".join(parts), attachments


def parse_email_export(file_name: str, data: bytes) -> Iterable[tuple[str, str, str]]:
    """Yield (descriptor, text, hint) per message for .eml or .mbox exports."""
    if file_name.lower().endswith(".eml"):
        chunks = [data]
    else:
        chunks = [c for c in re.split(rb"(?m)^From .*\r?\n", data) if c.strip()]
    for chunk in chunks[:MAX_ITEMS]:
        message = email.message_from_bytes(chunk, policy=email.policy.compat32)
        sender = str(message.get("From", ""))[:120]
        subject = str(message.get("Subject", ""))[:120]
        date = str(message.get("Date", ""))[:40]
        body, attachments = _message_text(message)
        domain_match = re.search(r"@([A-Za-z0-9.-]+)", sender)
        hint = f"{sender} {subject} {' '.join(attachments)} {domain_match.group(1) if domain_match else ''}"
        sender_label = domain_match.group(1) if domain_match else sender[:40]
        yield (f"Email from {sender_label} · {date[:16]} · “{subject[:70]}”", body, hint)


def parse_sms_export(file_name: str, data: bytes) -> Iterable[tuple[str, str, str]]:
    """Yield SMS items from SMS Backup & Restore XML, CSV, or plain text."""
    lower = file_name.lower()
    if lower.endswith(".xml"):
        try:
            from defusedxml import ElementTree as SafeET
        except ImportError as exc:
            raise ValidationError("XML parsing requires defusedxml (pip install -r requirements.txt)") from exc
        try:
            root = SafeET.fromstring(data)
        except Exception as exc:
            raise ValidationError("Could not parse SMS XML export") from exc
        for index, node in enumerate(root.iter("sms")):
            if index >= MAX_ITEMS:
                break
            sender = node.get("address", "")[:40]
            body = node.get("body", "")[:5000]
            date = node.get("readable_date") or node.get("date", "")
            if date.isdigit():
                date = datetime.fromtimestamp(int(date) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            yield (f"SMS from {sender} · {date[:16]}", body, sender)
        return
    text = _decode(data)
    if lower.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        for index, row in enumerate(reader):
            if index >= MAX_ITEMS:
                break
            low = {k.casefold().strip(): (v or "") for k, v in row.items() if k}
            sender = (low.get("address") or low.get("sender") or low.get("from") or "")[:40]
            body = (low.get("body") or low.get("message") or low.get("text") or "")[:5000]
            date = (low.get("date") or low.get("time") or "")[:20]
            yield (f"SMS from {sender} · {date}", body, sender)
        return
    for index, line in enumerate(l for l in text.splitlines() if l.strip()):
        if index >= MAX_ITEMS:
            break
        sender = line.split(":", 1)[0][:40] if ":" in line[:50] else "unknown"
        yield (f"SMS/text line from {sender}", line[:5000], sender)


def parse_cloud_export(file_name: str, data: bytes,
                       read_text) -> Iterable[tuple[str, str, str, str | None]]:
    """Yield (descriptor, text, hint, path) for a ZIP export or a .txt file listing."""
    if file_name.lower().endswith(".zip"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValidationError("Not a valid ZIP file") from exc
        entries = [e for e in archive.infolist() if not e.is_dir()]
        if len(entries) > ZIP_MAX_ENTRIES:
            raise ValidationError(f"ZIP has more than {ZIP_MAX_ENTRIES} files")
        total = 0
        for entry in entries:
            path = entry.filename.replace("\\", "/")
            if path.startswith("/") or ".." in PurePosixPath(path).parts:
                continue  # never trust archive paths
            text = ""
            suffix = PurePosixPath(path).suffix.lower()
            if suffix in (".txt", ".csv", ".eml", ".json", ".pdf") and entry.file_size <= ZIP_MAX_FILE:
                if entry.compress_size and entry.file_size / max(entry.compress_size, 1) > ZIP_MAX_RATIO:
                    continue  # zip-bomb guard
                total += entry.file_size
                if total > ZIP_MAX_TOTAL:
                    raise ValidationError("ZIP expands beyond the 50 MB scanning limit")
                with archive.open(entry) as handle:
                    raw = handle.read(ZIP_MAX_FILE + 1)[:ZIP_MAX_FILE]
                try:
                    text = read_text(PurePosixPath(path).name, raw)
                except ValidationError:
                    text = ""
            yield (f"Cloud file {path[-90:]}", text, path, path)
        return
    for line in _decode(data).splitlines()[:MAX_ITEMS]:
        if line.strip():
            yield (f"Cloud listing {line.strip()[-90:]}", "", line.strip(), line.strip())


# ------------------------------------------------------------- playbooks
def _region(case: Any) -> str:
    """Country code for a case (set at creation; detected for older free-text cases)."""
    from trove_agent.core.jurisdictions import detect_country

    keys = case.keys() if hasattr(case, "keys") else []
    return (case["country"] if "country" in keys and case["country"] else
            detect_country(case["jurisdiction"]))


GENERIC_LOOK = {
    "bank_account": "Look for 'A/c XX1234 credited/debited' SMS, passbooks, cheque books, and interest lines in tax returns.",
    "insurance": "Scan bank statements for recurring premium debits (NACH/ECS/auto-debit) and tax-deduction proofs.",
    "investment": "Search email for 'contract note', 'folio', 'SIP', 'dividend'; check bank statements for SIP debits and dividend credits.",
    "pension": "Ask former employers (salary-credit SMS show who they were) about provident fund, gratuity, pension and group life cover.",
    "property": "Look for property-tax receipts, society maintenance bills, home-loan statements and sale deeds.",
    "crypto": "Look for exchange emails, authenticator apps, hardware wallets, and paper notes with 12/24-word phrases. Never share a seed phrase with anyone, including Trove.",
    "business": "Check for GST/company filings, director notices and partnership deeds in email and documents.",
    "trust": "Search cloud storage and email for 'trust deed', 'will', 'testament'; ask the family lawyer or accountant.",
    "digital_account": "List email, cloud, and subscription accounts; recurring card charges reveal subscriptions to cancel.",
    "liability": "Loan EMIs and credit-card statements must be reported; lenders may also hold loan-protection insurance.",
}

def _tracing(region: str, category: str) -> list[str]:
    from trove_agent.core.jurisdictions import profile

    return list(profile(region).official_tracing.get(category, ()))


ACCESS_RECOVERY = [
    {"title": "Phone", "steps": [
        "Keep the SIM active (pay the bill) — bank and insurer OTPs and alerts still arrive there.",
        "Do not factory-reset the handset. If it is unlocked, export SMS with a backup app (XML/CSV) and upload it here.",
        "Ask the mobile operator about transferring the number to the legal heir with the death certificate."]},
    {"title": "Google / Gmail", "steps": [
        "If the person set up Inactive Account Manager, nominated contacts receive data automatically.",
        "Otherwise use Google's 'request regarding a deceased user's account' form (death certificate + your ID).",
        "If you can sign in lawfully, use Google Takeout to export Mail as .mbox and upload it here."]},
    {"title": "Apple / iCloud", "steps": [
        "A Legacy Contact with the access key can request data with the death certificate.",
        "Without one, Apple typically requires a court order."]},
    {"title": "Microsoft / Outlook / OneDrive", "steps": [
        "Use Microsoft's next-of-kin process for Outlook.com / OneDrive accounts.",
        "Export mail (.eml/.mbox) or download OneDrive folders as ZIP and upload them here."]},
    {"title": "Ground rules", "steps": [
        "Never guess passwords or bypass device locks — it can breach computer-misuse law and provider terms.",
        "Provider processes change; check each provider's current help page before applying."]},
]


MONEY_LINE = re.compile(
    r"(rs\.?|inr|\u20b9|\$|\u00a3|\u20ac|usd|gbp)\s?\d|\d+\.\d{2}\b|"
    r"(?<![a-z])(dr|cr|debit|credit|debited|credited|paid|payment|nach|ecs|emi|sip)(?![a-z])",
    re.IGNORECASE)


def encoder_signals(lines: list[tuple[str, str]], limit: int = 400) -> list[dict[str, Any]]:
    """System-1 fallback: classify money lines no rule matched, with a local encoder.

    Only transaction-looking lines are embedded; results are low-confidence,
    institution-less leads that need corroboration from another source.
    """
    candidates = [(d, t.strip()) for d, t in lines if t.strip() and MONEY_LINE.search(t)][:limit]
    if not candidates:
        return []
    try:
        from trove_agent.agents.encoders import classify

        labels = classify([t for _, t in candidates])
    except Exception:  # encoder problems must never break a scan
        return []
    out = []
    for (descriptor, _), label in zip(candidates, labels):
        if label:
            out.append({"asset_type": label[0], "institution": None, "masked_refs": [],
                        "evidence": f"{descriptor} (encoder match {label[1]:.2f})"[:140]})
    return out


class DiscoveryBootstrap:
    def __init__(self, service: TroveService) -> None:
        self.service = service
        self.extractor = SignalExtractor()
        with service._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_seeds (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    masked TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, kind, value_hash)
                );
                CREATE TABLE IF NOT EXISTS discovery_sources (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    source_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    items_scanned INTEGER NOT NULL,
                    signals_found INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS discovery_leads (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    lead_key TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    institution TEXT,
                    masked_refs_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    source_types_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    mentions INTEGER NOT NULL,
                    subject_name_found INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'new',
                    asset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id, lead_key)
                );
                CREATE TABLE IF NOT EXISTS discovery_profile (
                    case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
                    have_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------ seeds
    def add_seeds(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        have = data.get("have", [])
        valid_have = {"death_certificate", "name_dob", "national_id", "email_address",
                      "email_access", "phone_number", "phone_access", "cloud_access", "documents"}
        if not isinstance(have, list) or not set(have) <= valid_have:
            raise ValidationError(f"have must be a list drawn from {sorted(valid_have)}")
        seeds: list[tuple[str, str, str]] = []
        for kind, key, masker in (
            ("email", "emails", lambda v: f"{v[:1]}***@{v.split('@')[-1]}"),
            ("phone", "phones", lambda v: f"***{v[-3:]}"),
            ("employer", "employers", lambda v: v[:60]),
            ("national_id", "national_id_last4", lambda v: f"***{v[-4:]}"),
        ):
            values = data.get(key, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                raise ValidationError(f"{key} must be a list of strings")
            for value in values[:10]:
                if not isinstance(value, str) or not value.strip() or len(value) > 200:
                    continue
                clean = value.strip().casefold() if kind == "email" else value.strip()
                if kind == "email" and not EMAIL_PATTERN.fullmatch(clean):
                    raise ValidationError(f"Invalid email: {masker(clean)}")
                if kind == "phone":
                    clean = re.sub(r"[^\d+]", "", clean)
                purpose = {"email": "nominee_email", "phone": "nominee_phone"}.get(kind, f"seed_{kind}")
                # keyed blind index: links family seeds to nominee contacts in the graph
                seeds.append((kind, masker(clean), self.service.blind_index(purpose, clean)))
        with self.service._connect() as db:
            self.service._case_row(db, case_id)
            if have:
                db.execute(
                    """INSERT INTO discovery_profile (case_id, have_json, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(case_id) DO UPDATE SET have_json = excluded.have_json,
                       updated_at = excluded.updated_at""",
                    (case_id, json.dumps(sorted(set(have))), now()),
                )
            for kind, masked, value_hash in seeds:
                db.execute(
                    """INSERT OR IGNORE INTO discovery_seeds
                       (id, case_id, kind, masked, value_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                    (new_id(), case_id, kind, masked, value_hash, now()),
                )
                if kind == "employer":
                    self._merge_lead(db, case_id, "seed", "seed", {
                        "asset_type": "pension", "institution": f"Employer: {masked}",
                        "masked_refs": [], "subject_name_found": True,
                        "evidence": "Family-reported employer (PF, gratuity, pension, group life cover)"})
            self.service._audit(db, case_id, "discovery.seeds_added",
                                {"have": sorted(set(have)), "seed_count": len(seeds)})
        return self.overview(case_id)

    # ----------------------------------------------------------- ingest
    def ingest(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        source_type = data.get("source_type")
        file_name = data.get("file_name")
        encoded = data.get("content_base64")
        allowed = {
            "email_export": (".eml", ".mbox", ".txt"),
            "sms_export": (".xml", ".csv", ".txt"),
            "cloud_export": (".zip", ".txt"),
            "document": (".txt", ".eml", ".csv", ".json", ".pdf"),
        }
        if source_type not in allowed:
            raise ValidationError(f"source_type must be one of {sorted(allowed)}")
        if not isinstance(file_name, str) or not file_name.lower().endswith(allowed[source_type]):
            raise ValidationError(f"{source_type} accepts {', '.join(allowed[source_type])}")
        if not isinstance(encoded, str):
            raise ValidationError("content_base64 is required")
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValidationError("content_base64 must be valid base64") from exc
        if not content or len(content) > MAX_UPLOAD:
            raise ValidationError("File must be between 1 byte and 5 MB")
        digest = hashlib.sha256(content).hexdigest()
        with self.service._connect() as db:
            case = self.service._case_row(db, case_id)
            if db.execute("SELECT 1 FROM discovery_sources WHERE case_id = ? AND sha256 = ?",
                          (case_id, digest)).fetchone():
                raise ConflictError("This exact file was already scanned for this case")
        subject = case["subject_name"]
        signals: list[dict[str, Any]] = []
        unmatched: list[tuple[str, str]] = []
        items = 0
        read_text = lambda name, raw: self.service._document_text(name, "", raw)  # noqa: E731
        if source_type == "email_export":
            for descriptor, text, hint in parse_email_export(file_name, content):
                items += 1
                signals += self.extractor.extract(text, descriptor, subject, hint)
        elif source_type == "sms_export":
            for descriptor, text, hint in parse_sms_export(file_name, content):
                items += 1
                found = self.extractor.extract(text, descriptor, subject, hint)
                signals += found
                if not found:
                    unmatched.append((descriptor, text))
        elif source_type == "cloud_export":
            for descriptor, text, hint, path in parse_cloud_export(file_name, content, read_text):
                items += 1
                if path:
                    signals += self.extractor.from_file_name(path)
                if text:
                    signals += self.extractor.extract(text, descriptor, subject)
        else:
            category = data.get("category", "other")
            document = self.service.upload_document(
                case_id, {"file_name": file_name, "category": category, "content_base64": encoded})
            items = 1
            text = read_text(file_name, content)
            name_found = subject.casefold() in text.casefold()
            for line_no, line in enumerate(text.splitlines()[:MAX_ITEMS], 1):
                # line-level scan finds every recurring debit in a statement
                if line.strip():
                    found = self.extractor.extract(line, f"{file_name} line {line_no}")
                    for signal in found:
                        signal["subject_name_found"] = name_found
                        signals.append(signal)
                    if not found:
                        unmatched.append((f"{file_name} line {line_no}", line))
            digest = document["sha256"]
        signals += encoder_signals(unmatched)
        return self.record_scan(case_id, source_type, file_name, digest, items, signals)

    def scan_items(self, case_id: str, source_type: str, label: str,
                   items: Iterable[tuple[str, str, str]], digest: str) -> dict[str, Any]:
        """Entry point for connectors: (descriptor, text, hint) tuples."""
        with self.service._connect() as db:
            case = self.service._case_row(db, case_id)
        signals: list[dict[str, Any]] = []
        unmatched: list[tuple[str, str]] = []
        count = 0
        for descriptor, text, hint in items:
            count += 1
            if source_type == "cloud_export" or hint.startswith("file:"):
                signals += self.extractor.from_file_name(hint.removeprefix("file:"))
            found = self.extractor.extract(text, descriptor, case["subject_name"], hint) if text else []
            signals += found
            if text and not found:
                unmatched.append((descriptor, text[:500]))
        signals += encoder_signals(unmatched)
        return self.record_scan(case_id, source_type, label, digest, count, signals)

    def record_scan(self, case_id: str, source_type: str, file_name: str, digest: str,
                    items: int, signals: list[dict[str, Any]]) -> dict[str, Any]:
        source_id = new_id()
        with self.service._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO discovery_sources
                   (id, case_id, source_type, label, sha256, items_scanned, signals_found, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, case_id, source_type, file_name[:120], digest, items, len(signals), now()),
            )
            for signal in signals:
                self._merge_lead(db, case_id, source_id, source_type, signal)
            self.service._audit(db, case_id, "discovery.source_scanned",
                                {"source_type": source_type, "items": items,
                                 "signals": len(signals), "sha256": digest,
                                 "raw_text_stored": False})
        result = self.overview(case_id)
        result["scan"] = {"source_type": source_type, "items_scanned": items,
                          "signals_found": len(signals)}
        return result

    def _merge_lead(self, db: Any, case_id: str, source_id: str,
                    source_type: str, signal: dict[str, Any]) -> None:
        key = f"{signal['asset_type']}|{(signal['institution'] or '').casefold()}"
        row = db.execute("SELECT * FROM discovery_leads WHERE case_id = ? AND lead_key = ?",
                         (case_id, key)).fetchone()
        if row is None:
            db.execute(
                """INSERT INTO discovery_leads
                   (id, case_id, lead_key, asset_type, institution, masked_refs_json,
                    source_ids_json, source_types_json, evidence_json, mentions,
                    subject_name_found, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (new_id(), case_id, key, signal["asset_type"], signal["institution"],
                 json.dumps(signal["masked_refs"]), json.dumps([source_id]),
                 json.dumps([source_type]), json.dumps([signal["evidence"]]),
                 int(signal.get("subject_name_found", False)), now(), now()),
            )
            return
        refs = list(dict.fromkeys(json.loads(row["masked_refs_json"]) + signal["masked_refs"]))[:10]
        sources = list(dict.fromkeys(json.loads(row["source_ids_json"]) + [source_id]))
        types = list(dict.fromkeys(json.loads(row["source_types_json"]) + [source_type]))
        evidence = list(dict.fromkeys(json.loads(row["evidence_json"]) + [signal["evidence"]]))[-8:]
        db.execute(
            """UPDATE discovery_leads SET masked_refs_json = ?, source_ids_json = ?,
               source_types_json = ?, evidence_json = ?, mentions = mentions + 1,
               subject_name_found = CASE WHEN ? > subject_name_found THEN 1 ELSE subject_name_found END,
               updated_at = ? WHERE id = ?""",
            (json.dumps(refs), json.dumps(sources), json.dumps(types), json.dumps(evidence),
             int(signal.get("subject_name_found", False)), now(), row["id"]),
        )

    # ----------------------------------------------------------- review
    def update_lead(self, case_id: str, lead_id: str, data: dict[str, Any]) -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("promote", "dismiss", "reopen"):
            raise ValidationError("decision must be promote, dismiss, or reopen")
        with self.service._connect() as db:
            self.service._case_row(db, case_id)
            row = db.execute("SELECT * FROM discovery_leads WHERE id = ? AND case_id = ?",
                             (lead_id, case_id)).fetchone()
            if row is None:
                raise NotFoundError("Lead not found")
            asset_id = row["asset_id"]
            if decision == "promote" and not asset_id:
                asset_id = new_id()
                refs = json.loads(row["masked_refs_json"])
                db.execute(
                    """INSERT INTO assets (id, case_id, evidence_id, asset_type, institution,
                       masked_reference, description, confidence, status, created_at)
                       VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'needs_review', ?)""",
                    (asset_id, case_id, row["asset_type"], row["institution"],
                     refs[0] if refs else None,
                     f"Discovery lead from {len(json.loads(row['source_ids_json']))} source(s)",
                     self._confidence(row), now()),
                )
            status = {"promote": "promoted", "dismiss": "dismissed", "reopen": "new"}[decision]
            db.execute("UPDATE discovery_leads SET status = ?, asset_id = ?, updated_at = ? WHERE id = ?",
                       (status, asset_id, now(), lead_id))
            self.service._audit(db, case_id, "discovery.lead_reviewed",
                                {"lead_id": lead_id, "decision": decision})
        return self.overview(case_id)

    @staticmethod
    def _confidence(row: Any) -> float:
        types = len(json.loads(row["source_types_json"]))
        sources = len(json.loads(row["source_ids_json"]))
        score = (0.30 + 0.12 * min(sources, 4) + 0.10 * (types - 1)
                 + (0.10 if json.loads(row["masked_refs_json"]) else 0)
                 + (0.08 if row["institution"] else 0)
                 + (0.05 if row["subject_name_found"] else 0)
                 + min(row["mentions"], 10) * 0.01)
        return round(min(score, 0.97), 2)

    # --------------------------------------------------------- overview
    def overview(self, case_id: str) -> dict[str, Any]:
        with self.service._connect() as db:
            case = self.service._case_row(db, case_id)
            profile = db.execute("SELECT have_json FROM discovery_profile WHERE case_id = ?",
                                 (case_id,)).fetchone()
            have = json.loads(profile["have_json"]) if profile else []
            seeds = [dict(r) for r in db.execute(
                "SELECT kind, masked, created_at FROM discovery_seeds WHERE case_id = ?", (case_id,))]
            sources = [dict(r) for r in db.execute(
                """SELECT source_type, label, items_scanned, signals_found, created_at
                   FROM discovery_sources WHERE case_id = ? ORDER BY created_at""", (case_id,))]
            rows = db.execute("SELECT * FROM discovery_leads WHERE case_id = ?", (case_id,)).fetchall()
            documents = {r["category"] for r in db.execute(
                "SELECT category FROM documents WHERE case_id = ?", (case_id,))}
        leads = []
        for row in rows:
            leads.append({
                "id": row["id"],
                "asset_type": row["asset_type"],
                "category_label": CATEGORIES.get(row["asset_type"], row["asset_type"]),
                "institution": row["institution"],
                "masked_refs": json.loads(row["masked_refs_json"]),
                "source_count": len(json.loads(row["source_ids_json"])),
                "source_types": json.loads(row["source_types_json"]),
                "evidence": json.loads(row["evidence_json"]),
                "mentions": row["mentions"],
                "confidence": self._confidence(row),
                "status": row["status"],
                "asset_id": row["asset_id"],
                "next_step": self._lead_next_step(row),
            })
        leads.sort(key=lambda l: (l["status"] == "dismissed", -l["confidence"]))
        region = _region(case)
        coverage = []
        for key, label in CATEGORIES.items():
            active = [l for l in leads if l["asset_type"] == key and l["status"] != "dismissed"]
            coverage.append({
                "category": key, "label": label, "leads": len(active),
                "status": "found" if active else "gap",
                "where_to_look": GENERIC_LOOK[key],
                "official_tracing": _tracing(region, key),
            })
        source_types = {s["source_type"] for s in sources}
        return {
            "case_id": case_id,
            "region": region,
            "have": have,
            "seeds": seeds,
            "sources": sources,
            "leads": leads,
            "coverage": coverage,
            "plan": self._plan(set(have), source_types, documents, leads),
            "access_recovery": ACCESS_RECOVERY,
            "privacy": "Exports are parsed in memory; only institution, asset type, masked "
                       "references and short sender/date descriptors are stored.",
        }

    @staticmethod
    def _lead_next_step(row: Any) -> str:
        institution = row["institution"]
        refs = json.loads(row["masked_refs_json"])
        ref = f" quoting reference {refs[0]}" if refs else ""
        if row["asset_type"] == "liability":
            return f"Notify {institution or 'the lender'}{ref}; ask whether loan-protection insurance applies."
        if institution and institution.startswith("Employer:"):
            return "Write to the employer's HR with the death certificate asking about PF/pension, gratuity, group life cover and final dues."
        if institution:
            return (f"Contact {institution}'s bereavement / deceased-claims team{ref} with the death "
                    "certificate; ask for all accounts held and the nominee on record.")
        return "Corroborate with another source (statement, email, SMS) to identify the institution."

    @staticmethod
    def _plan(have: set[str], sources: set[str], documents: set[str],
              leads: list[dict[str, Any]]) -> list[dict[str, str]]:
        steps: list[dict[str, str]] = []

        def add(priority: str, title: str, why: str, action: str) -> None:
            steps.append({"priority": priority, "title": title, "why": why, "action": action})

        if "death_certificate" not in have and "death_certificate" not in documents:
            add("now", "Obtain several certified death-certificate copies",
                "Every institution needs one before discussing the deceased's accounts.",
                "Request 5-10 certified copies from the registrar.")
        if "phone_access" in have and "sms_export" not in sources:
            add("now", "Export SMS from the phone (highest yield)",
                "Bank, card, insurer and broker alerts arrive by SMS and name the institution and last digits.",
                "Use an SMS backup app to export XML/CSV, then upload as 'Phone SMS export'.")
        elif "phone_number" in have and "phone_access" not in have:
            add("now", "Keep the phone number alive",
                "OTPs and alerts keep arriving; losing the number loses the trail.",
                "Pay the bill; ask the operator about transferring ownership to the legal heir.")
        if "email_access" in have and "email_export" not in sources:
            add("now", "Export the mailbox",
                "Statements, premium reminders and contract notes are usually emailed.",
                "Google Takeout → Mail (.mbox) or save key messages as .eml, then upload as 'Email export'.")
        elif "email_address" in have and "email_access" not in have:
            add("soon", "Start the provider's deceased-user process",
                "Lawful access unlocks the richest source of statements.",
                "See the access-recovery guide below for Google, Apple and Microsoft.")
        if "cloud_access" in have and "cloud_export" not in sources:
            add("soon", "Scan cloud storage",
                "Wills, policies and deeds are often saved as PDFs with telling file names.",
                "Download Drive/OneDrive/Dropbox folders as ZIP (or paste a file listing) and upload as 'Cloud export'.")
        if "document" not in sources and not (documents - {"death_certificate", "identity_document"}):
            add("now", "Upload one bank statement (even a single page)",
                "One statement reveals recurring debits: insurance premiums, SIPs, loan EMIs, cards — each a new lead.",
                "Upload it as 'Document'; every line is scanned for payees.")
        if {"name_dob", "national_id"} & have:
            add("soon", "Run official unclaimed-asset searches",
                "Name + DOB + ID are enough for government and industry tracing services.",
                "Use the 'official tracing' links in the coverage map for your jurisdiction.")
        strong = [l for l in leads if l["status"] == "new" and l["confidence"] >= 0.6]
        if strong:
            add("now", f"Review {len(strong)} high-confidence lead(s)",
                "Corroborated across sources or carries an account reference.",
                "Promote real ones to the asset register, then open a claim tracker.")
        institutions = sorted({l["institution"] for l in leads if l["institution"]
                               and l["status"] != "dismissed"})[:6]
        if institutions:
            add("soon", "Search other sources for the institutions already found",
                "Each institution found in one source can be confirmed—and expanded—in another.",
                "Search email/SMS/cloud for: " + ", ".join(institutions))
        if not steps:
            add("soon", "Fill the gaps", "Every category marked 'gap' still needs a source.",
                "Follow the 'where to look' advice in the coverage map.")
        order = {"now": 0, "soon": 1}
        return sorted(steps, key=lambda s: order[s["priority"]])
