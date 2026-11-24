"""Sample use case for live, hands-on testing (development only).

`python -m trove_agent demo` builds a fresh database with the "Rao family" scenario:

* Owner Asha Rao plans her legacy vault (Plan mode) and is also tracing the assets of
  her late father Vikram Mehta (Discovery mode).
* Nominees are staged at different points so every flow can be finished by hand:
  Ravi and Meera are fully verified, Arjun's ID is waiting for review, and Kiran has only
  an invite code.
* An independent reviewer account (rita) is created.
* Sample files (SMS export, mailbox export, statement, certificates, ID) are written to a
  folder so you can upload them yourself.

All people, numbers and institutions here are fictional sample data.
"""
from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path
from typing import Any

from trove_agent.core import security as sec

OWNER_EMAIL = "asha@demo.test"
OWNER_PASSWORD = "Demo-Owner-Pass-2026"
NOMINEE_PASSWORD = "Demo-Nominee-Pass-2026"
REVIEWER = ("rita", "Demo-Reviewer-Pass-2026")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


SAMPLE_FILES: dict[str, str] = {
    "vikram-sms-export.xml": (
        '<?xml version="1.0" encoding="UTF-8"?>\n<smses count="5">\n'
        '  <sms address="VM-HDFCBK" date="1714600000000" body="Rs 5000.00 debited from A/c XX4321 '
        'towards LIC premium policy 88xx1290. Avl Bal Rs 1,20,450"/>\n'
        '  <sms address="AD-ZERODH" date="1714700000000" body="Contract note for your trades on NSE '
        'dated 02-May has been emailed. Zerodha"/>\n'
        '  <sms address="JM-SBIINB" date="1714800000000" body="Your SBI PPF A/c XX8812 credited with '
        'interest Rs 12,340"/>\n'
        '  <sms address="BW-ICICIP" date="1714900000000" body="SIP of Rs 2000 in ICICI Prudential '
        'Bluechip Fund processed. Folio 77xx31"/>\n'
        '  <sms address="+919812300000" date="1715000000000" body="Dinner at 8? - Ramesh"/>\n'
        '</smses>\n'
    ),
    "vikram-hdfc-statement.txt": (
        "HDFC Bank - Savings Account Statement\nAccount holder: Vikram Mehta\n"
        "Account number: XXXXXXXX4321\nPeriod: 01/04/2024 - 30/04/2024\n\n"
        "02/04 NACH DR LIC PREMIUM              5,000.00\n"
        "05/04 SIP ICICI Prudential MF          2,000.00\n"
        "10/04 NEFT CR SALARY PENSION TATA     48,000.00\n"
        "15/04 ACH DR HDFC LIFE INSURANCE       3,200.00\n"
        "28/04 Grocery store                     1,450.00\n"
    ),
    "vikram-mailbox.mbox": (
        "From alerts@nsdl.co.in Mon May 06 10:00:00 2024\n"
        "From: NSDL e-Services <alerts@nsdl.co.in>\nSubject: Consolidated Account Statement (CAS) for April\n\n"
        "Dear Vikram Mehta, your Consolidated Account Statement for demat account IN30xx1123 "
        "held with Zerodha is attached.\n\n"
        "From noreply@epfindia.gov.in Tue May 07 09:00:00 2024\n"
        "From: EPFO <noreply@epfindia.gov.in>\nSubject: EPF passbook updated\n\n"
        "UAN 10xxxx5521: your EPF passbook has been updated with the interest credit.\n\n"
        "From friend@example.test Wed May 08 18:00:00 2024\n"
        "From: Ramesh <friend@example.test>\nSubject: Cricket on Sunday\n\nAre you coming?\n"
    ),
    "vikram-death-certificate.txt": (
        "GOVERNMENT OF MAHARASHTRA - CERTIFICATE OF DEATH (SAMPLE)\n"
        "Name of deceased: Vikram Mehta\nDate of death: 12/03/2026\nPlace of death: Pune\n"
        "Registration number: SAMPLE-2026-000123\n"
    ),
    "asha-death-certificate.txt": (
        "CERTIFICATE OF DEATH (SAMPLE FOR TESTING ONLY)\nName of deceased: Asha Rao\n"
        "Date of death: 01/11/2026\nRegistration number: SAMPLE-2026-009876\n"
    ),
    "asha-will.txt": (
        "LAST WILL AND TESTAMENT OF ASHA RAO (SAMPLE)\n"
        "I leave my HDFC Bank savings account and LIC policy equally to my children Ravi Rao and "
        "Meera Rao. Meera Rao is appointed executor.\n"
    ),
    "ravi-legal-heir-certificate.txt": (
        "LEGAL HEIR CERTIFICATE (SAMPLE FOR TESTING ONLY)\nIssued by: Tahsildar, Pune\n"
        "Deceased: Asha Rao\nLegal heirs: Kiran Rao (spouse), Ravi Rao (son), Meera Rao (daughter)\n"
    ),
    "probate-grant-sample.txt": (
        "GRANT OF PROBATE (SAMPLE FOR TESTING ONLY)\nHigh Court of Justice, England and Wales\n"
        "Deceased: Sample Person\nExecutor: Sample Executor\n"
    ),
    "kiran-id-card.txt": "NATIONAL ID (SAMPLE)\nName: Kiran Rao\nID number: XXXX-XXXX-5678\nDOB: 1968-07-21\n",
    "kiran-marriage-certificate.txt": "MARRIAGE CERTIFICATE (SAMPLE)\nKiran Rao and Asha Rao, married 1992.\n",
}


def _latest_code(legacy, token: str, channel: str) -> str:
    for message in legacy.outbox(token):
        if message["channel"] == channel and message["purpose"] == "otp":
            found = re.search(r"code: (\d{6})", message["body"])
            if found:
                return found.group(1)
    raise RuntimeError(f"no {channel} code in the dev outbox (is TROVE_NOTIFIER=dev?)")


def _onboard(legacy, case_id: str, nominee: dict[str, Any], email: str, last4: str, dob: str,
             approve: bool) -> str:
    """Take a nominee through invite, OTPs, MFA and identity. Returns their TOTP secret."""
    token = legacy.enroll({"invite_code": nominee["invite_code"], "email": email,
                           "password": NOMINEE_PASSWORD})
    for channel in ("email", "phone"):
        legacy.verify_contact(token, {"channel": channel, "code": _latest_code(legacy, token, channel)})
    secret = legacy.mfa_setup(token)["secret"]
    token = legacy.mfa_confirm(token, {"code": sec.totp_at(secret, sec.current_step() - 1)})
    legacy.submit_identity(token, {"id_last4": last4, "date_of_birth": dob, "file_name": "id-card.txt",
                                   "content_base64": _b64(f"NATIONAL ID (SAMPLE) {nominee['full_name']}")})
    if approve:
        legacy.review_identity(case_id, nominee["id"], {"decision": "approve"}, actor="demo-setup")
    legacy.logout(token)
    return secret


def build(database_url: str, files_dir: Path) -> dict[str, Any]:
    from trove_agent.core.store import TroveService
    from trove_agent.discovery.bootstrap import DiscoveryBootstrap
    from trove_agent.services.legacy import LegacyService
    from trove_agent.services.reviewer import ReviewerService

    service = TroveService(database_url)
    with service._connect() as db:
        if db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]:
            raise RuntimeError("This database already has cases. Use a new --db path or --reset.")
    legacy = LegacyService(service)
    discovery = DiscoveryBootstrap(service)

    owner_token = service.create_owner(OWNER_PASSWORD, OWNER_EMAIL)
    owner_id = service.session_user(owner_token)["id"]
    service.logout(owner_token)

    # ---------------------------------------------------------------- Plan case
    plan = service.create_case({"mode": "plan", "subject_name": "Asha Rao", "country": "IN",
                                "jurisdiction": "Maharashtra"}, owner_id)
    legacy.update_policy(plan["id"], {"quorum": 2, "waiting_period_hours": 0, "checkin_interval_days": 30,
                                      "owner_email": OWNER_EMAIL, "owner_phone": "+919800000001"})
    for name, category in (("asha-will.txt", "will"),):
        service.upload_document(plan["id"], {"file_name": name, "category": category,
                                             "content_base64": _b64(SAMPLE_FILES[name])})
    service.upload_document(plan["id"], {
        "file_name": "lic-policy-schedule.txt", "category": "insurance_policy",
        "content_base64": _b64("LIC Jeevan Anand policy 88xx1290. Sum assured Rs 10,00,000. "
                               "Nominee: Ravi Rao, Meera Rao.")})
    service.upload_document(plan["id"], {
        "file_name": "hdfc-savings-statement.txt", "category": "bank_statement",
        "content_base64": _b64("HDFC Bank savings account XXXXXXXX7788 statement for Asha Rao.")})
    for name, rel, share in (("Ravi Rao", "son", 50), ("Meera Rao", "daughter", 50)):
        service.add_beneficiary(plan["id"], {"name": name, "relationship": rel, "share_percent": share})

    people = {
        "ravi": dict(full_name="Ravi Rao", relationship="son", role="primary", email="ravi@demo.test",
                     phone="+919811110001", id_last4="1111", date_of_birth="1994-02-11",
                     allowed_categories=["will", "insurance_policy"]),
        "meera": dict(full_name="Meera Rao", relationship="daughter", role="executor", email="meera@demo.test",
                      phone="+919811110002", id_last4="2222", date_of_birth="1996-09-03",
                      allowed_categories=[]),
        "arjun": dict(full_name="Arjun Rao", relationship="nephew", role="secondary", email="arjun@demo.test",
                      phone="+919811110003", id_last4="3333", date_of_birth="2000-12-25",
                      allowed_categories=["will"]),
        "kiran": dict(full_name="Kiran Rao", relationship="spouse", role="secondary", email="kiran@demo.test",
                      phone="+919811110004", id_last4="5678", date_of_birth="1968-07-21",
                      allowed_categories=["will", "property_document"]),
    }
    added = {key: legacy.add_nominee(plan["id"], data) for key, data in people.items()}
    secrets = {
        key: _onboard(legacy, plan["id"], added[key], people[key]["email"], people[key]["id_last4"],
                      people[key]["date_of_birth"], approve=(key != "arjun"))
        for key in ("ravi", "meera", "arjun")
    }

    # ----------------------------------------------------------- Discovery case
    disc = service.create_case({"mode": "discovery", "subject_name": "Vikram Mehta", "country": "IN",
                                "jurisdiction": "Maharashtra"}, owner_id)
    discovery.add_seeds(disc["id"], {"have": ["death_certificate", "name_dob", "phone_access", "documents"],
                                     "phones": ["+91 98123 45678"], "employers": ["Tata Motors"]})
    discovery.ingest(disc["id"], {"source_type": "sms_export", "file_name": "vikram-sms-export.xml",
                                  "content_base64": _b64(SAMPLE_FILES["vikram-sms-export.xml"])})
    service.upload_document(disc["id"], {"file_name": "vikram-death-certificate.txt", "category": "death_certificate",
                                         "date_of_death": "2026-03-12",
                                         "content_base64": _b64(SAMPLE_FILES["vikram-death-certificate.txt"])})

    # ---------------------------------------------------------------- reviewer
    reviewer = ReviewerService(service, legacy).create_reviewer(*REVIEWER)

    # ---------------------------------------------------------- sample uploads
    files_dir.mkdir(parents=True, exist_ok=True)
    for name, content in SAMPLE_FILES.items():
        (files_dir / name).write_text(content, encoding="utf-8")

    return {
        "plan_case": plan["id"], "discovery_case": disc["id"], "kiran_invite": added["kiran"]["invite_code"],
        "secrets": secrets, "reviewer_secret": reviewer["totp_secret"],
    }


def guide(result: dict[str, Any], db: str, files_dir: Path) -> str:
    s = result["secrets"]
    totp = "python -m trove_agent totp"
    return f"""
TROVE SAMPLE USE CASE - the Rao family (fictional data, development only)
========================================================================
Start the app:   python -m trove_agent --db {db}
Open:            http://127.0.0.1:8000/          (owner)
                 http://127.0.0.1:8000/nominee   (nominees; use a private window per person)
                 http://127.0.0.1:8000/reviewer  (independent reviewer)
Sample files to upload: {files_dir}

Authenticator codes: add a setup key below to Google/Microsoft Authenticator, or run
    {totp} <SETUP KEY>
A code can be used once; if one is rejected, wait for the next 30-second code.

ACCOUNTS
  Owner     Asha Rao     {OWNER_EMAIL:<18} {OWNER_PASSWORD}
  Nominee   Ravi Rao     ravi@demo.test     {NOMINEE_PASSWORD}   key {s['ravi']}   (verified, primary)
  Nominee   Meera Rao    meera@demo.test    {NOMINEE_PASSWORD}   key {s['meera']}   (verified, executor)
  Nominee   Arjun Rao    arjun@demo.test    {NOMINEE_PASSWORD}   key {s['arjun']}   (ID awaiting review)
  Nominee   Kiran Rao    kiran@demo.test    not enrolled - invite code {result['kiran_invite']}
            (ID last 4: 5678, date of birth: 1968-07-21)
  Reviewer  rita         {REVIEWER[1]}    key {result['reviewer_secret']}

SCENARIOS TO TRY
  A. Enrol a new nominee (Kiran): /nominee > "I have an invite code" > invite code above,
     kiran@demo.test, any strong password. Copy codes from the dev inbox card, set up MFA,
     submit ID (5678, 1968-07-21, upload kiran-id-card.txt). Then approve it as the owner
     (Plan > Nominees) or as rita at /reviewer.
  B. Review an identity: owner Plan > Arjun Rao > Approve ID (or rita's queue).
  C. Death claim and release (India rules, quorum 2 of 4, no cooling-off):
     1. Ravi signs in (password + authenticator code), reports the death with
        asha-death-certificate.txt. Opening the claim counts as his confirmation (1 of 2).
     2. India requires proof of legal entitlement: on the claim, Ravi picks "Legal heir
        certificate" and uploads ravi-legal-heir-certificate.txt.
     3. Owner (or rita) approves the certificate and the legal-heir certificate
        (Plan > Death claims, or rita's "Legal documents" tab).
     4. Meera signs in and clicks Confirm death (enter an authenticator code if asked)
        -> RELEASED.
     5. Meera (executor) unlocks downloads and sees every document; Ravi sees only the
        will and insurance policy.
  D. Owner veto: while a claim is open, owner clicks "I'm alive - check in" -> claim cancelled.
  E. Dispute: a nominee clicks Dispute -> release freezes.
  F. Security: 5 wrong passwords lock a nominee for 15 minutes; 3 wrong ID details suspend.
  G. Discovery (case "Vikram Mehta"): leads already exist from an SMS export. Upload
     vikram-hdfc-statement.txt (Document > Bank statement) and vikram-mailbox.mbox (Email
     export) to corroborate leads, try the demo inbox, promote leads, confirm them in
     Overview, review the death certificate in Documents, advance the workflow, add claims,
     and open the Knowledge graph.
  H. Assistant: ask "What should I do next?" and "Which legal documents and deadlines
     apply here?" in each case (local mode, no data leaves).
  I. Country rules: open "Country & compliance" (India: legal-heir/succession certificate,
     21-day death registration, DPDP Act). Then create cases for United Kingdom, United
     States, European Union and Other to compare documents, deadlines and privacy law.
     Plan > Release policy can switch legal documents between required and advisory.
"""


def run(database_url: str, reset: bool = False) -> str:
    from trove_agent import config

    if config.current().production:
        raise RuntimeError("The demo seeds fictional data and is disabled in production.")
    if database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("Use a SQLite file for the demo, e.g. --db demo.db")
    db_path = Path(database_url)
    files_dir = db_path.with_name(f"{db_path.stem}-files")
    if reset:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), db_path.with_name(f"{db_path.stem}_vault"),
                     files_dir, db_path.with_name(f"{db_path.stem}-logins.txt")):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    result = build(database_url, files_dir)
    text = guide(result, database_url, files_dir.resolve())
    db_path.with_name(f"{db_path.stem}-logins.txt").write_text(text, encoding="utf-8")
    return text
