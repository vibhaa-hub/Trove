from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from trove_agent.core import security as sec
from trove_agent.discovery.bootstrap import DiscoveryBootstrap
from trove_agent.services.legacy import AuthError, ForbiddenError, LegacyService, RateLimitError
from trove_agent.core.store import ConflictError, TroveService, ValidationError


class _NoLLM:
    def analyze(self, *args, **kwargs):  # pragma: no cover - not used
        raise AssertionError("LLM must not be called")


def b64(text: str | bytes) -> str:
    return base64.b64encode(text.encode() if isinstance(text, str) else text).decode()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "t.db"
        self.service = TroveService(db, llm_agent=_NoLLM(), assistant_agent=object())
        self.legacy = LegacyService(self.service)
        self.discovery = DiscoveryBootstrap(self.service)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class LegacyReleaseTests(Base):
    PASSWORD = "Correct-Horse-9"

    def _plan_case(self, quorum: int = 2) -> str:
        case = self.service.create_case(
            {"mode": "plan", "subject_name": "Asha Rao", "jurisdiction": "India"})
        self.legacy.update_policy(case["id"], {
            "quorum": quorum, "waiting_period_hours": 0, "checkin_interval_days": 30,
            "owner_email": "asha@example.test", "owner_phone": "+919800000001"})
        return case["id"]

    def _latest_code(self, token: str, channel: str) -> str:
        for message in self.legacy.outbox(token):
            if message["channel"] == channel and message["purpose"] == "otp":
                return message["body"].split("code: ")[1][:6]
        raise AssertionError("no OTP")

    def _totp(self, secret: str, offset: int = 0) -> str:
        return sec.totp_at(secret, sec.current_step() + offset)

    def _onboard(self, case_id: str, name: str, email: str, last4: str = "1234",
                 dob: str = "1990-01-01") -> tuple[str, str, str]:
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": name, "relationship": "child", "role": "primary",
            "email": email, "phone": "+9198" + str(abs(hash(email)) % 10**8).zfill(8),
            "id_last4": last4,
            "date_of_birth": dob, "allowed_categories": ["will"]})
        token = self.legacy.enroll({"invite_code": nominee["invite_code"], "email": email,
                                    "password": self.PASSWORD})
        for channel in ("email", "phone"):
            self.legacy.verify_contact(token, {"channel": channel,
                                               "code": self._latest_code(token, channel)})
        secret = self.legacy.mfa_setup(token)["secret"]
        token = self.legacy.mfa_confirm(token, {"code": self._totp(secret)})
        self.legacy.submit_identity(token, {"id_last4": last4, "date_of_birth": dob,
                                            "file_name": "id.txt", "content_base64": b64("ID card")})
        self.legacy.review_identity(case_id, nominee["id"], {"decision": "approve"})
        return nominee["id"], token, secret

    def test_full_release_requires_every_gate(self) -> None:
        case_id = self._plan_case(quorum=2)
        will = self.service.upload_document(case_id, {
            "file_name": "will.txt", "category": "will", "content_base64": b64("My will")})
        self.service.upload_document(case_id, {
            "file_name": "deed.txt", "category": "property_document",
            "content_base64": b64("Title deed")})
        a_id, a_token, a_secret = self._onboard(case_id, "Ravi Rao", "ravi@example.test")
        b_id, b_token, b_secret = self._onboard(case_id, "Meera Rao", "meera@example.test")

        with self.assertRaises(ForbiddenError):
            self.legacy.vault_view(a_token)

        request = self.legacy.open_release_request(a_token, {
            "file_name": "death.txt", "date_of_death": "2026-01-02",
            "content_base64": b64("Certificate of death for Asha Rao")})
        gates = {g["key"]: g["passed"] for g in request["gates"]}
        self.assertFalse(gates["certificate"])
        self.assertFalse(gates["quorum"])

        # owner alerted on both channels
        overview = self.legacy.overview(case_id)
        alerts = [m for m in overview["outbox"] if m["purpose"] == "owner_alert"]
        self.assertEqual({m["channel"] for m in alerts}, {"email", "phone"})
        # reviewer outbox never exposes nominee OTP codes
        self.assertTrue(all("******" in m["body"] for m in overview["outbox"] if m["purpose"] == "otp"))

        self.legacy.review_certificate(case_id, request["id"], {"decision": "approve"})
        # India requires proof of legal entitlement before release
        view = self.legacy.submit_legal_document(a_token, request["id"], {
            "requirement_key": "entitlement",
            "document_type": "Legal heir certificate (Tahsildar / revenue authority)",
            "file_name": "heir.txt", "content_base64": b64("Legal heir certificate")})
        legal_gate = next(g for g in view["gates"] if g["key"] == "legal_documents")
        self.assertFalse(legal_gate["passed"])
        self.legacy.review_legal_document(case_id, request["id"], view["legal"]["documents"][0]["id"],
                                          {"decision": "approve"})
        with self.assertRaises(ForbiddenError):  # step-up stale? no: fresh after login; force stale
            with self.service._connect() as db:
                db.execute("UPDATE nominee_sessions SET step_up_at = NULL")
            self.legacy.attest(b_token, request["id"], {"decision": "confirm"})
        self.legacy.step_up(b_token, {"totp": self._totp(b_secret, 1)})
        result = self.legacy.attest(b_token, request["id"], {"decision": "confirm"})
        self.assertEqual(result["status"], "released", result["gates"])

        vault = self.legacy.vault_view(a_token)
        self.assertEqual([d["id"] for d in vault["documents"]], [will["id"]])
        self.legacy.step_up(a_token, {"totp": self._totp(a_secret, 1)})
        _, content = self.legacy.vault_download(a_token, will["id"])
        self.assertEqual(content, b"My will")
        self.assertTrue(self.legacy.verify_audit_chain(case_id)["valid"])

    def test_owner_checkin_vetoes_and_dispute_freezes(self) -> None:
        case_id = self._plan_case(quorum=1)
        _, token, _ = self._onboard(case_id, "Ravi Rao", "ravi@example.test")
        request = self.legacy.open_release_request(token, {
            "file_name": "death.txt", "date_of_death": "2026-01-02",
            "content_base64": b64("Asha Rao death certificate")})
        self.legacy.owner_checkin(case_id)
        view = self.legacy.evaluate(case_id, request["id"])
        self.assertEqual(view["status"], "vetoed")
        with self.assertRaises(ForbiddenError):
            self.legacy.vault_view(token)

    def test_recent_checkin_is_high_risk_and_blocks(self) -> None:
        case_id = self._plan_case(quorum=1)
        self.legacy.owner_checkin(case_id)  # owner alive recently
        _, token, _ = self._onboard(case_id, "Ravi Rao", "ravi@example.test")
        request = self.legacy.open_release_request(token, {
            "file_name": "death.txt", "date_of_death": "2026-01-02",
            "content_base64": b64("Asha Rao death certificate")})
        view = self.legacy.review_certificate(case_id, request["id"], {"decision": "approve"})
        self.assertEqual(view["risk"]["level"], "high")
        self.assertEqual(view["status"], "open")
        codes = {f["code"] for f in view["risk"]["flags"]}
        self.assertIn("owner_recently_active", codes)

    def test_identity_mismatch_suspends_after_three(self) -> None:
        case_id = self._plan_case()
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "X", "relationship": "friend", "email": "x@example.test",
            "phone": "+919822222222", "id_last4": "9999"})
        token = self.legacy.enroll({"invite_code": nominee["invite_code"],
                                    "email": "x@example.test", "password": self.PASSWORD})
        for channel in ("email", "phone"):
            self.legacy.verify_contact(token, {"channel": channel,
                                               "code": self._latest_code(token, channel)})
        secret = self.legacy.mfa_setup(token)["secret"]
        token = self.legacy.mfa_confirm(token, {"code": self._totp(secret)})
        for _ in range(3):
            with self.assertRaises(ValidationError):
                self.legacy.submit_identity(token, {"id_last4": "0000", "file_name": "a.txt",
                                                    "content_base64": b64("x")})
        with self.assertRaises(AuthError):
            self.legacy.me(token)

    def test_invite_requires_matching_email_and_is_single_use(self) -> None:
        case_id = self._plan_case()
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "Y", "relationship": "spouse", "email": "y@example.test",
            "phone": "+919833333333"})
        with self.assertRaises(AuthError):
            self.legacy.enroll({"invite_code": nominee["invite_code"],
                                "email": "attacker@example.test", "password": self.PASSWORD})
        self.legacy.enroll({"invite_code": nominee["invite_code"],
                            "email": "y@example.test", "password": self.PASSWORD})
        with self.assertRaises(AuthError):
            self.legacy.enroll({"invite_code": nominee["invite_code"],
                                "email": "y@example.test", "password": self.PASSWORD})

    def test_weak_password_and_lockout(self) -> None:
        case_id = self._plan_case()
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "Z", "relationship": "sibling", "email": "z@example.test",
            "phone": "+919844444444"})
        with self.assertRaises(ValidationError):
            self.legacy.enroll({"invite_code": nominee["invite_code"],
                                "email": "z@example.test", "password": "alllowercase"})
        self.legacy.enroll({"invite_code": nominee["invite_code"],
                            "email": "z@example.test", "password": self.PASSWORD})
        for _ in range(5):
            with self.assertRaises(AuthError):
                self.legacy.login({"email": "z@example.test", "password": "Wrong-Pass-123"})
        with self.assertRaises(RateLimitError):
            self.legacy.login({"email": "z@example.test", "password": self.PASSWORD})

    def test_otp_attempt_counter_persists(self) -> None:
        case_id = self._plan_case()
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "W", "relationship": "child", "email": "w@example.test",
            "phone": "+919855555555"})
        token = self.legacy.enroll({"invite_code": nominee["invite_code"],
                                    "email": "w@example.test", "password": self.PASSWORD})
        real = self._latest_code(token, "email")
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(ValidationError):
                self.legacy.verify_contact(token, {"channel": "email", "code": wrong})
        with self.assertRaises(RateLimitError):
            self.legacy.verify_contact(token, {"channel": "email", "code": real})

    def test_unverified_nominee_cannot_open_claim(self) -> None:
        case_id = self._plan_case()
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "V", "relationship": "child", "email": "v@example.test",
            "phone": "+919866666666"})
        token = self.legacy.enroll({"invite_code": nominee["invite_code"],
                                    "email": "v@example.test", "password": self.PASSWORD})
        with self.assertRaises(ForbiddenError):  # pre-MFA session
            self.legacy.open_release_request(token, {})

    def test_audit_tampering_detected(self) -> None:
        case_id = self._plan_case()
        self.legacy.owner_checkin(case_id)
        with self.service._connect() as db:
            db.execute("UPDATE legacy_audit SET details_json = '{}' WHERE seq = 1")
        self.assertFalse(self.legacy.verify_audit_chain(case_id)["valid"])

    def test_totp_replay_rejected(self) -> None:
        secret = sec.new_totp_secret()
        code = sec.totp_at(secret, sec.current_step())
        step = sec.verify_totp(secret, code, None)
        self.assertIsNotNone(step)
        self.assertIsNone(sec.verify_totp(secret, code, step))


class DiscoveryBootstrapTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.case_id = self.service.create_case(
            {"mode": "discovery", "subject_name": "Asha Rao", "jurisdiction": "India"})["id"]

    def test_starting_plan_from_phone_only(self) -> None:
        result = self.discovery.add_seeds(self.case_id, {
            "have": ["phone_access", "name_dob"], "phones": ["+919800000001"],
            "employers": ["Acme Pvt Ltd"]})
        titles = [s["title"] for s in result["plan"]]
        self.assertTrue(any("SMS" in t for t in titles))
        self.assertEqual(result["region"], "IN")
        self.assertTrue(any(l["institution"] == "Employer: Acme Pvt Ltd" for l in result["leads"]))

    def test_sms_export_and_statement_corroborate(self) -> None:
        xml = (
            '<?xml version="1.0"?><smses>'
            '<sms address="VM-HDFCBK" date="1714600000000" body="Rs 5000 debited from A/c XX4321 '
            'towards LIC premium. Avl Bal 1000"/>'
            '<sms address="AD-ZERODH" date="1714700000000" body="Contract note for your trades '
            'is emailed"/>'
            '</smses>'
        )
        result = self.discovery.ingest(self.case_id, {
            "source_type": "sms_export", "file_name": "sms.xml", "content_base64": b64(xml)})
        names = {l["institution"] for l in result["leads"]}
        self.assertTrue({"HDFC Bank", "LIC", "Zerodha"} <= names, names)
        statement = ("HDFC Bank statement for Asha Rao\n"
                     "02/05 NACH DR LIC PREMIUM 5000\n"
                     "05/05 SIP ICICI Prudential MF 2000\n")
        result = self.discovery.ingest(self.case_id, {
            "source_type": "document", "file_name": "statement.txt", "category": "bank_statement",
            "content_base64": b64(statement)})
        hdfc = next(l for l in result["leads"] if l["institution"] == "HDFC Bank")
        self.assertEqual(hdfc["source_count"], 2)
        self.assertIn("***4321", hdfc["masked_refs"])
        with self.assertRaises(ConflictError):
            self.discovery.ingest(self.case_id, {
                "source_type": "sms_export", "file_name": "sms.xml", "content_base64": b64(xml)})

    def test_email_mbox_and_cloud_zip(self) -> None:
        mbox = (
            "From a@b Mon Jan 1 00:00:00 2024\n"
            "From: Policy Desk <alerts@hdfclife.com>\nSubject: Premium due for policy no 778899\n"
            "Date: Mon, 1 Jan 2024 10:00:00 +0000\n\nYour HDFC Life premium is due.\n\n"
            "From a@b Mon Jan 2 00:00:00 2024\n"
            "From: CAS <cas@camsonline.com>\nSubject: Consolidated Account Statement\n\n"
            "Mutual fund folio 12345678\n"
        )
        result = self.discovery.ingest(self.case_id, {
            "source_type": "email_export", "file_name": "mail.mbox", "content_base64": b64(mbox)})
        self.assertEqual(result["scan"]["items_scanned"], 2)
        names = {l["institution"] for l in result["leads"]}
        self.assertIn("HDFC Life", names)
        self.assertIn("CAMS (mutual fund registrar)", names)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Drive/Last_Will_2019.pdf", b"%PDF-fake")
            archive.writestr("Drive/notes.txt", "Coinbase account and wallet address list")
            archive.writestr("../evil.txt", "should be skipped")
        result = self.discovery.ingest(self.case_id, {
            "source_type": "cloud_export", "file_name": "drive.zip",
            "content_base64": b64(buffer.getvalue())})
        types = {l["asset_type"] for l in result["leads"]}
        self.assertIn("trust", types)
        self.assertIn("crypto", types)
        coverage = {c["category"]: c for c in result["coverage"]}
        self.assertEqual(coverage["property"]["status"], "gap")
        self.assertTrue(coverage["bank_account"]["official_tracing"])
        stored = json.dumps(result)
        self.assertNotIn("Your HDFC Life premium is due", stored)

    def test_promote_lead_creates_reviewable_asset(self) -> None:
        result = self.discovery.ingest(self.case_id, {
            "source_type": "sms_export", "file_name": "s.txt",
            "content_base64": b64("SBIINB: Your A/c XX9876 credited with interest")})
        lead = next(l for l in result["leads"] if l["institution"] == "State Bank of India")
        self.discovery.update_lead(self.case_id, lead["id"], {"decision": "promote"})
        assets = self.service.list_assets(self.case_id)
        self.assertEqual(assets[0]["status"], "needs_review")
        self.assertEqual(assets[0]["masked_reference"], "***9876")


if __name__ == "__main__":
    unittest.main()
