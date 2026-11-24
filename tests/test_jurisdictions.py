from __future__ import annotations

import base64
import dataclasses
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TROVE_ENCODER", "hashing")

from trove_agent import config  # noqa: E402
from trove_agent.core import jurisdictions as j  # noqa: E402
from trove_agent.core import security as sec  # noqa: E402
from trove_agent.core.store import TroveService, ValidationError  # noqa: E402
from trove_agent.discovery.bootstrap import DiscoveryBootstrap  # noqa: E402
from trove_agent.services.legacy import LegacyService  # noqa: E402
from trove_agent.services.reviewer import ReviewerService  # noqa: E402

PASSWORD = "Correct-Horse-9x-long"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class DetectCountryTests(unittest.TestCase):
    def test_whole_names_only(self) -> None:
        cases = {
            "India": "IN", "in": "IN", "Maharashtra, India": "IN", "Karnataka": "IN",
            "England": "UK", "Scotland": "UK", "United Kingdom": "UK",
            "California": "US", "New York, USA": "US", "United States": "US",
            "Germany": "EU", "France": "EU",
            "Living in Canada": "OTHER",  # old bug: the word "in" meant India
            "Indiana": "US", "Australia": "OTHER", "": "OTHER", None: "OTHER",
        }
        for text, expected in cases.items():
            self.assertEqual(j.detect_country(text), expected, text)

    def test_every_profile_is_complete(self) -> None:
        for code, rules in j.PROFILES.items():
            self.assertTrue(rules.legal_requirements and rules.privacy.rights, code)
            self.assertIn(rules.legal_documents_default, ("required", "advisory"))
            self.assertTrue({"quorum", "waiting_period_hours", "checkin_interval_days"} <= set(rules.defaults))

    def test_deadline_dates(self) -> None:
        uk = {d["label"]: d["due"] for d in j.deadlines("UK", "2026-01-15")}
        self.assertEqual(uk["Register the death"], "2026-01-20")
        self.assertEqual(uk["Pay Inheritance Tax"], "2026-07-31")  # end of the sixth month
        self.assertEqual(uk["Inheritance Tax account"], "2027-01-15")
        us = {d["label"]: d["due"] for d in j.deadlines("US", "2026-05-31")}
        self.assertEqual(us["Federal estate tax return (Form 706)"], "2027-02-28")  # clamps to month end
        self.assertTrue(all(d["due"] is None for d in j.deadlines("IN", None)))


class CountryRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TROVE_VAULT_DIR"] = str(Path(self.tmp.name) / "vault")
        self.service = TroveService(str(Path(self.tmp.name) / "j.db"), llm_agent=object(), assistant_agent=object())
        self.legacy = LegacyService(self.service)
        self.saved = config.current()

    def tearDown(self) -> None:
        config.set_current(self.saved)
        os.environ.pop("TROVE_VAULT_DIR", None)
        self.tmp.cleanup()

    def case(self, country: str | None = None, jurisdiction: str | None = None, mode: str = "plan") -> dict:
        data = {"mode": mode, "subject_name": "Asha Rao"}
        if country:
            data["country"] = country
        if jurisdiction:
            data["jurisdiction"] = jurisdiction
        return self.service.create_case(data)

    def test_country_is_stored_and_validated(self) -> None:
        self.assertEqual(self.case("UK")["country"], "UK")
        self.assertEqual(self.case("UK")["jurisdiction"], "United Kingdom")  # defaulted
        self.assertEqual(self.case(jurisdiction="Living in Canada")["country"], "OTHER")
        with self.assertRaises(ValidationError):
            self.case("XX")
        with self.assertRaises(ValidationError):
            self.service.create_case({"mode": "plan", "subject_name": "A"})

    def test_policy_defaults_and_production_minimum(self) -> None:
        case_id = self.case("IN")["id"]
        policy = self.legacy.overview(case_id)["policy"]
        self.assertEqual((policy["quorum"], policy["waiting_period_hours"], policy["legal_documents"]),
                         (2, 72, "required"))
        self.assertEqual(self.legacy.overview(self.case("OTHER")["id"])["policy"]["legal_documents"], "advisory")
        self.legacy.update_policy(case_id, {"waiting_period_hours": 0})  # dev: allowed for testing
        config.set_current(dataclasses.replace(self.saved, env="production"))
        with self.assertRaises(ValidationError):
            self.legacy.update_policy(case_id, {"waiting_period_hours": 12})
        self.legacy.update_policy(case_id, {"waiting_period_hours": 48})

    def test_residency_enforcement(self) -> None:
        config.set_current(dataclasses.replace(self.saved, data_region="uk", enforce_residency=True))
        with self.assertRaises(ValidationError):
            self.case("IN")
        self.case("UK")
        self.case("OTHER")
        report = self.legacy.compliance(self.case("UK")["id"])["data_residency"]
        self.assertTrue(report["matches"] and report["enforced"])

    def test_discovery_tracing_follows_country(self) -> None:
        discovery = DiscoveryBootstrap(self.service)
        uk = discovery.overview(self.case("UK", mode="discovery")["id"])
        bank = next(c for c in uk["coverage"] if c["category"] == "bank_account")
        self.assertTrue(any("My Lost Account" in t for t in bank["official_tracing"]))
        canada = discovery.overview(self.case(jurisdiction="Living in Canada", mode="discovery")["id"])
        self.assertEqual(canada["region"], "OTHER")
        self.assertFalse(any("RBI" in t for c in canada["coverage"] for t in c["official_tracing"]))

    def _verified_nominee_request(self, case_id: str) -> tuple[str, dict]:
        nominee = self.legacy.add_nominee(case_id, {
            "full_name": "Ravi Rao", "relationship": "son", "email": "ravi@example.test",
            "phone": "+447700900111", "id_last4": "1111", "date_of_birth": "1990-01-01",
            "allowed_categories": ["will"]})
        token = self.legacy.enroll({"invite_code": nominee["invite_code"], "email": "ravi@example.test",
                                    "password": PASSWORD})
        for channel in ("email", "phone"):
            body = next(m["body"] for m in self.legacy.outbox(token) if m["channel"] == channel)
            self.legacy.verify_contact(token, {"channel": channel, "code": body.split("code: ")[1][:6]})
        secret = self.legacy.mfa_setup(token)["secret"]
        token = self.legacy.mfa_confirm(token, {"code": sec.totp_at(secret, sec.current_step())})
        self.legacy.submit_identity(token, {"id_last4": "1111", "date_of_birth": "1990-01-01",
                                            "file_name": "id.txt", "content_base64": b64("ID")})
        self.legacy.review_identity(case_id, nominee["id"], {"decision": "approve"})
        self.legacy.update_policy(case_id, {"quorum": 1, "waiting_period_hours": 0})
        request = self.legacy.open_release_request(token, {
            "date_of_death": "2026-01-02", "file_name": "death.txt",
            "content_base64": b64("Certificate of death Asha Rao")})
        return token, request

    def test_uk_grant_required_and_reviewer_queue(self) -> None:
        case_id = self.case("UK")["id"]
        token, request = self._verified_nominee_request(case_id)
        self.assertEqual(request["legal"]["requirements"][0]["key"], "grant")
        self.legacy.review_certificate(case_id, request["id"], {"decision": "approve"})
        self.assertEqual(self.legacy.evaluate(case_id, request["id"])["status"], "open")  # grant missing
        with self.assertRaises(ValidationError):  # India's document is not accepted in the UK
            self.legacy.submit_legal_document(token, request["id"], {
                "requirement_key": "grant", "document_type": "Legal heir certificate (Tahsildar / revenue authority)",
                "file_name": "x.txt", "content_base64": b64("x")})
        view = self.legacy.submit_legal_document(token, request["id"], {
            "requirement_key": "grant", "document_type": "Grant of probate (England & Wales / Northern Ireland)",
            "file_name": "probate.txt", "content_base64": b64("Grant of probate")})
        self.assertNotIn("document_id", view["legal"]["documents"][0])  # nominee view stays minimal
        reviewers = ReviewerService(self.service, self.legacy)
        staff = reviewers.create_reviewer("rita", PASSWORD)
        staff_token = reviewers.login({"username": "rita", "password": PASSWORD,
                                       "totp": sec.totp_at(staff["totp_secret"], sec.current_step())})
        queue = reviewers.queue(staff_token)["legal_document_reviews"]
        self.assertEqual(queue[0]["requirement_label"], "Grant of representation")
        reviewers.download(staff_token, case_id, queue[0]["document_id"])  # pending docs can be opened
        result = reviewers.review_legal_document(staff_token, case_id, request["id"], queue[0]["id"],
                                                 {"decision": "approve"})
        self.assertEqual(result["status"], "released", result["gates"])
        vault = self.legacy.vault_view(token)
        self.assertFalse(any(d["category"] == "legal_document" for d in vault["documents"]))

    def test_advisory_mode_does_not_block(self) -> None:
        case_id = self.case("OTHER")["id"]
        _, request = self._verified_nominee_request(case_id)
        result = self.legacy.review_certificate(case_id, request["id"], {"decision": "approve"})
        gate = next(g for g in result["gates"] if g["key"] == "legal_documents")
        self.assertTrue(gate["passed"] and "advisory" in gate["label"])
        self.assertEqual(result["status"], "released")


if __name__ == "__main__":
    unittest.main()
