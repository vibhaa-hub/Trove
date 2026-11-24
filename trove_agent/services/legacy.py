"""Legacy access: nominee/beneficiary verification and gated vault release.

Release model (all gates must pass before any nominee sees vault content):

  Plan mode    owner registers nominees + release policy + per-nominee scope
  Enrol        one-time invite code (shared out of band) + registered email
               + strong password
  Possession   OTP to the email AND phone the owner registered
  MFA          mandatory authenticator-app TOTP (replay-protected)
  Identity     ID last-4 + DOB matched against owner-registered hashes
               + ID document reviewed by a human reviewer
  Death claim  death certificate uploaded by a verified nominee
               + human reviewer approval
  Quorum       k-of-n verified nominees confirm (step-up TOTP); any dispute freezes
  Liveness     owner is alerted on every claim; an owner check-in vetoes it
  Cooling-off  waiting period must elapse
  Risk         fraud signals scored; high risk blocks release unless a
               reviewer records an explicit override
  Access       released nominees see only their granted categories; downloads
               need a fresh TOTP (step-up) and are audit-logged

Every legacy event is written to a per-case SHA-256 hash chain so tampering
with the audit history is detectable.
"""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from trove_agent import config
from trove_agent.core import jurisdictions
from trove_agent.core import security as sec
from trove_agent.core.db import IntegrityError as DBIntegrityError
from trove_agent.core.store import (
    ConflictError,
    NotFoundError,
    TroveError,
    TroveService,
    ValidationError,
    new_id,
    now,
)


class AuthError(TroveError):
    status_code = 401


class ForbiddenError(TroveError):
    status_code = 403


class RateLimitError(TroveError):
    status_code = 429


ROLES = ("primary", "secondary", "executor", "guardian", "claimant")
NOMINEE_SESSION_HOURS = 2
PRE_MFA_SESSION_MINUTES = 30
STEP_UP_SECONDS = 10 * 60
INVITE_DAYS = 7
IDENTITY_MAX_MISMATCHES = 3
RISK_WEIGHTS = {"high": 40, "medium": 20, "low": 5}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _in(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


class OutboxNotifier:
    """Transactional outbox. Messages are queued in the same DB transaction
    as the action that caused them, then delivered by ``notify.deliver_pending``
    after commit (and retried by the worker).

    mode="dev": nothing is sent; the message stays visible in the dev outbox.
    mode="live": destination and body are stored encrypted until delivered,
    then the body is wiped and only the redacted text is kept as a log.
    """

    def __init__(self, service: TroveService, mode: str = "dev") -> None:
        self.service = service
        self.mode = mode

    def send(self, db: Any, case_id: str, nominee_id: str | None, channel: str,
             destination: str, destination_masked: str, purpose: str, body: str,
             redacted_body: str) -> None:
        live = self.mode != "dev"
        db.execute(
            """INSERT INTO legacy_outbox
               (id, case_id, nominee_id, channel, destination_masked, purpose,
                body, redacted_body, created_at, status, destination_enc, attempts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (new_id(), case_id, nominee_id, channel, destination_masked, purpose,
             self.service.encrypt_field(body) if live else body, redacted_body, now(),
             "pending" if live else "dev",
             self.service.encrypt_field(destination) if live else None),
        )


DevOutboxNotifier = OutboxNotifier  # backward-compatible name


class LegacyService:
    def __init__(self, service: TroveService, notifier: Any | None = None) -> None:
        self.service = service
        from trove_agent.config import current

        self.notifier = notifier or OutboxNotifier(service, current().notifier)
        self.pepper = service.pepper
        self.limiter = sec.RateLimiter()
        self._initialize()

    # ------------------------------------------------------------ schema
    def _connect(self) -> Any:
        return self.service._connect()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS legacy_policy (
                    case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
                    quorum INTEGER NOT NULL DEFAULT 1,
                    waiting_period_hours INTEGER NOT NULL DEFAULT 72,
                    checkin_interval_days INTEGER NOT NULL DEFAULT 90,
                    owner_email TEXT,
                    owner_phone TEXT,
                    last_owner_checkin TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nominees (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    full_name TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    role TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    id_last4_hash TEXT,
                    dob_hash TEXT,
                    allowed_categories_json TEXT NOT NULL DEFAULT '[]',
                    invite_hash TEXT UNIQUE,
                    invite_expires_at TEXT,
                    password_hash TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    phone_verified INTEGER NOT NULL DEFAULT 0,
                    totp_secret_enc TEXT,
                    totp_enabled INTEGER NOT NULL DEFAULT 0,
                    totp_last_step INTEGER,
                    identity_status TEXT NOT NULL DEFAULT 'not_submitted',
                    identity_document_id TEXT,
                    relationship_document_id TEXT,
                    identity_mismatches INTEGER NOT NULL DEFAULT 0,
                    failed_logins INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    status TEXT NOT NULL DEFAULT 'invited',
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, email)
                );
                CREATE TABLE IF NOT EXISTS nominee_sessions (
                    token_hash TEXT PRIMARY KEY,
                    nominee_id TEXT NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                    level TEXT NOT NULL,
                    step_up_at TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS otp_challenges (
                    id TEXT PRIMARY KEY,
                    nominee_id TEXT NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS release_requests (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    initiated_by TEXT NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    certificate_document_id TEXT,
                    certificate_review TEXT NOT NULL DEFAULT 'pending',
                    certificate_name_match INTEGER NOT NULL DEFAULT 0,
                    date_of_death TEXT,
                    risk_override_note TEXT,
                    opened_at TEXT NOT NULL,
                    waiting_until TEXT NOT NULL,
                    closed_at TEXT,
                    close_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS release_attestations (
                    request_id TEXT NOT NULL REFERENCES release_requests(id) ON DELETE CASCADE,
                    nominee_id TEXT NOT NULL REFERENCES nominees(id) ON DELETE CASCADE,
                    decision TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (request_id, nominee_id)
                );
                CREATE TABLE IF NOT EXISTS legacy_outbox (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    nominee_id TEXT,
                    channel TEXT NOT NULL,
                    destination_masked TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    body TEXT NOT NULL,
                    redacted_body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS legacy_audit (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    UNIQUE(case_id, seq)
                );
                CREATE TABLE IF NOT EXISTS release_legal_documents (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL REFERENCES release_requests(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    requirement_key TEXT NOT NULL,
                    document_type TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_review',
                    uploaded_by TEXT NOT NULL,
                    reviewed_by TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_legal_docs_request ON release_legal_documents(request_id);
                CREATE INDEX IF NOT EXISTS idx_nominees_case ON nominees(case_id);
                CREATE INDEX IF NOT EXISTS idx_release_case ON release_requests(case_id);
                """
            )
            additions = {
                "nominees": {"email_bidx": "TEXT", "phone_bidx": "TEXT"},
                "legacy_outbox": {"status": "TEXT NOT NULL DEFAULT 'dev'",
                                  "destination_enc": "TEXT",
                                  "attempts": "INTEGER NOT NULL DEFAULT 0",
                                  "last_error": "TEXT", "sent_at": "TEXT"},
                "legacy_policy": {"last_reminder_at": "TEXT", "escalated_at": "TEXT",
                                  "legal_documents": "TEXT"},
            }
            for table, columns in additions.items():
                existing = db.columns(table)
                for column, definition in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_nominee_email_bidx
                          ON nominees(case_id, email_bidx)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_outbox_status ON legacy_outbox(status)")
            # Backfill: encrypt legacy plaintext contacts and add blind indexes.
            for row in db.execute(
                "SELECT id, email, phone FROM nominees WHERE email_bidx IS NULL").fetchall():
                email, phone = row["email"], row["phone"]
                db.execute(
                    """UPDATE nominees SET email = ?, phone = ?, email_bidx = ?, phone_bidx = ?
                       WHERE id = ?""",
                    (self.service.encrypt_field(email), self.service.encrypt_field(phone),
                     self.pepper.keyed_hash("nominee_email", email),
                     self.pepper.keyed_hash("nominee_phone", phone), row["id"]))
            for row in db.execute(
                """SELECT case_id, owner_email, owner_phone FROM legacy_policy
                   WHERE (owner_email IS NOT NULL AND owner_email NOT LIKE 'enc1:%')
                      OR (owner_phone IS NOT NULL AND owner_phone NOT LIKE 'enc1:%')""").fetchall():
                db.execute(
                    "UPDATE legacy_policy SET owner_email = ?, owner_phone = ? WHERE case_id = ?",
                    (self.service.encrypt_field(row["owner_email"]) if row["owner_email"] else None,
                     self.service.encrypt_field(row["owner_phone"]) if row["owner_phone"] else None,
                     row["case_id"]))

    # ------------------------------------------------------------ helpers
    def _audit(
        self, db: Any, case_id: str, actor: str, action: str,
        details: dict[str, Any],
    ) -> None:
        last = db.execute(
            "SELECT seq, hash FROM legacy_audit WHERE case_id = ? ORDER BY seq DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        seq = (last["seq"] + 1) if last else 1
        prev_hash = last["hash"] if last else sec.GENESIS
        created_at = now()
        payload = {"seq": seq, "actor": actor, "action": action,
                   "details": details, "created_at": created_at}
        db.execute(
            """INSERT INTO legacy_audit
               (id, case_id, seq, actor, action, details_json, created_at, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id(), case_id, seq, actor, action, json.dumps(details, sort_keys=True),
             created_at, prev_hash, sec.chain_hash(prev_hash, payload)),
        )

    def verify_audit_chain(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self.service._case_row(db, case_id)
            rows = db.execute(
                "SELECT * FROM legacy_audit WHERE case_id = ? ORDER BY seq", (case_id,)
            ).fetchall()
        previous = sec.GENESIS
        for index, row in enumerate(rows, 1):
            payload = {"seq": row["seq"], "actor": row["actor"], "action": row["action"],
                       "details": json.loads(row["details_json"]),
                       "created_at": row["created_at"]}
            if (row["seq"] != index or row["prev_hash"] != previous
                    or sec.chain_hash(previous, payload) != row["hash"]):
                return {"valid": False, "events": len(rows), "broken_at_seq": row["seq"]}
            previous = row["hash"]
        return {"valid": True, "events": len(rows), "head": previous}

    def list_audit(self, case_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT seq, actor, action, details_json, created_at, hash
                   FROM legacy_audit WHERE case_id = ? ORDER BY seq DESC LIMIT ?""",
                (case_id, limit),
            ).fetchall()
        return [{**{k: row[k] for k in ("seq", "actor", "action", "created_at")},
                 "details": json.loads(row["details_json"]), "hash": row["hash"][:12]}
                for row in rows]

    def _fernet(self):
        fernet, _ = self.service.vault._fernet()
        return fernet

    def _rules(self, db: Any, case_id: str) -> jurisdictions.Profile:
        row = db.execute("SELECT country, jurisdiction FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFoundError("Case not found")
        return jurisdictions.profile(row["country"] or jurisdictions.detect_country(row["jurisdiction"]))

    def _legal_mode(self, db: Any, case_id: str, policy: Any | None = None) -> str:
        policy = policy if policy is not None else self._policy(db, case_id)
        return policy["legal_documents"] or self._rules(db, case_id).legal_documents_default

    def _policy(self, db: Any, case_id: str) -> Any:
        row = db.execute("SELECT * FROM legacy_policy WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            defaults = self._rules(db, case_id).defaults
            db.execute(
                """INSERT INTO legacy_policy
                   (case_id, quorum, waiting_period_hours, checkin_interval_days, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (case_id, defaults["quorum"], defaults["waiting_period_hours"],
                 defaults["checkin_interval_days"], now()),
            )
            row = db.execute(
                "SELECT * FROM legacy_policy WHERE case_id = ?", (case_id,)
            ).fetchone()
        return row

    def _nominee(self, db: Any, nominee_id: str,
                 case_id: str | None = None) -> Any:
        row = db.execute("SELECT * FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
        if row is None or (case_id and row["case_id"] != case_id):
            raise NotFoundError("Nominee not found")
        return row

    def _rate(self, key: str, limit: int, window: int) -> None:
        if not self.limiter.hit(key, limit, window):
            raise RateLimitError("Too many attempts. Wait a few minutes and try again.")

    def _email(self, row: Any) -> str:
        return self.service.decrypt_field(row["email"])

    def _phone(self, row: Any) -> str:
        return self.service.decrypt_field(row["phone"])

    @staticmethod
    def _verified(row: Any) -> bool:
        return bool(
            row["status"] not in ("revoked", "suspended")
            and row["password_hash"] and row["email_verified"] and row["phone_verified"]
            and row["totp_enabled"] and row["identity_status"] == "approved"
        )

    def _steps(self, row: Any) -> list[dict[str, Any]]:
        return [
            {"key": "enrolled", "label": "Invite code + password", "done": bool(row["password_hash"])},
            {"key": "email", "label": "Email ownership (OTP)", "done": bool(row["email_verified"])},
            {"key": "phone", "label": "Phone ownership (OTP)", "done": bool(row["phone_verified"])},
            {"key": "mfa", "label": "Authenticator app MFA", "done": bool(row["totp_enabled"])},
            {"key": "identity", "label": "Identity match + document review",
             "done": row["identity_status"] == "approved", "state": row["identity_status"]},
        ]

    def _public_nominee(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "full_name": row["full_name"],
            "relationship": row["relationship"],
            "role": row["role"],
            "email": sec.mask_email(self._email(row)),
            "phone": sec.mask_phone(self._phone(row)),
            "status": row["status"],
            "verified": self._verified(row),
            "identity_status": row["identity_status"],
            "identity_hint_registered": bool(row["id_last4_hash"]),
            "allowed_categories": json.loads(row["allowed_categories_json"]),
            "locked": bool(row["locked_until"] and _utc(row["locked_until"]) > datetime.now(timezone.utc)),
            "steps": self._steps(row),
            "created_at": row["created_at"],
        }

    def _send_otp(self, db: Any, nominee: Any, channel: str) -> None:
        code = sec.new_numeric_code()
        db.execute(
            "UPDATE otp_challenges SET consumed = 1 WHERE nominee_id = ? AND channel = ?",
            (nominee["id"], channel),
        )
        db.execute(
            """INSERT INTO otp_challenges
               (id, nominee_id, channel, code_hash, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), nominee["id"], channel,
             self.pepper.keyed_hash(f"otp:{nominee['id']}:{channel}", code),
             _in(timedelta(seconds=sec.OTP_TTL_SECONDS)), now()),
        )
        real = self._email(nominee) if channel == "email" else self._phone(nominee)
        destination = sec.mask_email(real) if channel == "email" else sec.mask_phone(real)
        text = (f"Trove verification code: {code}. It expires in 10 minutes. "
                "Trove staff will never ask you for this code.")
        self.notifier.send(db, nominee["case_id"], nominee["id"], channel, real, destination,
                           "otp", text, "Trove verification code: ****** (redacted)")

    def _notify_owner(self, db: Any, case_id: str, text: str) -> None:
        policy = self._policy(db, case_id)
        for channel, stored in (("email", policy["owner_email"]), ("phone", policy["owner_phone"])):
            if stored:
                value = self.service.decrypt_field(stored)
                masked = sec.mask_email(value) if channel == "email" else sec.mask_phone(value)
                self.notifier.send(db, case_id, None, channel, value, masked, "owner_alert",
                                   text, text)

    def _notify_nominees(self, db: Any, case_id: str, text: str,
                         exclude: str | None = None) -> None:
        for row in db.execute(
            "SELECT * FROM nominees WHERE case_id = ? AND status != 'revoked'", (case_id,)
        ):
            if row["id"] != exclude:
                email = self._email(row)
                self.notifier.send(db, case_id, row["id"], "email", email, sec.mask_email(email),
                                   "nominee_alert", text, text)

    # ===================================================== OWNER / REVIEWER
    def overview(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            case = self.service._case_row(db, case_id)
            policy = dict(self._policy(db, case_id))
            nominees = [self._public_nominee(r) for r in db.execute(
                "SELECT * FROM nominees WHERE case_id = ? ORDER BY created_at", (case_id,))]
            requests = [self._request_view(db, r) for r in db.execute(
                "SELECT * FROM release_requests WHERE case_id = ? ORDER BY opened_at DESC",
                (case_id,))]
            outbox = [dict(r) for r in db.execute(
                """SELECT channel, destination_masked, purpose, redacted_body AS body,
                          created_at, status
                   FROM legacy_outbox WHERE case_id = ? ORDER BY created_at DESC LIMIT 30""",
                (case_id,))]
            categories = sorted({r["category"] for r in db.execute(
                "SELECT category FROM documents WHERE case_id = ?", (case_id,))})
        with self._connect() as db:
            policy["legal_documents"] = self._legal_mode(db, case_id, policy)
            rules = self._rules(db, case_id)
        policy["country"] = rules.code
        policy["minimums"] = rules.minimums if config.current().production else {}
        policy["recommended"] = rules.defaults
        for key in ("owner_email", "owner_phone"):
            if policy[key]:
                value = self.service.decrypt_field(policy[key])
                policy[key] = (sec.mask_email(value) if key == "owner_email"
                               else sec.mask_phone(value))
        return {
            "case_id": case_id,
            "mode": case["mode"],
            "policy": policy,
            "nominees": nominees,
            "verified_nominees": sum(n["verified"] for n in nominees),
            "requests": requests,
            "outbox": outbox,
            "document_categories": categories,
            "audit": self.verify_audit_chain(case_id),
            "dev_outbox": True,
        }

    def update_policy(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        def bounded(name: str, low: int, high: int) -> int | None:
            value = data.get(name)
            if value is None:
                return None
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValidationError(f"{name} must be an integer between {low} and {high}")
            return value

        quorum = bounded("quorum", 1, 10)
        waiting = bounded("waiting_period_hours", 0, 24 * 90)
        checkin = bounded("checkin_interval_days", 7, 730)
        updates: dict[str, Any] = {}
        if quorum is not None:
            updates["quorum"] = quorum
        if waiting is not None:
            updates["waiting_period_hours"] = waiting
        if checkin is not None:
            updates["checkin_interval_days"] = checkin
        legal = data.get("legal_documents")
        if legal is not None:
            if legal not in ("required", "advisory"):
                raise ValidationError("legal_documents must be 'required' or 'advisory'")
            updates["legal_documents"] = legal
        try:
            if data.get("owner_email"):
                updates["owner_email"] = self.service.encrypt_field(
                    sec.normalize_email(data["owner_email"]))
            if data.get("owner_phone"):
                updates["owner_phone"] = self.service.encrypt_field(
                    sec.normalize_phone(data["owner_phone"]))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        with self._connect() as db:
            self.service._case_row(db, case_id)
            self._policy(db, case_id)
            rules = self._rules(db, case_id)
            minimum = rules.minimums.get("waiting_period_hours", 0)
            if waiting is not None and config.current().production and waiting < minimum:
                raise ValidationError(
                    f"{rules.name} cases need a cooling-off period of at least {minimum} hours")
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                db.execute(
                    f"UPDATE legacy_policy SET {assignments}, updated_at = ? WHERE case_id = ?",
                    (*updates.values(), now(), case_id),
                )
                self._audit(db, case_id, "owner", "policy.updated",
                            {"fields": sorted(updates)})
        return self.overview(case_id)["policy"]

    def owner_checkin(self, case_id: str) -> dict[str, Any]:
        """Proof-of-life. Vetoes any release request that is still open."""
        with self._connect() as db:
            self.service._case_row(db, case_id)
            self._policy(db, case_id)
            db.execute(
                "UPDATE legacy_policy SET last_owner_checkin = ? WHERE case_id = ?",
                (now(), case_id),
            )
            vetoed = db.execute(
                """UPDATE release_requests SET status = 'vetoed', closed_at = ?,
                   close_reason = 'Owner checked in (proof of life)'
                   WHERE case_id = ? AND status IN ('open', 'frozen')""",
                (now(), case_id),
            ).rowcount
            if vetoed:
                self._notify_nominees(
                    db, case_id,
                    "A pending legacy release request was cancelled because the owner "
                    "confirmed they are alive. If you believe this is wrong, contact Trove support.")
            self._audit(db, case_id, "owner", "owner.checkin", {"vetoed_requests": vetoed})
        return {"checked_in": True, "vetoed_requests": vetoed}

    def add_nominee(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = data.get("full_name")
        relationship = data.get("relationship")
        role = data.get("role", "primary")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValidationError("full_name is required (max 200 characters)")
        if not isinstance(relationship, str) or not relationship.strip() or len(relationship) > 100:
            raise ValidationError("relationship is required (max 100 characters)")
        if role not in ROLES:
            raise ValidationError(f"role must be one of {', '.join(ROLES)}")
        try:
            email = sec.normalize_email(data.get("email"))
            phone = sec.normalize_phone(data.get("phone"))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        id_last4 = sec.normalize_identifier(data.get("id_last4"))
        if data.get("id_last4") and len(id_last4) != 4:
            raise ValidationError("id_last4 must be exactly the last 4 letters/digits of an ID")
        dob = data.get("date_of_birth")
        if dob:
            try:
                datetime.strptime(dob, "%Y-%m-%d")
            except (TypeError, ValueError) as exc:
                raise ValidationError("date_of_birth must be YYYY-MM-DD") from exc
        categories = data.get("allowed_categories", [])
        if not isinstance(categories, list) or not all(
            isinstance(c, str) and len(c) <= 50 for c in categories
        ):
            raise ValidationError("allowed_categories must be a list of document categories")
        invite = sec.new_invite_code()
        nominee_id = new_id()
        with self._connect() as db:
            case = self.service._case_row(db, case_id)
            if role != "claimant" and case["mode"] == "discovery" and not id_last4:
                # Discovery claimants have no owner-registered identity hint.
                role = "claimant"
            try:
                db.execute(
                    """INSERT INTO nominees
                       (id, case_id, full_name, relationship, role, email, phone,
                        id_last4_hash, dob_hash, allowed_categories_json, invite_hash,
                        invite_expires_at, created_at, email_bidx, phone_bidx)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (nominee_id, case_id, name.strip(), relationship.strip(), role,
                     self.service.encrypt_field(email), self.service.encrypt_field(phone),
                     self.pepper.keyed_hash("id_last4", id_last4) if id_last4 else None,
                     self.pepper.keyed_hash("dob", dob) if dob else None,
                     json.dumps(sorted(set(categories))),
                     self.pepper.keyed_hash("invite", invite),
                     _in(timedelta(days=INVITE_DAYS)), now(),
                     self.pepper.keyed_hash("nominee_email", email),
                     self.pepper.keyed_hash("nominee_phone", phone)),
                )
            except DBIntegrityError as exc:
                raise ConflictError("A nominee with this email already exists for the case") from exc
            self._audit(db, case_id, "owner", "nominee.added",
                        {"nominee_id": nominee_id, "role": role,
                         "identity_hint": bool(id_last4), "dob_hint": bool(dob)})
            row = self._nominee(db, nominee_id)
        return {**self._public_nominee(row), "invite_code": invite,
                "invite_expires_at": row["invite_expires_at"],
                "invite_note": "Shown once. Share it with the nominee in person or by a "
                               "separate channel from the email address you registered."}

    def reinvite(self, case_id: str, nominee_id: str) -> dict[str, Any]:
        invite = sec.new_invite_code()
        with self._connect() as db:
            row = self._nominee(db, nominee_id, case_id)
            if row["status"] == "revoked":
                raise ConflictError("Revoked nominees cannot be re-invited")
            db.execute(
                """UPDATE nominees SET invite_hash = ?, invite_expires_at = ?,
                   failed_logins = 0, locked_until = NULL WHERE id = ?""",
                (self.pepper.keyed_hash("invite", invite), _in(timedelta(days=INVITE_DAYS)),
                 nominee_id),
            )
            self._audit(db, case_id, "owner", "nominee.reinvited", {"nominee_id": nominee_id})
        return {"invite_code": invite, "nominee_id": nominee_id}

    def revoke_nominee(self, case_id: str, nominee_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._nominee(db, nominee_id, case_id)
            db.execute(
                "UPDATE nominees SET status = 'revoked', invite_hash = NULL WHERE id = ?",
                (nominee_id,),
            )
            db.execute("DELETE FROM nominee_sessions WHERE nominee_id = ?", (nominee_id,))
            self._audit(db, case_id, "owner", "nominee.revoked", {"nominee_id": nominee_id})
            return self._public_nominee(self._nominee(db, nominee_id))

    def review_identity(self, case_id: str, nominee_id: str, data: dict[str, Any],
                        actor: str = "reviewer") -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("approve", "reject"):
            raise ValidationError("decision must be approve or reject")
        with self._connect() as db:
            row = self._nominee(db, nominee_id, case_id)
            if row["identity_status"] != "pending_review":
                raise ConflictError("No identity submission is waiting for review")
            status = "approved" if decision == "approve" else "rejected"
            db.execute("UPDATE nominees SET identity_status = ? WHERE id = ?", (status, nominee_id))
            if row["identity_document_id"]:
                db.execute("UPDATE documents SET review_status = ? WHERE id = ?",
                           ("reviewed" if status == "approved" else "rejected",
                            row["identity_document_id"]))
            row = self._nominee(db, nominee_id)
            if self._verified(row):
                db.execute("UPDATE nominees SET status = 'verified' WHERE id = ?", (nominee_id,))
            self._audit(db, case_id, actor, "nominee.identity_reviewed",
                        {"nominee_id": nominee_id, "decision": status})
            return self._public_nominee(self._nominee(db, nominee_id))

    def review_certificate(self, case_id: str, request_id: str, data: dict[str, Any],
                           actor: str = "reviewer") -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("approve", "reject"):
            raise ValidationError("decision must be approve or reject")
        override = data.get("risk_override_note")
        if override is not None and (not isinstance(override, str) or len(override) > 500):
            raise ValidationError("risk_override_note must be at most 500 characters")
        with self._connect() as db:
            request = self._request(db, case_id, request_id)
            if request["status"] not in ("open", "frozen"):
                raise ConflictError("This request is closed")
            status = "approved" if decision == "approve" else "rejected"
            db.execute(
                """UPDATE release_requests SET certificate_review = ?,
                   risk_override_note = COALESCE(?, risk_override_note) WHERE id = ?""",
                (status, override.strip() if override else None, request_id),
            )
            if request["certificate_document_id"]:
                db.execute(
                    "UPDATE documents SET review_status = ? WHERE id = ?",
                    ("reviewed" if status == "approved" else "rejected",
                     request["certificate_document_id"]),
                )
                db.execute(
                    "UPDATE cases SET death_status = ? WHERE id = ? AND death_certificate_id = ?",
                    ("reviewed" if status == "approved" else "rejected", case_id,
                     request["certificate_document_id"]),
                )
            if status == "rejected":
                db.execute(
                    """UPDATE release_requests SET status = 'rejected', closed_at = ?,
                       close_reason = 'Death certificate rejected by reviewer' WHERE id = ?""",
                    (now(), request_id),
                )
            self._audit(db, case_id, actor, "release.certificate_reviewed",
                        {"request_id": request_id, "decision": status,
                         "risk_override": bool(override)})
        return self.evaluate(case_id, request_id)

    # ========================================================== NOMINEE AUTH
    def _create_session(self, db: Any, nominee_id: str, level: str) -> str:
        token = secrets.token_urlsafe(32)
        lifetime = (timedelta(hours=NOMINEE_SESSION_HOURS) if level == "mfa"
                    else timedelta(minutes=PRE_MFA_SESSION_MINUTES))
        db.execute(
            """INSERT INTO nominee_sessions
               (token_hash, nominee_id, level, step_up_at, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (self.service._session_hash(token), nominee_id, level,
             now() if level == "mfa" else None, _in(lifetime), now()),
        )
        db.execute("DELETE FROM nominee_sessions WHERE expires_at <= ?", (now(),))
        return token

    def session(self, token: str | None, require_mfa: bool = True) -> dict[str, Any]:
        if not token:
            raise AuthError("Nominee sign-in required")
        with self._connect() as db:
            row = db.execute(
                """SELECT s.*, n.case_id, n.status FROM nominee_sessions s
                   JOIN nominees n ON n.id = s.nominee_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (self.service._session_hash(token), now()),
            ).fetchone()
        if row is None or row["status"] in ("revoked", "suspended"):
            raise AuthError("Session expired or access revoked; sign in again")
        if require_mfa and row["level"] != "mfa":
            raise ForbiddenError("Complete MFA before accessing this")
        return dict(row)

    def _fail_login(self, db: Any, row: Any, reason: str) -> None:
        failures = row["failed_logins"] + 1
        locked = (_in(timedelta(seconds=sec.LOCKOUT_SECONDS))
                  if failures >= sec.LOCKOUT_THRESHOLD else None)
        db.execute("UPDATE nominees SET failed_logins = ?, locked_until = ? WHERE id = ?",
                   (0 if locked else failures, locked, row["id"]))
        self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}", "nominee.login_failed",
                    {"reason": reason, "locked": bool(locked)})
        if locked:
            self._notify_nominees(db, row["case_id"],
                                  "Security notice: a nominee account was locked after "
                                  "repeated failed sign-in attempts.", exclude=None)

    def _check_lock(self, row: Any) -> None:
        if row["locked_until"] and _utc(row["locked_until"]) > datetime.now(timezone.utc):
            raise RateLimitError("Account temporarily locked after failed attempts. Try later.")

    def enroll(self, data: dict[str, Any], client: str = "local") -> str:
        self._rate(f"enroll:{client}", 10, 900)
        invite = data.get("invite_code")
        password = data.get("password")
        try:
            email = sec.normalize_email(data.get("email"))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if not isinstance(invite, str) or not invite.strip():
            raise ValidationError("invite_code is required")
        error = sec.password_strength_error(password)
        if error:
            raise ValidationError(error)
        invite_hash = self.pepper.keyed_hash("invite", invite.strip().upper())
        # Same error for every failure so invite codes cannot be probed.
        generic = AuthError("Invite code and email do not match an active invitation")
        with self._connect() as db:
            row = db.execute("SELECT * FROM nominees WHERE invite_hash = ?",
                             (invite_hash,)).fetchone()
            if row is None or row["status"] == "revoked":
                raise generic
            if (row["email_bidx"] != self.pepper.keyed_hash("nominee_email", email)
                    or _utc(row["invite_expires_at"]) < datetime.now(timezone.utc)):
                self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}",
                            "nominee.enroll_failed", {"reason": "email_or_expiry"})
                row = None
        if row is None:
            raise generic
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM nominees WHERE id = ? AND invite_hash = ?",
                             (row["id"], invite_hash)).fetchone()
            if row is None:
                raise generic
            db.execute(
                """UPDATE nominees SET password_hash = ?, invite_hash = NULL,
                   invite_expires_at = NULL, status = 'enrolled' WHERE id = ?""",
                (self.service._hash_password(password), row["id"]),
            )
            row = self._nominee(db, row["id"])
            self._send_otp(db, row, "email")
            self._send_otp(db, row, "phone")
            self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}", "nominee.enrolled", {})
            return self._create_session(db, row["id"], "password")

    def login(self, data: dict[str, Any], client: str = "local") -> str:
        try:
            email = sec.normalize_email(data.get("email"))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self._rate(f"login:{client}", 20, 900)
        self._rate(f"login:{email}", 10, 900)
        password = data.get("password")
        case_id = data.get("case_id")
        failure: TroveError | None = None
        with self._connect() as db:
            bidx = self.pepper.keyed_hash("nominee_email", email)
            query = "SELECT * FROM nominees WHERE email_bidx = ? AND password_hash IS NOT NULL"
            params: tuple[Any, ...] = (bidx,)
            if case_id:
                query += " AND case_id = ?"
                params = (bidx, case_id)
            rows = db.execute(query, params).fetchall()
            if len(rows) > 1:
                raise ValidationError("This email is a nominee on several cases; include case_id")
            if not rows:
                raise AuthError("Invalid credentials")
            row = rows[0]
            self._check_lock(row)
            if row["status"] in ("revoked", "suspended"):
                raise AuthError("Invalid credentials")
            level = "password"
            if not isinstance(password, str) or not self.service._verify_password(
                password, row["password_hash"]
            ):
                self._fail_login(db, row, "password")
                failure = AuthError("Invalid credentials")
            elif row["totp_enabled"]:
                secret = self._fernet().decrypt(row["totp_secret_enc"].encode()).decode()
                step = sec.verify_totp(secret, data.get("totp"), row["totp_last_step"])
                if step is None:
                    self._fail_login(db, row, "totp")
                    failure = AuthError("Authenticator code required or invalid")
                else:
                    db.execute("UPDATE nominees SET totp_last_step = ? WHERE id = ?",
                               (step, row["id"]))
                    level = "mfa"
            if failure is None:
                db.execute("UPDATE nominees SET failed_logins = 0 WHERE id = ?", (row["id"],))
                self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}", "nominee.login",
                            {"level": level})
                return self._create_session(db, row["id"], level)
        raise failure

    def logout(self, token: str | None) -> None:
        if token:
            with self._connect() as db:
                db.execute("DELETE FROM nominee_sessions WHERE token_hash = ?",
                           (self.service._session_hash(token),))

    def me(self, token: str | None) -> dict[str, Any]:
        session = self.session(token, require_mfa=False)
        with self._connect() as db:
            row = self._nominee(db, session["nominee_id"])
            case = self.service._case_row(db, row["case_id"])
            policy = self._policy(db, row["case_id"])
            requests = [self._nominee_safe(self._request_view(db, r, viewer=row["id"])) for r in db.execute(
                "SELECT * FROM release_requests WHERE case_id = ? ORDER BY opened_at DESC",
                (row["case_id"],))]
        return {
            "nominee": self._public_nominee(row),
            "session_level": session["level"],
            "case": {"id": case["id"], "subject_name": case["subject_name"], "mode": case["mode"],
                     "country": case["country"] or jurisdictions.detect_country(case["jurisdiction"])},
            "quorum": policy["quorum"],
            "waiting_period_hours": policy["waiting_period_hours"],
            "requests": requests,
            "released": any(r["status"] == "released" for r in requests),
        }

    def outbox(self, token: str | None) -> list[dict[str, Any]]:
        """DEV ONLY: simulates the nominee's own phone/email inbox."""
        session = self.session(token, require_mfa=False)
        with self._connect() as db:
            rows = db.execute(
                """SELECT channel, destination_masked, purpose, body, created_at
                   FROM legacy_outbox WHERE nominee_id = ? AND status = 'dev'
                   ORDER BY created_at DESC LIMIT 20""",
                (session["nominee_id"],),
            ).fetchall()
        return [dict(r) for r in rows]

    def resend_otp(self, token: str | None, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token, require_mfa=False)
        channel = data.get("channel")
        if channel not in ("email", "phone"):
            raise ValidationError("channel must be email or phone")
        self._rate(f"otp-send:{session['nominee_id']}", 5, 3600)
        with self._connect() as db:
            self._send_otp(db, self._nominee(db, session["nominee_id"]), channel)
        return {"sent": True, "channel": channel}

    def verify_contact(self, token: str | None, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token, require_mfa=False)
        channel = data.get("channel")
        code = data.get("code")
        if channel not in ("email", "phone"):
            raise ValidationError("channel must be email or phone")
        if not isinstance(code, str):
            raise ValidationError("code is required")
        nominee_id = session["nominee_id"]
        with self._connect() as db:
            challenge = db.execute(
                """SELECT * FROM otp_challenges WHERE nominee_id = ? AND channel = ?
                   AND consumed = 0 ORDER BY created_at DESC LIMIT 1""",
                (nominee_id, channel),
            ).fetchone()
            if challenge is None or _utc(challenge["expires_at"]) < datetime.now(timezone.utc):
                raise ValidationError("Code expired. Request a new one.")
            failure: TroveError | None = None
            if challenge["attempts"] >= sec.OTP_MAX_ATTEMPTS:
                db.execute("UPDATE otp_challenges SET consumed = 1 WHERE id = ?", (challenge["id"],))
                failure = RateLimitError("Too many wrong codes. Request a new one.")
            elif not self.pepper.matches(f"otp:{nominee_id}:{channel}", code.strip(),
                                         challenge["code_hash"]):
                db.execute("UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?",
                           (challenge["id"],))
                row = self._nominee(db, nominee_id)
                self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}",
                            "nominee.otp_failed", {"channel": channel})
                failure = ValidationError("Incorrect code")
            if failure is not None:
                db.commit()
                raise failure
            db.execute("UPDATE otp_challenges SET consumed = 1 WHERE id = ?", (challenge["id"],))
            db.execute(f"UPDATE nominees SET {channel}_verified = 1 WHERE id = ?", (nominee_id,))
            row = self._nominee(db, nominee_id)
            self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}",
                        "nominee.contact_verified", {"channel": channel})
            return self._public_nominee(row)

    def mfa_setup(self, token: str | None) -> dict[str, Any]:
        session = self.session(token, require_mfa=False)
        with self._connect() as db:
            row = self._nominee(db, session["nominee_id"])
            if row["totp_enabled"]:
                raise ConflictError("MFA is already enabled")
            if not (row["email_verified"] and row["phone_verified"]):
                raise ConflictError("Verify both email and phone before enabling MFA")
            secret = sec.new_totp_secret()
            db.execute("UPDATE nominees SET totp_secret_enc = ? WHERE id = ?",
                       (self._fernet().encrypt(secret.encode()).decode(), row["id"]))
        return {"secret": secret, "otpauth_uri": sec.totp_uri(secret, self._email(row)),
                "instructions": "Add this key to Google Authenticator, Microsoft "
                                "Authenticator, 1Password, etc., then enter the 6-digit code."}

    def mfa_confirm(self, token: str | None, data: dict[str, Any]) -> str:
        session = self.session(token, require_mfa=False)
        with self._connect() as db:
            row = self._nominee(db, session["nominee_id"])
            if row["totp_enabled"] or not row["totp_secret_enc"]:
                raise ConflictError("Start MFA setup first")
            secret = self._fernet().decrypt(row["totp_secret_enc"].encode()).decode()
            step = sec.verify_totp(secret, data.get("code"), row["totp_last_step"])
            if step is None:
                raise ValidationError("Incorrect authenticator code")
            db.execute("UPDATE nominees SET totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                       (step, row["id"]))
            db.execute("DELETE FROM nominee_sessions WHERE nominee_id = ?", (row["id"],))
            self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}", "nominee.mfa_enabled", {})
            return self._create_session(db, row["id"], "mfa")

    def step_up(self, token: str | None, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token)
        with self._connect() as db:
            row = self._nominee(db, session["nominee_id"])
            self._check_lock(row)
            secret = self._fernet().decrypt(row["totp_secret_enc"].encode()).decode()
            step = sec.verify_totp(secret, data.get("totp"), row["totp_last_step"])
            if step is None:
                self._fail_login(db, row, "step_up")
                db.commit()
                raise AuthError("Incorrect or already-used authenticator code; "
                                "wait for the next code and try again")
            db.execute("UPDATE nominees SET totp_last_step = ? WHERE id = ?", (step, row["id"]))
            db.execute("UPDATE nominee_sessions SET step_up_at = ? WHERE token_hash = ?",
                       (now(), session["token_hash"]))
        return {"step_up": True, "valid_for_seconds": STEP_UP_SECONDS}

    def _require_step_up(self, session: dict[str, Any]) -> None:
        at = session.get("step_up_at")
        if not at or (datetime.now(timezone.utc) - _utc(at)).total_seconds() > STEP_UP_SECONDS:
            raise ForbiddenError("Re-enter your authenticator code (step-up) to continue")

    # ====================================================== NOMINEE IDENTITY
    def _decode_upload(self, data: dict[str, Any], prefix: str = "") -> tuple[str, str]:
        name = data.get(f"{prefix}file_name")
        content = data.get(f"{prefix}content_base64")
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValidationError(f"{prefix}file_name and {prefix}content_base64 are required")
        try:
            base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise ValidationError("content_base64 must be valid base64") from exc
        return name, content

    def submit_identity(self, token: str | None, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token)
        nominee_id = session["nominee_id"]
        self._rate(f"identity:{nominee_id}", 5, 3600)
        with self._connect() as db:
            row = self._nominee(db, nominee_id)
        if row["identity_status"] in ("pending_review", "approved"):
            raise ConflictError("Identity already submitted")
        id_last4 = sec.normalize_identifier(data.get("id_last4"))
        dob = data.get("date_of_birth") or ""
        mismatches = []
        if row["id_last4_hash"] and not self.pepper.matches("id_last4", id_last4, row["id_last4_hash"]):
            mismatches.append("id_last4")
        if row["dob_hash"] and not self.pepper.matches("dob", dob, row["dob_hash"]):
            mismatches.append("date_of_birth")
        if mismatches:
            with self._connect() as db:
                count = row["identity_mismatches"] + 1
                suspended = count >= IDENTITY_MAX_MISMATCHES
                db.execute(
                    "UPDATE nominees SET identity_mismatches = ?, status = ? WHERE id = ?",
                    (count, "suspended" if suspended else row["status"], nominee_id),
                )
                self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}",
                            "nominee.identity_mismatch",
                            {"fields": mismatches, "suspended": suspended})
                if suspended:
                    self._notify_owner(db, row["case_id"],
                                       f"Security alert: nominee {row['full_name']} was suspended "
                                       "after repeated identity mismatches.")
            raise ValidationError(
                "Identity details do not match what the owner registered"
                + (" — account suspended" if suspended else "")
            )
        file_name, content = self._decode_upload(data)
        document = self.service.upload_document(
            row["case_id"],
            {"file_name": file_name, "category": "identity_document", "content_base64": content},
        )
        relationship_doc = None
        if not row["id_last4_hash"]:
            # No owner-registered hint (Discovery claimant): relationship proof is mandatory.
            rel_name, rel_content = self._decode_upload(data, "relationship_")
            relationship_doc = self.service.upload_document(
                row["case_id"],
                {"file_name": rel_name, "category": "other", "content_base64": rel_content},
            )["id"]
        with self._connect() as db:
            db.execute(
                """UPDATE nominees SET identity_status = 'pending_review',
                   identity_document_id = ?, relationship_document_id = ? WHERE id = ?""",
                (document["id"], relationship_doc, nominee_id),
            )
            self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}",
                        "nominee.identity_submitted",
                        {"document_id": document["id"],
                         "relationship_document": bool(relationship_doc),
                         "hint_checked": bool(row["id_last4_hash"])})
            return self._public_nominee(self._nominee(db, nominee_id))

    # ====================================================== RELEASE REQUESTS
    def _request(self, db: Any, case_id: str, request_id: str) -> Any:
        row = db.execute("SELECT * FROM release_requests WHERE id = ? AND case_id = ?",
                         (request_id, case_id)).fetchone()
        if row is None:
            raise NotFoundError("Release request not found")
        return row

    def _risk(self, db: Any, request: Any) -> dict[str, Any]:
        case_id = request["case_id"]
        policy = self._policy(db, case_id)
        flags: list[dict[str, str]] = []
        opened = _utc(request["opened_at"])
        if policy["last_owner_checkin"]:
            checkin = _utc(policy["last_owner_checkin"])
            if (opened - checkin) < timedelta(days=policy["checkin_interval_days"]):
                days = max(0, (opened - checkin).days)
                flags.append({"level": "high", "code": "owner_recently_active",
                              "message": f"Owner checked in {days} day(s) before the death claim"})
            if request["date_of_death"]:
                dod = datetime.strptime(request["date_of_death"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if checkin.date() > dod.date():
                    flags.append({"level": "high", "code": "checkin_after_death",
                                  "message": "Owner checked in after the claimed date of death"})
        if request["date_of_death"]:
            dod = datetime.strptime(request["date_of_death"], "%Y-%m-%d").date()
            if dod > datetime.now(timezone.utc).date():
                flags.append({"level": "high", "code": "future_death_date",
                              "message": "Date of death is in the future"})
        if not request["certificate_name_match"]:
            flags.append({"level": "medium", "code": "certificate_name_mismatch",
                          "message": "The subject's name was not found in the certificate text"})
        initiator = self._nominee(db, request["initiated_by"])
        if (opened - _utc(initiator["created_at"])) < timedelta(days=30):
            flags.append({"level": "medium", "code": "recent_nominee",
                          "message": "Claim opened by a nominee added less than 30 days earlier"})
        noisy = db.execute(
            """SELECT COUNT(*) FROM nominees WHERE case_id = ?
               AND (identity_mismatches > 0 OR status = 'suspended')""", (case_id,)
        ).fetchone()[0]
        if noisy:
            flags.append({"level": "medium", "code": "identity_mismatches",
                          "message": f"{noisy} nominee(s) had identity mismatches or suspension"})
        failed_otps = db.execute(
            """SELECT COUNT(*) FROM legacy_audit WHERE case_id = ?
               AND action IN ('nominee.otp_failed', 'nominee.login_failed')""", (case_id,)
        ).fetchone()[0]
        if failed_otps >= 5:
            flags.append({"level": "medium", "code": "auth_failures",
                          "message": f"{failed_otps} failed OTP/sign-in attempts on this case"})
        shared = db.execute(
            """SELECT COUNT(*) FROM (SELECT phone_bidx FROM nominees
               WHERE case_id = ? AND status != 'revoked' AND phone_bidx IS NOT NULL
               GROUP BY phone_bidx HAVING COUNT(*) > 1) dup""", (case_id,)
        ).fetchone()[0]
        if shared:
            flags.append({"level": "high", "code": "shared_phone",
                          "message": "Two or more nominees registered the same phone number, "
                                     "so OTP confirmations are not independent"})
        disputes = db.execute(
            """SELECT COUNT(*) FROM release_attestations
               WHERE request_id = ? AND decision = 'dispute'""", (request["id"],)
        ).fetchone()[0]
        if disputes:
            flags.append({"level": "high", "code": "dispute",
                          "message": f"{disputes} nominee(s) disputed this death claim"})
        if policy["quorum"] < 2:
            flags.append({"level": "low", "code": "single_quorum",
                          "message": "Policy requires only one nominee confirmation"})
        score = min(100, sum(RISK_WEIGHTS[f["level"]] for f in flags))
        level = ("high" if any(f["level"] == "high" for f in flags) or score >= 40
                 else "medium" if score >= 20 else "low")
        return {"score": score, "level": level, "flags": flags}

    def _gates(self, db: Any, request: Any) -> list[dict[str, Any]]:
        policy = self._policy(db, request["case_id"])
        confirmations = db.execute(
            """SELECT a.nominee_id FROM release_attestations a JOIN nominees n ON n.id = a.nominee_id
               WHERE a.request_id = ? AND a.decision = 'confirm'""", (request["id"],)
        ).fetchall()
        verified_confirms = sum(
            self._verified(self._nominee(db, r["nominee_id"])) for r in confirmations
        )
        disputes = db.execute(
            "SELECT COUNT(*) FROM release_attestations WHERE request_id = ? AND decision = 'dispute'",
            (request["id"],),
        ).fetchone()[0]
        waiting_left = (_utc(request["waiting_until"]) - datetime.now(timezone.utc)).total_seconds()
        risk = self._risk(db, request)
        legal = self._legal_status(db, request)
        return [
            {"key": "certificate", "label": "Death certificate approved by reviewer",
             "passed": request["certificate_review"] == "approved"},
            {"key": "quorum", "label": f"Verified nominee confirmations {verified_confirms}/{policy['quorum']}",
             "passed": verified_confirms >= policy["quorum"]},
            {"key": "no_dispute", "label": "No nominee dispute", "passed": disputes == 0},
            {"key": "waiting", "label": "Cooling-off period elapsed"
             + ("" if waiting_left <= 0 else f" ({int(waiting_left // 3600)}h {int(waiting_left % 3600 // 60)}m left)"),
             "passed": waiting_left <= 0},
            {"key": "liveness", "label": "Owner has not vetoed (no proof of life)",
             "passed": request["status"] != "vetoed"},
            {"key": "risk", "label": f"Fraud risk acceptable ({risk['level']})"
             + (" — reviewer override recorded" if request["risk_override_note"] and risk["level"] == "high" else ""),
             "passed": risk["level"] != "high" or bool(request["risk_override_note"])},
            {"key": "legal_documents",
             "label": (f"Legal documents for {legal['country_name']} approved "
                       f"{legal['satisfied']}/{legal['total']}") if legal["mode"] == "required" else
                      (f"Legal documents for {legal['country_name']} (advisory: "
                       f"{legal['satisfied']}/{legal['total']} approved)"),
             "passed": legal["mode"] == "advisory" or legal["satisfied"] >= legal["total"]},
        ]

    def _legal_status(self, db: Any, request: Any) -> dict[str, Any]:
        rules = self._rules(db, request["case_id"])
        mode = self._legal_mode(db, request["case_id"])
        rows = [dict(r) for r in db.execute(
            """SELECT l.id, l.requirement_key, l.document_type, l.status, l.document_id,
                      l.created_at, l.reviewed_at, d.file_name
               FROM release_legal_documents l LEFT JOIN documents d ON d.id = l.document_id
               WHERE l.request_id = ? ORDER BY l.created_at""", (request["id"],))]
        requirements = []
        for item in rules.legal_requirements:
            mine = [r for r in rows if r["requirement_key"] == item.key]
            requirements.append({
                "key": item.key, "label": item.label, "why": item.why,
                "accepted": list(item.accepted),
                "satisfied": any(r["status"] == "approved" for r in mine),
                "pending": any(r["status"] == "pending_review" for r in mine),
            })
        return {"mode": mode, "country": rules.code, "country_name": rules.name,
                "requirements": requirements, "documents": rows,
                "satisfied": sum(r["satisfied"] for r in requirements),
                "total": len(requirements)}

    def _request_view(self, db: Any, request: Any,
                      viewer: str | None = None) -> dict[str, Any]:
        attestations = [dict(r) for r in db.execute(
            """SELECT n.full_name, n.role, a.decision, a.created_at, a.nominee_id
               FROM release_attestations a JOIN nominees n ON n.id = a.nominee_id
               WHERE a.request_id = ?""", (request["id"],))]
        view = {
            "id": request["id"],
            "status": request["status"],
            "certificate_review": request["certificate_review"],
            "certificate_document_id": request["certificate_document_id"],
            "date_of_death": request["date_of_death"],
            "opened_at": request["opened_at"],
            "waiting_until": request["waiting_until"],
            "closed_at": request["closed_at"],
            "close_reason": request["close_reason"],
            "attestations": [{k: a[k] for k in ("full_name", "role", "decision", "created_at")}
                             for a in attestations],
            "gates": self._gates(db, request),
            "risk": self._risk(db, request),
            "legal": self._legal_status(db, request),
        }
        if viewer:
            view["you_attested"] = any(a["nominee_id"] == viewer for a in attestations)
        return view

    @staticmethod
    def _nominee_safe(view: dict[str, Any]) -> dict[str, Any]:
        """Nominees see gate status but not fraud-signal details."""
        view = dict(view)
        risk = view.pop("risk", None)
        view.pop("certificate_document_id", None)
        if "legal" in view:
            view["legal"] = {**view["legal"], "documents": [
                {k: v for k, v in d.items() if k != "document_id"} for d in view["legal"]["documents"]]}
        if risk:
            view["risk_level"] = risk["level"]
        return view

    def open_release_request(self, token: str | None, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token)
        nominee_id = session["nominee_id"]
        self._rate(f"release:{nominee_id}", 3, 3600)
        with self._connect() as db:
            row = self._nominee(db, nominee_id)
            if not self._verified(row):
                raise ForbiddenError("Complete every verification step before opening a claim")
            existing = db.execute(
                """SELECT 1 FROM release_requests WHERE case_id = ?
                   AND status IN ('open', 'frozen', 'released')""", (row["case_id"],)
            ).fetchone()
            if existing:
                raise ConflictError("A release request is already open or completed for this case")
            case = self.service._case_row(db, row["case_id"])
        date_of_death = data.get("date_of_death")
        try:
            datetime.strptime(date_of_death or "", "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError("date_of_death is required (YYYY-MM-DD)") from exc
        file_name, content = self._decode_upload(data)
        raw = base64.b64decode(content)
        text = self.service._document_text(file_name, "", raw)
        name_match = case["subject_name"].casefold() in text.casefold()
        document = self.service.upload_document(
            row["case_id"],
            {"file_name": file_name, "category": "death_certificate",
             "content_base64": content, "date_of_death": date_of_death},
        )
        request_id = new_id()
        with self._connect() as db:
            policy = self._policy(db, row["case_id"])
            db.execute(
                """INSERT INTO release_requests
                   (id, case_id, initiated_by, status, certificate_document_id,
                    certificate_name_match, date_of_death, opened_at, waiting_until)
                   VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
                (request_id, row["case_id"], nominee_id, document["id"], int(name_match),
                 date_of_death, now(), _in(timedelta(hours=policy["waiting_period_hours"]))),
            )
            db.execute(
                """INSERT INTO release_attestations (request_id, nominee_id, decision, created_at)
                   VALUES (?, ?, 'confirm', ?)""", (request_id, nominee_id, now()))
            self._notify_owner(
                db, row["case_id"],
                f"IMPORTANT: {row['full_name']} has reported your death to Trove and requested "
                "release of your legacy vault. If you are alive, sign in and press "
                "'I am alive' to cancel this immediately.")
            self._notify_nominees(
                db, row["case_id"],
                f"{row['full_name']} opened a legacy release request. Sign in to confirm or "
                "dispute it.", exclude=nominee_id)
            self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}", "release.opened",
                        {"request_id": request_id, "certificate_document_id": document["id"],
                         "name_match": name_match})
            return self._nominee_safe(
                self._request_view(db, self._request(db, row["case_id"], request_id), nominee_id))

    def attest(self, token: str | None, request_id: str, data: dict[str, Any]) -> dict[str, Any]:
        session = self.session(token)
        self._require_step_up(session)
        decision = data.get("decision")
        if decision not in ("confirm", "dispute"):
            raise ValidationError("decision must be confirm or dispute")
        nominee_id = session["nominee_id"]
        with self._connect() as db:
            row = self._nominee(db, nominee_id)
            if not self._verified(row):
                raise ForbiddenError("Only fully verified nominees can attest")
            request = self._request(db, row["case_id"], request_id)
            if request["status"] not in ("open", "frozen"):
                raise ConflictError("This request is closed")
            try:
                db.execute(
                    """INSERT INTO release_attestations (request_id, nominee_id, decision, created_at)
                       VALUES (?, ?, ?, ?)""", (request_id, nominee_id, decision, now()))
            except DBIntegrityError as exc:
                raise ConflictError("You have already responded to this request") from exc
            if decision == "dispute":
                db.execute("UPDATE release_requests SET status = 'frozen' WHERE id = ?", (request_id,))
                self._notify_owner(db, row["case_id"],
                                   f"A nominee ({row['full_name']}) disputed the death claim. "
                                   "Release is frozen pending review.")
            self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}", "release.attested",
                        {"request_id": request_id, "decision": decision})
        return self._nominee_safe(self.evaluate(row["case_id"], request_id))

    def evaluate(self, case_id: str, request_id: str) -> dict[str, Any]:
        with self._connect() as db:
            request = self._request(db, case_id, request_id)
            if request["status"] == "open":
                gates = self._gates(db, request)
                if all(g["passed"] for g in gates):
                    db.execute(
                        """UPDATE release_requests SET status = 'released', closed_at = ?,
                           close_reason = 'All release gates passed' WHERE id = ?""",
                        (now(), request_id),
                    )
                    self._notify_nominees(db, case_id,
                                          "The legacy vault has been released. Sign in with MFA "
                                          "to view the items the owner shared with you.")
                    self._audit(db, case_id, "system", "release.granted",
                                {"request_id": request_id, "gates": [g["key"] for g in gates]})
                    request = self._request(db, case_id, request_id)
            return self._request_view(db, request)

    def evaluate_for_nominee(self, token: str | None, request_id: str) -> dict[str, Any]:
        session = self.session(token)
        return self._nominee_safe(self.evaluate(session["case_id"], request_id))

    # ===================================================== LEGAL DOCUMENTS
    def submit_legal_document(self, token: str | None, request_id: str,
                              data: dict[str, Any]) -> dict[str, Any]:
        """A verified nominee attaches a country-specific legal document to a release request."""
        session = self.session(token)
        nominee_id = session["nominee_id"]
        self._rate(f"legal:{nominee_id}", 10, 3600)
        with self._connect() as db:
            row = self._nominee(db, nominee_id)
            if not self._verified(row):
                raise ForbiddenError("Only fully verified nominees can add legal documents")
            request = self._request(db, row["case_id"], request_id)
            if request["status"] not in ("open", "frozen"):
                raise ConflictError("This request is closed")
            rules = self._rules(db, row["case_id"])
        requirement = next((r for r in rules.legal_requirements
                            if r.key == data.get("requirement_key")), None)
        if requirement is None:
            raise ValidationError("requirement_key must be one of "
                                  + ", ".join(r.key for r in rules.legal_requirements))
        if data.get("document_type") not in requirement.accepted:
            raise ValidationError("document_type must be one of the accepted documents for "
                                  f"'{requirement.label}'")
        file_name, content = self._decode_upload(data)
        document = self.service.upload_document(
            row["case_id"], {"file_name": file_name, "category": "legal_document",
                             "content_base64": content})
        legal_id = new_id()
        with self._connect() as db:
            db.execute(
                """INSERT INTO release_legal_documents
                   (id, request_id, case_id, requirement_key, document_type, document_id,
                    uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (legal_id, request_id, row["case_id"], requirement.key, data["document_type"],
                 document["id"], nominee_id, now()))
            self._audit(db, row["case_id"], f"nominee:{nominee_id[:8]}", "release.legal_document_added",
                        {"request_id": request_id, "requirement": requirement.key,
                         "document_type": data["document_type"], "document_id": document["id"]})
            return self._nominee_safe(
                self._request_view(db, self._request(db, row["case_id"], request_id), nominee_id))

    def review_legal_document(self, case_id: str, request_id: str, legal_id: str,
                              data: dict[str, Any], actor: str = "reviewer") -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("approve", "reject"):
            raise ValidationError("decision must be approve or reject")
        with self._connect() as db:
            request = self._request(db, case_id, request_id)
            if request["status"] not in ("open", "frozen"):
                raise ConflictError("This request is closed")
            row = db.execute(
                "SELECT * FROM release_legal_documents WHERE id = ? AND request_id = ?",
                (legal_id, request_id)).fetchone()
            if row is None:
                raise NotFoundError("Legal document not found")
            if row["status"] != "pending_review":
                raise ConflictError("This legal document was already reviewed")
            status = "approved" if decision == "approve" else "rejected"
            db.execute(
                """UPDATE release_legal_documents SET status = ?, reviewed_by = ?, reviewed_at = ?
                   WHERE id = ?""", (status, actor, now(), legal_id))
            db.execute("UPDATE documents SET review_status = ? WHERE id = ?",
                       ("reviewed" if status == "approved" else "rejected", row["document_id"]))
            self._audit(db, case_id, actor, "release.legal_document_reviewed",
                        {"request_id": request_id, "legal_document_id": legal_id,
                         "requirement": row["requirement_key"], "decision": status})
            if status == "rejected":
                self._notify_nominees(db, case_id, "A legal document on the release request was "
                                      "rejected by the reviewer. Sign in to upload a replacement.")
        return self.evaluate(case_id, request_id)

    def compliance(self, case_id: str) -> dict[str, Any]:
        """Country rules for a case, with deadlines computed from the date of death."""
        settings = config.current()
        with self._connect() as db:
            case = self.service._case_row(db, case_id)
            rules = self._rules(db, case_id)
            mode = self._legal_mode(db, case_id)
            latest = db.execute(
                """SELECT date_of_death FROM release_requests WHERE case_id = ?
                   AND date_of_death IS NOT NULL ORDER BY opened_at DESC LIMIT 1""",
                (case_id,)).fetchone()
        date_of_death = case["date_of_death"] or (latest["date_of_death"] if latest else None)
        region = settings.data_region or None
        return {
            "case_id": case_id,
            "country": rules.code,
            "jurisdiction": case["jurisdiction"],
            "profile": rules.as_dict(),
            "legal_documents_mode": mode,
            "date_of_death": date_of_death,
            "deadlines": jurisdictions.deadlines(rules.code, date_of_death),
            "data_residency": {
                "deployment_region": region,
                "recommended_region": rules.data_region,
                "enforced": settings.enforce_residency,
                "matches": region is None or rules.data_region in ("any", region),
                "note": rules.residency_note,
            },
        }

    # ======================================================== RELEASED VAULT
    def _released(self, db: Any, case_id: str) -> bool:
        return db.execute(
            "SELECT 1 FROM release_requests WHERE case_id = ? AND status = 'released'", (case_id,)
        ).fetchone() is not None

    def vault_view(self, token: str | None) -> dict[str, Any]:
        session = self.session(token)
        with self._connect() as db:
            row = self._nominee(db, session["nominee_id"])
            if not self._verified(row) or not self._released(db, row["case_id"]):
                raise ForbiddenError("The vault is sealed until every release gate passes")
            allowed = set(json.loads(row["allowed_categories_json"]))
            everything = row["role"] == "executor"
            documents = [dict(d) for d in db.execute(
                """SELECT id, file_name, category, sha256, created_at FROM documents
                   WHERE case_id = ? AND category NOT IN ('identity_document', 'legal_document')
                   ORDER BY created_at""", (row["case_id"],))
                if everything or d["category"] in allowed]
            assets = [dict(a) for a in db.execute(
                """SELECT asset_type, institution, masked_reference, description, status
                   FROM assets WHERE case_id = ? AND status IN ('registered', 'confirmed')""",
                (row["case_id"],))]
            beneficiaries = [dict(b) for b in db.execute(
                """SELECT name, relationship, share_percent, beneficiary_type
                   FROM beneficiaries WHERE case_id = ?""", (row["case_id"],))]
            self._audit(db, row["case_id"], f"nominee:{row['id'][:8]}", "vault.viewed",
                        {"documents": len(documents)})
        return {"role": row["role"], "full_access": everything, "documents": documents,
                "assets": assets, "beneficiaries": beneficiaries}

    def vault_download(self, token: str | None, document_id: str) -> tuple[dict[str, Any], bytes]:
        session = self.session(token)
        self._require_step_up(session)
        view = self.vault_view(token)
        if document_id not in {d["id"] for d in view["documents"]}:
            raise ForbiddenError("This document was not shared with you")
        metadata, content = self.service.get_document(session["case_id"], document_id)
        with self._connect() as db:
            self._audit(db, session["case_id"], f"nominee:{session['nominee_id'][:8]}",
                        "vault.downloaded", {"document_id": document_id})
        return metadata, content
