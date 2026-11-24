"""SQLite persistence and application service for Trove cases."""

from __future__ import annotations

import json
import hashlib
import hmac
import base64
import difflib
import secrets
import re
import sqlite3
import uuid
import io
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trove_agent.core.db import IntegrityError as DBIntegrityError
from trove_agent.agents.rules import CaseManagerAgent, DiscoveryAgent, DocumentAgent, mask_reference


class TroveError(Exception):
    status_code = 400


class NotFoundError(TroveError):
    status_code = 404


class ConflictError(TroveError):
    status_code = 409


class ValidationError(TroveError):
    status_code = 422


class IntegrationError(TroveError):
    status_code = 502


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class TroveService:
    def __init__(
        self,
        database_path: str | Path = "trove.db",
        llm_agent: Any | None = None,
        mock_email_connector: Any | None = None,
        assistant_agent: Any | None = None,
    ) -> None:
        from trove_agent.core.db import Database

        self.database_path = str(database_path)
        self.db = Database(self.database_path)
        self.document_agent = DocumentAgent()
        self.discovery_agent = DiscoveryAgent()
        self.case_manager = CaseManagerAgent()
        if llm_agent is None:
            from trove_agent.agents.llm import MistralDiscoveryAgent

            llm_agent = MistralDiscoveryAgent()
        if mock_email_connector is None:
            from trove_agent.discovery.mock_email import MockEmailConnector

            mock_email_connector = MockEmailConnector()
        if assistant_agent is None:
            from trove_agent.agents.llm import TroveAssistantAgent

            assistant_agent = TroveAssistantAgent()
        self.llm_agent = llm_agent
        self.mock_email_connector = mock_email_connector
        self.assistant_agent = assistant_agent
        from trove_agent.core.vault import LocalVault

        self.vault = LocalVault(self.database_path)
        from trove_agent.core.security import Pepper

        self.pepper = Pepper(self.vault.root)
        self._initialize()

    def _connect(self) -> Any:
        return self.db.connect()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK (mode IN ('plan', 'discovery')),
                    subject_name TEXT NOT NULL,
                    subject_dob TEXT,
                    jurisdiction TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    death_status TEXT NOT NULL DEFAULT 'not_provided',
                    death_certificate_id TEXT,
                    date_of_death TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    file_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    external_ref TEXT,
                    text_sha256 TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS people (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    full_name TEXT NOT NULL,
                    person_type TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    date_of_birth TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    file_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'pending_review',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    evidence_id TEXT REFERENCES evidence(id),
                    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    asset_type TEXT NOT NULL,
                    institution TEXT,
                    masked_reference TEXT,
                    description TEXT NOT NULL,
                    confidence REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS beneficiaries (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    share_percent REAL,
                    beneficiary_type TEXT NOT NULL DEFAULT 'primary',
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    notes TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    from_person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    to_person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    relationship_type TEXT NOT NULL,
                    evidence_document_id TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, from_person_id, to_person_id, relationship_type)
                );
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
                    institution TEXT,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    next_action TEXT NOT NULL DEFAULT '',
                    due_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_users (
                    id TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cases_created ON cases(created_at);
                CREATE INDEX IF NOT EXISTS idx_assets_case ON assets(case_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id);
                CREATE INDEX IF NOT EXISTS idx_people_case ON people(case_id);
                CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id);
                CREATE INDEX IF NOT EXISTS idx_claims_case ON claims(case_id);
                """
            )
            migrations = {
                "evidence": {"external_ref": "TEXT"},
                "assets": {"document_id": "TEXT REFERENCES documents(id) ON DELETE CASCADE"},
                "app_users": {"email_bidx": "TEXT", "email_enc": "TEXT",
                              "role": "TEXT NOT NULL DEFAULT 'owner'",
                              "failed_logins": "INTEGER NOT NULL DEFAULT 0",
                              "locked_until": "TEXT"},
                "auth_sessions": {"user_id": "TEXT NOT NULL DEFAULT 'local-owner'"},
                "cases": {
                    "owner_id": "TEXT NOT NULL DEFAULT 'local-owner'",
                    "death_status": "TEXT NOT NULL DEFAULT 'not_provided'",
                    "death_certificate_id": "TEXT",
                    "date_of_death": "TEXT",
                    "country": "TEXT",
                },
                "beneficiaries": {
                    "person_id": "TEXT REFERENCES people(id) ON DELETE SET NULL",
                    "beneficiary_type": "TEXT NOT NULL DEFAULT 'primary'",
                    "verification_status": "TEXT NOT NULL DEFAULT 'unverified'",
                    "notes": "TEXT",
                },
            }
            for table, additions in migrations.items():
                columns = db.columns(table)
                for column, definition in additions.items():
                    if column not in columns:
                        db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
            db.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")
            # Backfill the country code for cases created with free-text jurisdictions only.
            from trove_agent.core.jurisdictions import detect_country

            for row in db.execute("SELECT id, jurisdiction FROM cases WHERE country IS NULL").fetchall():
                db.execute("UPDATE cases SET country = ? WHERE id = ?",
                           (detect_country(row["jurisdiction"]), row["id"]))
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_email
                   ON app_users(email_bidx) WHERE email_bidx IS NOT NULL"""
            )
            if db.dialect == "sqlite" and self.database_path != ":memory:":
                db.execute("PRAGMA journal_mode = WAL")
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_external_ref
                   ON evidence(case_id, external_ref)
                   WHERE external_ref IS NOT NULL"""
            )

    def _audit(
        self, db: sqlite3.Connection, case_id: str, action: str, details: dict[str, Any]
    ) -> None:
        db.execute(
            """INSERT INTO audit_events
               (id, case_id, actor, action, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), case_id, "local-user", action, json.dumps(details), now()),
        )

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return "pbkdf2_sha256${}${}".format(
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            scheme, salt_text, digest_text = encoded.split("$")
            if scheme != "pbkdf2_sha256":
                return False
            salt = base64.urlsafe_b64decode(salt_text)
            expected = base64.urlsafe_b64decode(digest_text)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ------------------------------------------------------ field crypto
    def encrypt_field(self, value: str | None) -> str | None:
        if value is None:
            return None
        fernet, _ = self.vault._fernet()
        return "enc1:" + fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_field(self, value: str | None) -> str | None:
        if value is None or not str(value).startswith("enc1:"):
            return value  # legacy plaintext rows stay readable
        fernet, invalid = self.vault._fernet()
        try:
            return fernet.decrypt(value[5:].encode("ascii")).decode("utf-8")
        except invalid as exc:
            raise IntegrationError(
                "Encrypted field could not be decrypted (wrong TROVE_VAULT_KEY?)") from exc

    def blind_index(self, purpose: str, value: str) -> str:
        return self.pepper.keyed_hash(purpose, value)

    # ------------------------------------------------------------ owners
    def auth_status(self, token: str | None = None) -> dict[str, Any]:
        from trove_agent.config import current

        user = self.session_user(token)
        with self._connect() as db:
            users_exist = db.execute("SELECT 1 FROM app_users LIMIT 1").fetchone()
        return {
            "setup_required": users_exist is None,
            "signup_allowed": current().allow_signup,
            "authenticated": user is not None,
            "user": user,
        }

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = self._session_hash(token)
        with self._connect() as db:
            row = db.execute(
                """SELECT u.id, u.role, u.email_enc FROM auth_sessions s
                   JOIN app_users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (token_hash, now()),
            ).fetchone()
            if row is None:
                db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
        email = self.decrypt_field(row["email_enc"])
        masked = (email[:1] + "***@" + email.split("@")[-1]) if email else None
        return {"id": row["id"], "role": row["role"], "email": masked}

    def create_owner(self, password: str, email: str | None = None) -> str:
        """First account (setup) or, when sign-up is allowed, another owner."""
        from trove_agent.config import current
        from trove_agent.core.security import normalize_email, password_strength_error

        error = password_strength_error(password)
        if error:
            raise ValidationError(error)
        if email is not None:
            try:
                email = normalize_email(email)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            first = db.execute("SELECT 1 FROM app_users LIMIT 1").fetchone() is None
            if not first and not current().allow_signup:
                raise ConflictError("Sign-up is disabled on this server")
            if not first and email is None:
                raise ConflictError("The local owner account is already set up")
            user_id = "local-owner" if first and email is None else new_id()
            try:
                db.execute(
                    """INSERT INTO app_users
                       (id, password_hash, created_at, email_bidx, email_enc, role)
                       VALUES (?, ?, ?, ?, ?, 'owner')""",
                    (user_id, self._hash_password(password), now(),
                     self.blind_index("owner_email", email) if email else None,
                     self.encrypt_field(email)),
                )
            except DBIntegrityError as exc:
                raise ConflictError("An account with this email already exists") from exc
        return self._create_session(user_id)

    def login(self, password: str, email: str | None = None) -> str:
        from trove_agent.core.security import LOCKOUT_SECONDS, LOCKOUT_THRESHOLD, normalize_email

        with self._connect() as db:
            if email:
                try:
                    bidx = self.blind_index("owner_email", normalize_email(email))
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
                user = db.execute(
                    "SELECT * FROM app_users WHERE email_bidx = ?", (bidx,)).fetchone()
            else:
                user = db.execute(
                    "SELECT * FROM app_users WHERE id = 'local-owner'").fetchone()
            if user is None:
                if db.execute("SELECT 1 FROM app_users LIMIT 1").fetchone() is None:
                    raise ConflictError("Create the first account first")
                raise ValidationError("Invalid email or password")
            if user["locked_until"] and user["locked_until"] > now():
                raise ValidationError("Account temporarily locked after failed attempts; try later")
            if not isinstance(password, str) or not self._verify_password(
                    password, user["password_hash"]):
                failures = user["failed_logins"] + 1
                locked = None
                if failures >= LOCKOUT_THRESHOLD:
                    locked = (datetime.now(timezone.utc)
                              + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
                db.execute(
                    "UPDATE app_users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                    (0 if locked else failures, locked, user["id"]))
                db.commit()
                raise ValidationError("Invalid email or password")
            db.execute(
                "UPDATE app_users SET failed_logins = 0, locked_until = NULL WHERE id = ?",
                (user["id"],))
        return self._create_session(user["id"])

    def _create_session(self, user_id: str = "local-owner") -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        with self._connect() as db:
            db.execute(
                """INSERT INTO auth_sessions (token_hash, expires_at, created_at, user_id)
                   VALUES (?, ?, ?, ?)""",
                (self._session_hash(token), expires, now(), user_id),
            )
            db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now(),))
        return token

    def logout(self, token: str | None) -> None:
        if token:
            with self._connect() as db:
                db.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (self._session_hash(token),),
                )

    def case_owner(self, case_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT owner_id FROM cases WHERE id = ?", (case_id,)).fetchone()
        return row["owner_id"] if row else None

    @staticmethod
    def _case_row(db: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Case {case_id} was not found")
        return row

    @staticmethod
    def _case_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def create_case(self, data: dict[str, Any], owner_id: str = "local-owner") -> dict[str, Any]:
        mode = data.get("mode", "discovery")
        name = data.get("subject_name")
        jurisdiction = data.get("jurisdiction")
        dob = data.get("subject_dob")
        if mode not in ("plan", "discovery"):
            raise ValidationError("mode must be 'plan' or 'discovery'")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValidationError("subject_name is required and must be at most 200 characters")
        from trove_agent import config
        from trove_agent.core import jurisdictions

        if jurisdiction is not None and (not isinstance(jurisdiction, str) or len(jurisdiction) > 100):
            raise ValidationError("jurisdiction must be at most 100 characters")
        if data.get("country") is not None:
            try:
                country = jurisdictions.normalize_country(data["country"])
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        elif isinstance(jurisdiction, str) and jurisdiction.strip():
            country = jurisdictions.detect_country(jurisdiction)
        else:
            raise ValidationError("country is required (one of " + ", ".join(jurisdictions.COUNTRIES) + ")")
        rules = jurisdictions.profile(country)
        if not isinstance(jurisdiction, str) or not jurisdiction.strip():
            jurisdiction = rules.name
        settings = config.current()
        if (settings.enforce_residency and rules.data_region not in ("any", settings.data_region)):
            raise ValidationError(
                f"This deployment stores data in region '{settings.data_region}', but {rules.name} "
                f"cases should be hosted in '{rules.data_region}'. Use the {rules.data_region} deployment.")
        if dob is not None:
            if not isinstance(dob, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
                raise ValidationError("subject_dob must use YYYY-MM-DD format")
            try:
                datetime.strptime(dob, "%Y-%m-%d")
            except ValueError as exc:
                raise ValidationError(
                    "subject_dob must be a valid date in YYYY-MM-DD format"
                ) from exc
        case_id = new_id()
        stage = "intake" if mode == "discovery" else "asset_verification"
        with self._connect() as db:
            db.execute(
                """INSERT INTO cases
                   (id, mode, subject_name, subject_dob, jurisdiction, stage,
                    death_status, created_at, owner_id, country)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case_id,
                    mode,
                    name.strip(),
                    dob,
                    jurisdiction.strip(),
                    stage,
                    "not_provided",
                    now(),
                    owner_id,
                    country,
                ),
            )
            person_id = new_id()
            db.execute(
                """INSERT INTO people
                   (id, case_id, full_name, person_type, date_of_birth,
                    verification_status, created_at)
                   VALUES (?, ?, ?, 'case_subject', ?, 'unverified', ?)""",
                (person_id, case_id, name.strip(), dob, now()),
            )
            self._audit(db, case_id, "case.created", {"mode": mode, "country": country})
            row = self._case_row(db, case_id)
        return self._case_dict(row)

    def list_cases(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if owner_id is None:
                rows = db.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM cases WHERE owner_id = ? ORDER BY created_at DESC",
                    (owner_id,)).fetchall()
            return [self._case_dict(row) for row in rows]

    def get_case(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            return self._case_dict(self._case_row(db, case_id))

    @staticmethod
    def _document_text(file_name: str, mime_type: str, content: bytes) -> str:
        suffix = Path(file_name).suffix.casefold()
        if suffix in (".txt", ".eml", ".csv", ".json"):
            try:
                return content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError("Text files must use UTF-8 encoding") from exc
        if suffix == ".pdf" or mime_type == "application/pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise IntegrationError("PDF reading requires pypdf in requirements.txt") from exc
            try:
                reader = PdfReader(io.BytesIO(content), strict=True)
                if len(reader.pages) > 100:
                    raise ValidationError("PDF must not exceed 100 pages")
                return "\n".join((page.extract_text() or "") for page in reader.pages)[:100_000]
            except ValidationError:
                raise
            except Exception as exc:
                raise ValidationError("Could not read this PDF") from exc
        raise ValidationError(
            "Supported uploads are UTF-8 TXT, EML, CSV, JSON, or text-based PDF"
        )

    def upload_document(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        from trove_agent.core.vault import LocalVault

        file_name = data.get("file_name")
        category = data.get("category")
        encoded = data.get("content_base64")
        date_of_death = data.get("date_of_death")
        allowed_categories = {
            "death_certificate",
            "identity_document",
            "will",
            "trust_deed",
            "property_document",
            "insurance_policy",
            "bank_statement",
            "investment_statement",
            "pension_record",
            "tax_return",
            "medical_record",
            "legal_document",
            "other",
        }
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValidationError("file_name is required")
        clean_name = Path(file_name.replace("\\", "/")).name
        if len(clean_name) > 180 or clean_name in ("", ".", ".."):
            raise ValidationError("file_name is not valid")
        if not isinstance(category, str) or category not in allowed_categories:
            raise ValidationError("Unsupported document category")
        if not isinstance(encoded, str) or len(encoded) > (LocalVault.MAX_FILE_SIZE * 4 // 3 + 16):
            raise ValidationError("content_base64 is required and must be at most 5 MB")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValidationError("content_base64 must be valid base64") from exc
        if not content or len(content) > LocalVault.MAX_FILE_SIZE:
            raise ValidationError("Document must be between 1 byte and 5 MB")
        if date_of_death is not None:
            if not isinstance(date_of_death, str):
                raise ValidationError("date_of_death must use YYYY-MM-DD format")
            try:
                datetime.strptime(date_of_death, "%Y-%m-%d")
            except ValueError as exc:
                raise ValidationError("date_of_death must be a valid YYYY-MM-DD date") from exc
        mime_type = mimetypes.guess_type(clean_name)[0] or "application/octet-stream"
        text = self._document_text(clean_name, mime_type, content)
        document_id = new_id()
        stored_path = self.vault.store(document_id, content)
        created_at = now()
        digest = hashlib.sha256(content).hexdigest()
        try:
            with self._connect() as db:
                case = self._case_row(db, case_id)
                initial_review = "pending_review"
                db.execute(
                    """INSERT INTO documents
                       (id, case_id, file_name, category, mime_type, sha256,
                        stored_path, review_status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        document_id,
                        case_id,
                        clean_name,
                        category,
                        mime_type,
                        digest,
                        stored_path,
                        initial_review,
                        created_at,
                    ),
                )
                if category == "death_certificate":
                    db.execute(
                        """UPDATE cases SET stage = 'death_verification',
                           death_status = 'pending_review',
                           death_certificate_id = ?,
                           date_of_death = COALESCE(?, date_of_death)
                           WHERE id = ?""",
                        (document_id, date_of_death, case_id),
                    )
                self._audit(
                    db,
                    case_id,
                    "document.uploaded",
                    {
                        "document_id": document_id,
                        "category": category,
                        "sha256": digest,
                        "text_extracted": bool(text.strip()),
                    },
                )
        except Exception:
            self.vault.delete(stored_path)
            raise

        assets = []
        if text.strip() and category != "death_certificate":
            analysis = self.document_agent.analyze(
                text[:100_000],
                clean_name,
                case["subject_name"],
                case["subject_dob"],
            )
            with self._connect() as db:
                self._case_row(db, case_id)
                for candidate in self.discovery_agent.discover(analysis):
                    asset_id = new_id()
                    db.execute(
                        """INSERT INTO assets
                           (id, case_id, evidence_id, document_id, asset_type, institution,
                            masked_reference, description, confidence, status, created_at)
                           VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'needs_review', ?)""",
                        (
                            asset_id,
                            case_id,
                            document_id,
                            candidate["asset_type"],
                            candidate["institution"],
                            candidate["masked_reference"],
                            candidate["description"],
                            candidate["confidence"],
                            now(),
                        ),
                    )
                    assets.append(
                        dict(db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone())
                    )
                db.execute(
                    """UPDATE documents SET review_status = 'pending_review'
                       WHERE id = ?""",
                    (document_id,),
                )
                self._audit(
                    db,
                    case_id,
                    "document.analyzed",
                    {"document_id": document_id, "candidate_count": len(assets)},
                )
        return {
            "id": document_id,
            "case_id": case_id,
            "file_name": clean_name,
            "category": category,
            "sha256": digest,
            "review_status": initial_review,
            "extracted_text_length": len(text),
            "raw_document_encrypted_in_vault": True,
            "raw_text_stored_in_database": False,
            "assets_created": assets,
        }

    def list_documents(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            rows = db.execute(
                """SELECT id, case_id, file_name, category, mime_type, sha256,
                   review_status, created_at FROM documents
                   WHERE case_id = ? ORDER BY created_at DESC""",
                (case_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_document(self, case_id: str, document_id: str) -> None:
        with self._connect() as db:
            self._case_row(db, case_id)
            row = db.execute(
                """SELECT stored_path, category FROM documents
                   WHERE id = ? AND case_id = ?""",
                (document_id, case_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Document not found in this case")
            db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            if row["category"] == "death_certificate":
                db.execute(
                    """UPDATE cases SET death_certificate_id = NULL,
                       death_status = 'not_provided', date_of_death = NULL,
                       stage = 'intake'
                       WHERE id = ? AND death_certificate_id = ?""",
                    (case_id, document_id),
                )
            self._audit(db, case_id, "document.deleted", {"document_id": document_id})
        self.vault.delete(row["stored_path"])

    def get_document(self, case_id: str, document_id: str) -> tuple[dict[str, Any], bytes]:
        with self._connect() as db:
            self._case_row(db, case_id)
            row = db.execute(
                "SELECT * FROM documents WHERE id = ? AND case_id = ?",
                (document_id, case_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Document not found in this case")
            metadata = {
                key: row[key]
                for key in (
                    "id",
                    "file_name",
                    "category",
                    "mime_type",
                    "sha256",
                    "review_status",
                    "created_at",
                )
            }
            content = self.vault.read(row["stored_path"])
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), row["sha256"]):
                raise IntegrationError("Vault document digest verification failed")
            self._audit(db, case_id, "document.downloaded", {"document_id": document_id})
            return metadata, content

    def review_death_certificate(
        self, case_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        decision = data.get("decision")
        reviewer = data.get("reviewer")
        if decision not in ("reviewed", "rejected"):
            raise ValidationError("decision must be reviewed or rejected")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValidationError("reviewer name is required and must be at most 200 characters")
        with self._connect() as db:
            case = self._case_row(db, case_id)
            document_id = case["death_certificate_id"]
            if not document_id:
                raise ConflictError("Upload a death certificate before reviewing")
            db.execute(
                "UPDATE documents SET review_status = ? WHERE id = ? AND case_id = ?",
                (decision, document_id, case_id),
            )
            stage = "evidence_review" if decision == "reviewed" else "intake"
            db.execute(
                "UPDATE cases SET death_status = ?, stage = ? WHERE id = ?",
                (decision, stage, case_id),
            )
            self._audit(
                db,
                case_id,
                "death_certificate.reviewed",
                {"document_id": document_id, "decision": decision, "reviewer": reviewer.strip()},
            )
            return {
                "case_id": case_id,
                "death_status": decision,
                "stage": stage,
                "reviewed_by": reviewer.strip(),
                "reviewer_label_only": True,
                "legal_verification": False,
                "human_document_review": True,
            }

    def review_document(self, case_id: str, document_id: str, data: dict[str, Any]) -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("reviewed", "rejected"):
            raise ValidationError("decision must be reviewed or rejected")
        with self._connect() as db:
            self._case_row(db, case_id)
            row = db.execute(
                "SELECT category FROM documents WHERE id = ? AND case_id = ?",
                (document_id, case_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Document not found in this case")
            if row["category"] == "death_certificate":
                raise ConflictError("Use the death-certificate review action")
            db.execute(
                "UPDATE documents SET review_status = ? WHERE id = ?",
                (decision, document_id),
            )
            self._audit(
                db,
                case_id,
                "document.reviewed",
                {"document_id": document_id, "decision": decision},
            )
            return {"id": document_id, "review_status": decision}

    def list_people(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM people WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            people = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM people WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            relationships = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM relationships WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            documents = [
                {
                    key: row[key]
                    for key in (
                        "id", "file_name", "category", "mime_type", "sha256",
                        "review_status", "created_at",
                    )
                }
                for row in db.execute(
                    "SELECT * FROM documents WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            claims = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM claims WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]

    def entity_match_suggestions(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            people = [
                dict(row)
                for row in db.execute(
                    """SELECT id, full_name, person_type, verification_status
                       FROM people WHERE case_id = ? ORDER BY created_at""",
                    (case_id,),
                ).fetchall()
            ]
        suggestions = []
        for index, first in enumerate(people):
            normalized_first = re.sub(r"[^a-z0-9]", "", first["full_name"].casefold())
            for second in people[index + 1 :]:
                normalized_second = re.sub(
                    r"[^a-z0-9]", "", second["full_name"].casefold()
                )
                if not normalized_first or not normalized_second:
                    continue
                score = difflib.SequenceMatcher(
                    None, normalized_first, normalized_second
                ).ratio()
                if score >= 0.88:
                    suggestions.append(
                        {
                            "person_a": first,
                            "person_b": second,
                            "similarity": round(score, 3),
                            "automatic_merge": False,
                            "requires_human_review": True,
                        }
                    )
        return suggestions

    def add_beneficiary(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name, relationship = data.get("name"), data.get("relationship")
        share = data.get("share_percent")
        beneficiary_type = data.get("beneficiary_type", "primary")
        notes = data.get("notes", "")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("name is required")
        if not isinstance(relationship, str) or not relationship.strip() or len(relationship) > 100:
            raise ValidationError("relationship is required and must be at most 100 characters")
        if beneficiary_type not in ("primary", "secondary", "minor", "trust", "corporate"):
            raise ValidationError("Unsupported beneficiary type")
        if not isinstance(notes, str) or len(notes) > 1000:
            raise ValidationError("notes must be text no longer than 1000 characters")
        if share is not None and (
            isinstance(share, bool)
            or not isinstance(share, (int, float))
            or not 0 <= share <= 100
        ):
            raise ValidationError("share_percent must be between 0 and 100")
        beneficiary_id, person_id = new_id(), new_id()
        item = {
            "id": beneficiary_id,
            "case_id": case_id,
            "person_id": person_id,
            "name": name.strip(),
            "relationship": relationship.strip(),
            "share_percent": share,
            "beneficiary_type": beneficiary_type,
            "verification_status": "unverified",
            "notes": notes.strip(),
            "created_at": now(),
        }
        with self._connect() as db:
            self._case_row(db, case_id)
            if share is not None:
                total = db.execute(
                    """SELECT COALESCE(SUM(share_percent), 0) FROM beneficiaries
                       WHERE case_id = ?""",
                    (case_id,),
                ).fetchone()[0]
                if total + share > 100:
                    raise ConflictError("Beneficiary shares for a case cannot exceed 100%")
            db.execute(
                """INSERT INTO people (id, case_id, full_name, person_type, verification_status, created_at)
                   VALUES (?, ?, ?, 'beneficiary', 'unverified', ?)""",
                (person_id, case_id, name.strip(), now()),
            )
            subject = db.execute(
                """SELECT id FROM people WHERE case_id = ?
                   AND person_type IN ('case_subject', 'deceased_subject') LIMIT 1""",
                (case_id,),
            ).fetchone()
            db.execute(
                """INSERT INTO beneficiaries
                   (id, case_id, person_id, name, relationship, share_percent, beneficiary_type,
                    verification_status, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'unverified', ?, ?)""",
                (
                    beneficiary_id,
                    case_id,
                    person_id,
                    name.strip(),
                    relationship.strip(),
                    share,
                    beneficiary_type,
                    notes.strip(),
                    item["created_at"],
                ),
            )
            if subject:
                db.execute(
                    """INSERT INTO relationships
                       (id, case_id, from_person_id, to_person_id, relationship_type, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        new_id(),
                        case_id,
                        subject["id"],
                        person_id,
                        relationship.strip().casefold(),
                        now(),
                    ),
                )
            self._audit(
                db,
                case_id,
                "beneficiary.added",
                {"beneficiary_id": beneficiary_id, "beneficiary_type": beneficiary_type},
            )
        return item

    def review_beneficiary(self, case_id: str, beneficiary_id: str, data: dict[str, Any]) -> dict[str, Any]:
        decision = data.get("decision")
        reviewer = data.get("reviewer")
        if decision not in ("documents_reviewed", "rejected"):
            raise ValidationError("decision must be documents_reviewed or rejected")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValidationError("reviewer name is required and must be at most 200 characters")
        with self._connect() as db:
            self._case_row(db, case_id)
            item = db.execute(
                "SELECT name FROM beneficiaries WHERE id = ? AND case_id = ?",
                (beneficiary_id, case_id),
            ).fetchone()
            if item is None:
                raise NotFoundError("Beneficiary not found")
            db.execute(
                """UPDATE beneficiaries SET verification_status = ?
                   WHERE id = ? AND case_id = ?""",
                (decision, beneficiary_id, case_id),
            )
            db.execute(
                """UPDATE people SET verification_status = ?
                   WHERE id = (SELECT person_id FROM beneficiaries
                               WHERE id = ? AND case_id = ?)""",
                (decision, beneficiary_id, case_id),
            )
            self._audit(
                db,
                case_id,
                "beneficiary.documents_reviewed",
                {
                    "beneficiary_id": beneficiary_id,
                    "decision": decision,
                    "reviewer": reviewer.strip(),
                    "legal_heir_verified": False,
                },
            )
            return {
                "id": beneficiary_id,
                "verification_status": decision,
                "legal_heir_verified": False,
            }

    def relationship_graph(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            self._case_row(db, case_id)
            nodes = []
            for row in db.execute(
                "SELECT id, full_name AS label, person_type AS type, verification_status AS status FROM people WHERE case_id = ?",
                (case_id,),
            ).fetchall():
                nodes.append(dict(row))
            for row in db.execute(
                """SELECT id, asset_type AS label, 'asset' AS type, status
                   FROM assets WHERE case_id = ?""",
                (case_id,),
            ).fetchall():
                nodes.append(dict(row))
            for row in db.execute(
                """SELECT id, file_name AS label, 'document' AS type, review_status AS status
                   FROM documents WHERE case_id = ?""",
                (case_id,),
            ).fetchall():
                nodes.append(dict(row))
            for row in db.execute(
                """SELECT id, file_name AS label, 'evidence' AS type,
                   'analyzed' AS status FROM evidence WHERE case_id = ?""",
                (case_id,),
            ).fetchall():
                nodes.append(dict(row))
            for row in db.execute(
                """SELECT id, COALESCE(institution, 'Unassigned claim') AS label,
                   'claim' AS type, status FROM claims WHERE case_id = ?""",
                (case_id,),
            ).fetchall():
                nodes.append(dict(row))
            edges = [
                dict(row)
                for row in db.execute(
                    """SELECT from_person_id AS source, to_person_id AS target,
                       relationship_type AS label, verification_status AS status
                       FROM relationships WHERE case_id = ?""",
                    (case_id,),
                ).fetchall()
            ]
            subject = db.execute(
                """SELECT id FROM people WHERE case_id = ?
                   AND person_type IN ('case_subject', 'deceased_subject') LIMIT 1""",
                (case_id,),
            ).fetchone()
            if subject:
                for row in db.execute(
                    """SELECT id, review_status FROM documents WHERE case_id = ?""",
                    (case_id,),
                ).fetchall():
                    edges.append(
                        {
                            "source": subject["id"],
                            "target": row["id"],
                            "label": "case_document",
                            "status": row["review_status"],
                        }
                    )
            for row in db.execute(
                """SELECT id, document_id AS source, evidence_id AS evidence_source, status
                   FROM assets WHERE case_id = ?""",
                (case_id,),
            ).fetchall():
                source_id = row["source"] or row["evidence_source"]
                if source_id:
                    edges.append(
                        {
                            "source": source_id,
                            "target": row["id"],
                            "label": "identified_in",
                            "status": row["status"],
                        }
                    )
                elif subject:
                    edges.append(
                        {
                            "source": subject["id"],
                            "target": row["id"],
                            "label": "reported_asset",
                            "status": row["status"],
                        }
                    )
            for row in db.execute(
                """SELECT id, asset_id, status FROM claims
                   WHERE case_id = ? AND asset_id IS NOT NULL""",
                (case_id,),
            ).fetchall():
                edges.append(
                    {
                        "source": row["asset_id"],
                        "target": row["id"],
                        "label": "tracked_by_claim",
                        "status": row["status"],
                    }
                )
            return {"case_id": case_id, "nodes": nodes, "edges": edges}

    def add_claim(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        asset_id = data.get("asset_id")
        institution = data.get("institution")
        next_action = data.get("next_action", "")
        due_date = data.get("due_date")
        if not isinstance(next_action, str) or len(next_action) > 500:
            raise ValidationError("next_action must be text up to 500 characters")
        if institution is not None and (
            not isinstance(institution, str) or len(institution) > 200
        ):
            raise ValidationError("institution must be text no longer than 200 characters")
        if asset_id is not None and not isinstance(asset_id, str):
            raise ValidationError("asset_id must be a string")
        if due_date is not None:
            try:
                datetime.strptime(due_date, "%Y-%m-%d")
            except (TypeError, ValueError) as exc:
                raise ValidationError("due_date must use YYYY-MM-DD format") from exc
        claim_id, timestamp = new_id(), now()
        with self._connect() as db:
            case = self._case_row(db, case_id)
            if case["stage"] not in ("claims", "settlement"):
                raise ConflictError("Move the case to claims before creating a claim record")
            if asset_id:
                asset = db.execute(
                    "SELECT institution FROM assets WHERE id = ? AND case_id = ?",
                    (asset_id, case_id),
                ).fetchone()
                if asset is None:
                    raise NotFoundError("Asset not found in this case")
                institution = institution or asset["institution"]
            db.execute(
                """INSERT INTO claims
                   (id, case_id, asset_id, institution, status, next_action, due_date, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'not_started', ?, ?, ?, ?)""",
                (claim_id, case_id, asset_id, institution, next_action.strip(), due_date, timestamp, timestamp),
            )
            self._audit(db, case_id, "claim.created", {"claim_id": claim_id})
            row = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
            return dict(row)

    def list_claims(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM claims WHERE case_id = ? ORDER BY created_at DESC",
                    (case_id,),
                ).fetchall()
            ]

    def update_claim(self, case_id: str, claim_id: str, data: dict[str, Any]) -> dict[str, Any]:
        statuses = ("not_started", "documents_requested", "submitted", "under_review", "settled", "rejected")
        status, next_action = data.get("status"), data.get("next_action")
        if status not in statuses:
            raise ValidationError("Unsupported claim status")
        if next_action is not None and (
            not isinstance(next_action, str) or len(next_action) > 500
        ):
            raise ValidationError("next_action must be text up to 500 characters")
        with self._connect() as db:
            self._case_row(db, case_id)
            row = db.execute(
                "SELECT * FROM claims WHERE id = ? AND case_id = ?",
                (claim_id, case_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Claim not found")
            db.execute(
                """UPDATE claims SET status = ?, next_action = ?, updated_at = ?
                   WHERE id = ?""",
                (status, next_action if next_action is not None else row["next_action"], now(), claim_id),
            )
            self._audit(db, case_id, "claim.updated", {"claim_id": claim_id, "status": status})
            return dict(db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone())

    def add_evidence(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        file_name, text = data.get("file_name"), data.get("text")
        source_type = data.get("source_type", "user_upload")
        external_ref = data.get("external_ref")
        use_llm = data.get("use_llm", False)
        consent = data.get("consent_to_send_to_mistral", False)
        if not isinstance(use_llm, bool) or not isinstance(consent, bool):
            raise ValidationError("use_llm and consent_to_send_to_mistral must be booleans")
        if use_llm and not consent:
            raise ValidationError(
                "Set consent_to_send_to_mistral=true to send document text to Mistral"
            )
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValidationError("file_name is required")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("text is required")
        if len(text) > 100_000:
            raise ValidationError("text must not exceed 100000 characters")
        if source_type not in (
            "user_upload",
            "email_export",
            "mock_email",
            "cloud_export",
            "manual",
        ):
            raise ValidationError("source_type is not supported")
        if external_ref is not None and (
            source_type != "mock_email"
            or not isinstance(external_ref, str)
            or not external_ref.strip()
            or len(external_ref) > 500
        ):
            raise ValidationError(
                "external_ref is only supported for email evidence and must be a non-empty string"
            )
        with self._connect() as db:
            case = self._case_row(db, case_id)
            if external_ref:
                already_imported = db.execute(
                    """SELECT 1 FROM evidence
                       WHERE case_id = ? AND external_ref = ?""",
                    (case_id, external_ref),
                ).fetchone()
                if already_imported:
                    raise ConflictError("This external email was already imported")
            subject_name, subject_dob = case["subject_name"], case["subject_dob"]
        agent = self.llm_agent if use_llm else self.document_agent
        analysis = agent.analyze(text, file_name.strip(), subject_name, subject_dob)
        with self._connect() as db:
            case = self._case_row(db, case_id)
            evidence_id = new_id()
            created_at = now()
            db.execute(
                """INSERT INTO evidence
                   (id, case_id, file_name, source_type, external_ref, text_sha256,
                    entities_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id,
                    case_id,
                    file_name.strip(),
                    source_type,
                    external_ref,
                    analysis.text_sha256,
                    json.dumps(analysis.entities),
                    created_at,
                ),
            )
            assets = []
            for candidate in self.discovery_agent.discover(analysis):
                asset_id = new_id()
                db.execute(
                    """INSERT INTO assets
                       (id, case_id, evidence_id, asset_type, institution,
                        masked_reference, description, confidence, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        asset_id,
                        case_id,
                        evidence_id,
                        candidate["asset_type"],
                        candidate["institution"],
                        candidate["masked_reference"],
                        candidate["description"],
                        candidate["confidence"],
                        candidate["status"],
                        created_at,
                    ),
                )
                assets.append(self._asset_dict(db.execute(
                    "SELECT * FROM assets WHERE id = ?", (asset_id,)
                ).fetchone()))
            if case["mode"] == "discovery" and case["death_status"] == "reviewed":
                db.execute(
                    "UPDATE cases SET stage = 'evidence_review' WHERE id = ?",
                    (case_id,),
                )
            self._audit(
                db,
                case_id,
                "evidence.analyzed",
                {
                    "evidence_id": evidence_id,
                    "candidate_count": len(assets),
                    "analysis_provider": "mistral" if use_llm else "local_rules",
                },
            )
            return {
                "id": evidence_id,
                "case_id": case_id,
                "file_name": file_name.strip(),
                "source_type": source_type,
                "text_sha256": analysis.text_sha256,
                "entities": analysis.entities,
                "created_at": created_at,
                "assets_created": assets,
                "raw_text_stored": False,
                "analysis_provider": "mistral" if use_llm else "local_rules",
            }

    def mock_email_status(self) -> dict[str, Any]:
        return self.mock_email_connector.status()

    def connect_mock_email(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        email = data.get("email")
        limit = data.get("max_messages", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise ValidationError("max_messages must be an integer between 1 and 25")
        with self._connect() as db:
            case = self._case_row(db, case_id)
        try:
            messages = self.mock_email_connector.list_messages(
                email, limit, case["subject_name"], case_id
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {
            "provider": "mock_email",
            "demo_only": True,
            "email": email.strip().casefold(),
            "messages": [
                {
                    "id": message["id"],
                    "from": message["from"],
                    "date": message["date"],
                    "subject": message["subject"],
                }
                for message in messages
            ],
        }

    def sync_mock_email(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        email = data.get("email")
        message_ids = data.get("message_ids")
        if not isinstance(message_ids, list) or not message_ids:
            raise ValidationError("Select at least one sample email to analyze")
        if len(message_ids) > 25 or any(
            not isinstance(message_id, str) for message_id in message_ids
        ):
            raise ValidationError("message_ids must contain at most 25 string IDs")
        if len(set(message_ids)) != len(message_ids):
            raise ValidationError("message_ids cannot contain duplicates")
        with self._connect() as db:
            case = self._case_row(db, case_id)
        try:
            messages = self.mock_email_connector.list_messages(
                email, 25, case["subject_name"], case_id
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        selected = {message["id"]: message for message in messages}
        if any(message_id not in selected for message_id in message_ids):
            raise ValidationError("A selected email does not belong to this sample inbox")
        processed = []
        skipped = 0
        for message_id in message_ids:
            message = selected[message_id]
            try:
                result = self.add_evidence(
                    case_id,
                    {
                        "file_name": f"Mock email: {message['subject'][:100]}",
                        "source_type": "mock_email",
                        "external_ref": f"mock:{email.strip().casefold()}:{message_id}",
                        "text": message["text"],
                    },
                )
            except ConflictError:
                skipped += 1
                continue
            processed.append(
                {
                    "evidence_id": result["id"],
                    "subject": message["subject"],
                    "assets_created": result["assets_created"],
                }
            )
        with self._connect() as db:
            self._audit(
                db,
                case_id,
                "mock_email.discovery_completed",
                {"messages_analyzed": len(processed), "duplicates_skipped": skipped},
            )
        return {
            "case_id": case_id,
            "provider": "mock_email",
            "demo_only": True,
            "messages_analyzed": len(processed),
            "duplicates_skipped": skipped,
            "results": processed,
            "raw_email_text_stored": False,
            "external_services_used": False,
        }

    def ask_assistant(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        question = data.get("question")
        consent = data.get("consent_to_send_case_summary_to_mistral")
        if consent is not True:
            raise ValidationError(
                "Set consent_to_send_case_summary_to_mistral=true to send the "
                "question and a minimal case summary to Mistral"
            )
        if not isinstance(question, str) or not question.strip():
            raise ValidationError("question is required")
        if len(question) > 5_000:
            raise ValidationError("question must not exceed 5000 characters")
        with self._connect() as db:
            case = self._case_row(db, case_id)
            asset_rows = db.execute(
                "SELECT asset_type, status FROM assets WHERE case_id = ?",
                (case_id,),
            ).fetchall()
            beneficiary_count = db.execute(
                "SELECT COUNT(*) FROM beneficiaries WHERE case_id = ?",
                (case_id,),
            ).fetchone()[0]
            asset_summary: dict[str, dict[str, int]] = {}
            for row in asset_rows:
                status_counts = asset_summary.setdefault(row["asset_type"], {})
                status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
            case_summary = {
                "mode": case["mode"],
                "jurisdiction": case["jurisdiction"],
                "workflow_stage": case["stage"],
                "beneficiary_count": beneficiary_count,
                "assets_by_type_and_status": asset_summary,
            }
        answer = self.assistant_agent.answer(question.strip(), case_summary)
        with self._connect() as db:
            self._case_row(db, case_id)
            self._audit(
                db,
                case_id,
                "assistant.question_answered",
                {"provider": "mistral"},
            )
        return {
            "case_id": case_id,
            "answer": answer,
            "provider": "mistral",
            "case_summary_sent": case_summary,
            "disclaimer": (
                "Informational only; not legal, tax, investment, or financial advice."
            ),
        }

    @staticmethod
    def _asset_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def list_evidence(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            rows = db.execute(
                "SELECT * FROM evidence WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["entities"] = json.loads(item.pop("entities_json"))
                result.append(item)
            return result

    def add_asset(self, case_id: str, data: dict[str, Any]) -> dict[str, Any]:
        asset_type = data.get("asset_type")
        if not isinstance(asset_type, str) or not asset_type.strip():
            raise ValidationError("asset_type is required")
        description = data.get("description", f"User-registered {asset_type.strip()}")
        reference = data.get("reference")
        if reference is not None and not isinstance(reference, str):
            raise ValidationError("reference must be a string")
        institution = data.get("institution")
        if institution is not None and not isinstance(institution, str):
            raise ValidationError("institution must be a string")
        item = {
            "id": new_id(),
            "case_id": case_id,
            "evidence_id": None,
            "asset_type": asset_type.strip(),
            "institution": institution.strip() if institution else None,
            "masked_reference": mask_reference(reference) if reference else None,
            "description": str(description)[:500],
            "confidence": None,
            "status": "registered",
            "created_at": now(),
        }
        with self._connect() as db:
            self._case_row(db, case_id)
            db.execute(
                """INSERT INTO assets
                   (id, case_id, evidence_id, asset_type, institution,
                    masked_reference, description, confidence, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(item[key] for key in (
                    "id", "case_id", "evidence_id", "asset_type", "institution",
                    "masked_reference", "description", "confidence", "status", "created_at"
                )),
            )
            self._audit(db, case_id, "asset.registered", {"asset_id": item["id"]})
        return item

    def list_assets(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            rows = db.execute(
                "SELECT * FROM assets WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            return [self._asset_dict(row) for row in rows]

    def review_asset(
        self, case_id: str, asset_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        decision = data.get("decision")
        if decision not in ("confirmed", "rejected"):
            raise ValidationError("decision must be 'confirmed' or 'rejected'")
        with self._connect() as db:
            self._case_row(db, case_id)
            row = db.execute(
                "SELECT * FROM assets WHERE id = ? AND case_id = ?",
                (asset_id, case_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Asset {asset_id} was not found in case {case_id}")
            if row["status"] not in ("needs_review", "confirmed", "rejected"):
                raise ConflictError("Only discovered candidates can be reviewed")
            db.execute("UPDATE assets SET status = ? WHERE id = ?", (decision, asset_id))
            self._audit(
                db, case_id, "asset.reviewed", {"asset_id": asset_id, "decision": decision}
            )
            return self._asset_dict(
                db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            )

    def list_beneficiaries(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            rows = db.execute(
                "SELECT * FROM beneficiaries WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def advance_workflow(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            case = self._case_row(db, case_id)
            unverified_beneficiaries = db.execute(
                """SELECT COUNT(*) FROM beneficiaries
                   WHERE case_id = ? AND verification_status = 'unverified'""",
                (case_id,),
            ).fetchone()[0]
            unresolved = db.execute(
                """SELECT COUNT(*) FROM assets
                   WHERE case_id = ? AND status = 'needs_review'""",
                (case_id,),
            ).fetchone()[0]
            try:
                next_stage = self.case_manager.next_stage(
                    case["stage"],
                    unresolved,
                    case["death_status"],
                    unverified_beneficiaries,
                )
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            db.execute("UPDATE cases SET stage = ? WHERE id = ?", (next_stage, case_id))
            self._audit(
                db, case_id, "workflow.advanced", {"from": case["stage"], "to": next_stage}
            )
            return {"case_id": case_id, "stage": next_stage}

    def get_workflow(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            case = self._case_row(db, case_id)
            total = db.execute(
                "SELECT COUNT(*) FROM assets WHERE case_id = ?", (case_id,)
            ).fetchone()[0]
            unresolved = db.execute(
                "SELECT COUNT(*) FROM assets WHERE case_id = ? AND status = 'needs_review'",
                (case_id,),
            ).fetchone()[0]
            unverified_beneficiaries = db.execute(
                """SELECT COUNT(*) FROM beneficiaries
                   WHERE case_id = ? AND verification_status = 'unverified'""",
                (case_id,),
            ).fetchone()[0]
            can_advance = (
                unresolved == 0
                and case["stage"] != self.case_manager.STAGES[-1]
                and (case["stage"] != "death_verification" or case["death_status"] == "reviewed")
                and (case["stage"] != "intake" or case["death_status"] == "pending_review")
                and not (self.case_manager.STAGES.index(case["stage"]) + 1 == self.case_manager.STAGES.index("claims") and unverified_beneficiaries)
            )
            return {
                "case_id": case_id,
                "mode": case["mode"],
                "stage": case["stage"],
                "asset_count": total,
                "assets_needing_review": unresolved,
                "death_status": case["death_status"],
                "unverified_beneficiaries": unverified_beneficiaries,
                "can_advance": can_advance,
            }

    def list_audit_events(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            self._case_row(db, case_id)
            rows = db.execute(
                "SELECT * FROM audit_events WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json"))
                result.append(item)
            return result

    def export_case(self, case_id: str) -> dict[str, Any]:
        with self._connect() as db:
            case = self._case_dict(self._case_row(db, case_id))
            evidence_rows = db.execute(
                "SELECT * FROM evidence WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall()
            evidence = []
            for row in evidence_rows:
                item = dict(row)
                item["entities"] = json.loads(item.pop("entities_json"))
                evidence.append(item)
            assets = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM assets WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            beneficiaries = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM beneficiaries WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            people = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM people WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            relationships = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM relationships WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            documents = [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "file_name",
                        "category",
                        "mime_type",
                        "sha256",
                        "review_status",
                        "created_at",
                    )
                }
                for row in db.execute(
                    "SELECT * FROM documents WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            claims = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM claims WHERE case_id = ? ORDER BY created_at",
                    (case_id,),
                ).fetchall()
            ]
            audit = []
            for row in db.execute(
                "SELECT * FROM audit_events WHERE case_id = ? ORDER BY created_at",
                (case_id,),
            ).fetchall():
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json"))
                audit.append(item)
            return {
                "exported_at": now(),
                "case": case,
                "evidence": evidence,
                "assets": assets,
                "beneficiaries": beneficiaries,
                "people": people,
                "relationships": relationships,
                "documents": documents,
                "claims": claims,
                "audit_events": audit,
            }

    def delete_case(self, case_id: str, confirmation: str) -> None:
        if confirmation != "DELETE":
            raise ValidationError("Send confirmation='DELETE' to permanently delete this case")
        with self._connect() as db:
            self._case_row(db, case_id)
            paths = [
                row["stored_path"]
                for row in db.execute(
                    "SELECT stored_path FROM documents WHERE case_id = ?",
                    (case_id,),
                ).fetchall()
            ]
            db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        for path in paths:
            self.vault.delete(path)
        if self.database_path != ":memory:":
            with self._connect() as db:
                db.execute("VACUUM")
