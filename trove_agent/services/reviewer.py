"""Independent reviewer (staff) accounts.

Separation of duties: the person who owns a case or is a nominee on it must
not approve identity documents or death certificates. Reviewers are staff
accounts created from the command line, always use TOTP MFA, and can only
download the specific documents waiting for their review.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from trove_agent.core import security as sec
from trove_agent.core.db import IntegrityError as DBIntegrityError
from trove_agent.services.legacy import AuthError, ForbiddenError, LegacyService, RateLimitError
from trove_agent.core.store import ConflictError, NotFoundError, TroveService, ValidationError, new_id, now

SESSION_HOURS = 8


class ReviewerService:
    def __init__(self, service: TroveService, legacy: LegacyService) -> None:
        self.service, self.legacy = service, legacy
        self.limiter = sec.RateLimiter()
        with service._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS staff_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    totp_secret_enc TEXT NOT NULL,
                    totp_last_step INTEGER,
                    role TEXT NOT NULL DEFAULT 'reviewer',
                    disabled INTEGER NOT NULL DEFAULT 0,
                    failed_logins INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS staff_sessions (
                    token_hash TEXT PRIMARY KEY,
                    staff_id TEXT NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    # ---------------------------------------------------------- accounts
    def create_reviewer(self, username: str, password: str, role: str = "reviewer") -> dict[str, str]:
        if not isinstance(username, str) or not re.fullmatch(r"[a-z0-9._-]{3,40}", username):
            raise ValidationError("username must be 3-40 chars of a-z 0-9 . _ -")
        error = sec.password_strength_error(password)
        if error:
            raise ValidationError(error)
        if role not in ("reviewer", "admin"):
            raise ValidationError("role must be reviewer or admin")
        secret = sec.new_totp_secret()
        fernet, _ = self.service.vault._fernet()
        try:
            with self.service._connect() as db:
                db.execute(
                    """INSERT INTO staff_users (id, username, password_hash, totp_secret_enc, role, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (new_id(), username, self.service._hash_password(password),
                     fernet.encrypt(secret.encode()).decode(), role, now()),
                )
        except DBIntegrityError as exc:
            raise ConflictError("That username already exists") from exc
        return {"username": username, "totp_secret": secret,
                "otpauth_uri": sec.totp_uri(secret, username, "Trove Reviewer")}

    def login(self, data: dict[str, Any], client: str = "local") -> str:
        if not self.limiter.hit(f"staff-login:{client}", 20, 900):
            raise RateLimitError("Too many attempts; wait and retry")
        username = data.get("username")
        password = data.get("password")
        with self.service._connect() as db:
            row = db.execute("SELECT * FROM staff_users WHERE username = ?", (username,)).fetchone()
            if row is None or row["disabled"]:
                raise AuthError("Invalid credentials")
            if row["locked_until"] and row["locked_until"] > now():
                raise RateLimitError("Account temporarily locked")
            fernet, _ = self.service.vault._fernet()
            secret = fernet.decrypt(row["totp_secret_enc"].encode()).decode()
            ok_password = isinstance(password, str) and self.service._verify_password(
                password, row["password_hash"])
            step = sec.verify_totp(secret, data.get("totp"), row["totp_last_step"]) if ok_password else None
            if step is None:
                failures = row["failed_logins"] + 1
                locked = None
                if failures >= sec.LOCKOUT_THRESHOLD:
                    locked = (datetime.now(timezone.utc)
                              + timedelta(seconds=sec.LOCKOUT_SECONDS)).isoformat()
                db.execute("UPDATE staff_users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                           (0 if locked else failures, locked, row["id"]))
                db.commit()
                raise AuthError("Invalid credentials or authenticator code")
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE staff_users SET failed_logins = 0, totp_last_step = ? WHERE id = ?",
                       (step, row["id"]))
            db.execute(
                "INSERT INTO staff_sessions (token_hash, staff_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (self.service._session_hash(token), row["id"],
                 (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(), now()),
            )
            db.execute("DELETE FROM staff_sessions WHERE expires_at <= ?", (now(),))
        return token

    def logout(self, token: str | None) -> None:
        if token:
            with self.service._connect() as db:
                db.execute("DELETE FROM staff_sessions WHERE token_hash = ?",
                           (self.service._session_hash(token),))

    def session(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AuthError("Reviewer sign-in required")
        with self.service._connect() as db:
            row = db.execute(
                """SELECT u.id, u.username, u.role FROM staff_sessions s
                   JOIN staff_users u ON u.id = s.staff_id
                   WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0""",
                (self.service._session_hash(token), now()),
            ).fetchone()
        if row is None:
            raise AuthError("Reviewer session expired")
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

    # ------------------------------------------------------------- queue
    def queue(self, token: str | None) -> dict[str, Any]:
        staff = self.session(token)
        with self.service._connect() as db:
            identities = [dict(r) for r in db.execute(
                """SELECT n.id AS nominee_id, n.case_id, n.full_name, n.relationship, n.role,
                          n.identity_document_id, n.relationship_document_id,
                          n.id_last4_hash IS NOT NULL AS hint_matched, c.subject_name, c.mode
                   FROM nominees n JOIN cases c ON c.id = n.case_id
                   WHERE n.identity_status = 'pending_review' ORDER BY n.created_at""")]
            requests = db.execute(
                """SELECT r.*, c.subject_name FROM release_requests r JOIN cases c ON c.id = r.case_id
                   WHERE r.status IN ('open', 'frozen') AND r.certificate_review = 'pending'
                   ORDER BY r.opened_at""").fetchall()
            certificates = []
            for request in requests:
                view = self.legacy._request_view(db, request)
                view["case_id"] = request["case_id"]
                view["subject_name"] = request["subject_name"]
                certificates.append(view)
            legal = [dict(r) for r in db.execute(
                """SELECT l.id, l.request_id, l.case_id, l.requirement_key, l.document_type,
                          l.document_id, l.created_at, c.subject_name, c.country, n.full_name AS uploaded_by
                   FROM release_legal_documents l
                   JOIN release_requests r ON r.id = l.request_id
                   JOIN cases c ON c.id = l.case_id
                   JOIN nominees n ON n.id = l.uploaded_by
                   WHERE l.status = 'pending_review' AND r.status IN ('open', 'frozen')
                   ORDER BY l.created_at""")]
        from trove_agent.core.jurisdictions import profile

        for item in legal:
            rules = profile(item["country"])
            item["country_name"] = rules.name
            item["requirement_label"] = next(
                (r.label for r in rules.legal_requirements if r.key == item["requirement_key"]),
                item["requirement_key"])
        return {"reviewer": staff, "identity_reviews": identities,
                "certificate_reviews": certificates, "legal_document_reviews": legal}

    def review_identity(self, token: str | None, case_id: str, nominee_id: str,
                        data: dict[str, Any]) -> dict[str, Any]:
        staff = self.session(token)
        return self.legacy.review_identity(case_id, nominee_id, data,
                                           actor=f"reviewer:{staff['username']}")

    def review_certificate(self, token: str | None, case_id: str, request_id: str,
                           data: dict[str, Any]) -> dict[str, Any]:
        staff = self.session(token)
        return self.legacy.review_certificate(case_id, request_id, data,
                                              actor=f"reviewer:{staff['username']}")

    def review_legal_document(self, token: str | None, case_id: str, request_id: str,
                              legal_id: str, data: dict[str, Any]) -> dict[str, Any]:
        staff = self.session(token)
        return self.legacy.review_legal_document(case_id, request_id, legal_id, data,
                                                 actor=f"reviewer:{staff['username']}")

    def download(self, token: str | None, case_id: str, document_id: str) -> tuple[dict[str, Any], bytes]:
        """Only documents currently awaiting review can be opened."""
        self.session(token)
        with self.service._connect() as db:
            allowed = db.execute(
                """SELECT 1 FROM nominees WHERE case_id = ? AND identity_status = 'pending_review'
                     AND (identity_document_id = ? OR relationship_document_id = ?)
                   UNION SELECT 1 FROM release_requests WHERE case_id = ?
                     AND status IN ('open', 'frozen') AND certificate_document_id = ?
                   UNION SELECT 1 FROM release_legal_documents WHERE case_id = ?
                     AND status = 'pending_review' AND document_id = ?""",
                (case_id, document_id, document_id, case_id, document_id, case_id, document_id),
            ).fetchone()
        if allowed is None:
            raise ForbiddenError("Only documents awaiting your review can be opened")
        return self.service.get_document(case_id, document_id)
