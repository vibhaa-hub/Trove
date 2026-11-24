from __future__ import annotations

import json
import base64
import sqlite3
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from threading import Thread
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from pypdf import PdfWriter

from trove_agent.agents.rules import DocumentAgent
from trove_agent.api.server import make_server
from trove_agent.agents.llm import MistralDiscoveryAgent
from trove_agent.discovery.mock_email import MockEmailConnector
from trove_agent.core.store import (
    ConflictError,
    NotFoundError,
    TroveService,
    ValidationError,
)


class FakeLLMAgent:
    def __init__(self) -> None:
        self.received_text = []

    def analyze(self, text: str, file_name: str, subject_name: str, subject_dob: str | None):
        self.received_text.append(text)
        return DocumentAgent().analyze(text, file_name, subject_name, subject_dob)


class FakeAssistantAgent:
    def __init__(self) -> None:
        self.calls = []

    def answer(self, question: str, case_summary: dict[str, object]) -> str:
        self.calls.append((question, case_summary))
        return "There is one asset lead requiring review."


class TroveServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = TroveService(Path(self.temp_dir.name) / "test.db")
        self.case = self.service.create_case(
            {
                "subject_name": "Asha Patel",
                "subject_dob": "1950-01-02",
                "jurisdiction": "IN",
                "mode": "discovery",
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_evidence_creates_redacted_reviewable_lead_without_storing_text(self) -> None:
        result = self.service.add_evidence(
            self.case["id"],
            {
                "file_name": "statement.txt",
                "text": "Asha Patel HDFC Bank fixed deposit account number 123456789",
            },
        )
        self.assertFalse(result["raw_text_stored"])
        self.assertEqual(len(result["assets_created"]), 1)
        asset = result["assets_created"][0]
        self.assertEqual(asset["asset_type"], "bank_account")
        self.assertEqual(asset["masked_reference"], "***6789")
        self.assertEqual(asset["status"], "needs_review")
        stored = self.service.list_evidence(self.case["id"])[0]
        self.assertNotIn("text", stored)
        self.assertNotIn("123456789", str(stored))

    def test_workflow_requires_review_before_claims(self) -> None:
        uploaded = self.service.upload_document(
            self.case["id"],
            {
                "file_name": "death-certificate.txt",
                "category": "death_certificate",
                "content_base64": base64.b64encode(
                    b"Fictional death certificate for testing"
                ).decode(),
            },
        )
        self.assertEqual(uploaded["review_status"], "pending_review")
        self.assertFalse(
            self.service.review_death_certificate(
                self.case["id"], {"decision": "reviewed", "reviewer": "Test Owner"}
            )["legal_verification"]
        )
        self.service.add_evidence(
            self.case["id"],
            {"file_name": "policy.txt", "text": "Life insurance policy premium"},
        )
        self.service.advance_workflow(self.case["id"])
        self.service.advance_workflow(self.case["id"])
        beneficiary = self.service.add_beneficiary(
            self.case["id"], {"name": "Mira", "relationship": "child"}
        )
        with self.assertRaises(ConflictError):
            self.service.advance_workflow(self.case["id"])
        asset = self.service.list_assets(self.case["id"])[0]
        self.service.review_asset(
            self.case["id"], asset["id"], {"decision": "confirmed"}
        )
        with self.assertRaises(ConflictError):
            self.service.advance_workflow(self.case["id"])
        self.service.review_beneficiary(
            self.case["id"],
            beneficiary["id"],
            {"decision": "documents_reviewed", "reviewer": "Test Owner"},
        )
        self.assertEqual(
            self.service.advance_workflow(self.case["id"])["stage"],
            "claims",
        )

    def test_beneficiary_shares_cannot_exceed_one_hundred(self) -> None:
        self.service.add_beneficiary(
            self.case["id"],
            {"name": "Mira", "relationship": "child", "share_percent": 60},
        )
        with self.assertRaises(ConflictError):
            self.service.add_beneficiary(
                self.case["id"],
                {"name": "Ravi", "relationship": "child", "share_percent": 50},
            )

    def test_relationship_graph_and_entity_match_suggestions_are_review_only(self) -> None:
        first = self.service.add_beneficiary(
            self.case["id"], {"name": "Mira Patel", "relationship": "child"}
        )
        self.service.add_beneficiary(
            self.case["id"], {"name": "Mira Patel", "relationship": "daughter"}
        )
        suggestions = self.service.entity_match_suggestions(self.case["id"])
        self.assertEqual(len(suggestions), 1)
        self.assertFalse(suggestions[0]["automatic_merge"])
        graph = self.service.relationship_graph(self.case["id"])
        self.assertTrue(any(edge["label"] == "child" for edge in graph["edges"]))
        self.assertEqual(self.service.list_people(self.case["id"])[1]["id"], first["person_id"])

    def test_invalid_case_and_document_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_case({"subject_name": "", "jurisdiction": "IN"})
        with self.assertRaises(ValidationError):
            self.service.create_case(
                {
                    "subject_name": "Asha Patel",
                    "jurisdiction": "IN",
                    "subject_dob": "2025-99-99",
                }
            )
        with self.assertRaises(ValidationError):
            self.service.add_evidence(
                self.case["id"], {"file_name": "large.txt", "text": "x" * 100_001}
            )

    def test_llm_analysis_requires_explicit_consent(self) -> None:
        llm = FakeLLMAgent()
        service = TroveService(
            Path(self.temp_dir.name) / "consent.db", llm_agent=llm
        )
        case = service.create_case(
            {"subject_name": "Asha", "jurisdiction": "IN"}
        )
        data = {
            "file_name": "statement.txt",
            "text": "Life insurance policy premium",
            "use_llm": True,
        }
        with self.assertRaises(ValidationError):
            service.add_evidence(case["id"], data)
        self.assertEqual(llm.received_text, [])

    def test_mock_inbox_fetches_fictional_emails_and_never_uses_llm(self) -> None:
        service = TroveService(
            Path(self.temp_dir.name) / "mock-email.db",
            mock_email_connector=MockEmailConnector(),
        )
        case = service.create_case(
            {"subject_name": "Asha", "jurisdiction": "IN"}
        )
        inbox = service.connect_mock_email(
            case["id"], {"email": "demo.family@example.test"}
        )
        self.assertTrue(inbox["demo_only"])
        self.assertEqual(len(inbox["messages"]), 7)
        generated = service.mock_email_connector.list_messages(
            "demo.family@example.test", 1, "Asha", case["id"]
        )
        self.assertIn("Asha", generated[0]["text"])
        self.assertNotIn("Asha", str(inbox["messages"]))
        self.assertNotIn("text", str(inbox["messages"]))
        result = service.sync_mock_email(
            case["id"],
            {
                "email": "demo.family@example.test",
                "message_ids": [inbox["messages"][0]["id"]],
            },
        )
        self.assertEqual(result["provider"], "mock_email")
        self.assertFalse(result["external_services_used"])
        self.assertFalse(result["raw_email_text_stored"])
        self.assertEqual(
            result["results"][0]["assets_created"][0]["asset_type"], "insurance"
        )
        duplicate = service.sync_mock_email(
            case["id"],
            {
                "email": "demo.family@example.test",
                "message_ids": [inbox["messages"][0]["id"]],
            },
        )
        self.assertEqual(duplicate["messages_analyzed"], 0)
        self.assertEqual(duplicate["duplicates_skipped"], 1)

    def test_mock_email_requires_demo_address_and_valid_selection(self) -> None:
        service = TroveService(Path(self.temp_dir.name) / "mock-validation.db")
        case = service.create_case({"subject_name": "Asha", "jurisdiction": "IN"})
        with self.assertRaises(ValidationError):
            service.connect_mock_email(
                case["id"], {"email": "real.person@example.com"}
            )
        with self.assertRaises(ValidationError):
            service.sync_mock_email(
                case["id"],
                {
                    "email": "demo.family@example.test",
                    "message_ids": ["executor-property-001"],
                },
            )

    def test_owner_authentication_and_encrypted_document_vault(self) -> None:
        service = TroveService(Path(self.temp_dir.name) / "auth-vault.db")
        case = service.create_case({"subject_name": "Asha", "jurisdiction": "IN"})
        with self.assertRaises(ValidationError):
            service.create_owner("short")
        token = service.create_owner("A-long-local-test-password-1")
        self.assertTrue(service.auth_status(token)["authenticated"])
        self.assertFalse(service.auth_status("invalid-session")["authenticated"])
        raw = b"Fictional HDFC Bank fixed deposit account number 123456789"
        uploaded = service.upload_document(
            case["id"],
            {
                "file_name": "statement.txt",
                "category": "bank_statement",
                "content_base64": base64.b64encode(raw).decode(),
            },
        )
        metadata, recovered = service.get_document(case["id"], uploaded["id"])
        self.assertEqual(recovered, raw)
        self.assertNotIn("stored_path", metadata)
        stored_path = Path(service.vault.root) / f"{uploaded['id']}.enc"
        self.assertNotIn(raw, stored_path.read_bytes())
        self.assertEqual(len(uploaded["assets_created"]), 1)
        service.delete_document(case["id"], uploaded["id"])
        self.assertEqual(service.list_assets(case["id"]), [])
        service.logout(token)
        self.assertFalse(service.auth_status(token)["authenticated"])

    def test_death_certificate_review_gates_post_death_workflow(self) -> None:
        with self.assertRaises(ConflictError):
            self.service.advance_workflow(self.case["id"])
        uploaded = self.service.upload_document(
            self.case["id"],
            {
                "file_name": "certificate.txt",
                "category": "death_certificate",
                "content_base64": base64.b64encode(b"Sample").decode(),
            },
        )
        self.assertEqual(uploaded["category"], "death_certificate")
        with self.assertRaises(ConflictError):
            self.service.advance_workflow(self.case["id"])
        review = self.service.review_death_certificate(
            self.case["id"], {"decision": "rejected", "reviewer": "Owner"}
        )
        self.assertEqual(review["stage"], "intake")
        self.assertEqual(review["death_status"], "rejected")

    def test_pdf_vault_delete_and_claim_tracker(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf = io.BytesIO()
        writer.write(pdf)
        document = self.service.upload_document(
            self.case["id"],
            {
                "file_name": "sample.pdf",
                "category": "other",
                "content_base64": base64.b64encode(pdf.getvalue()).decode(),
            },
        )
        self.assertIn(document["id"], [item["id"] for item in self.service.list_documents(self.case["id"])])
        self.service.delete_document(self.case["id"], document["id"])
        self.assertEqual(self.service.list_documents(self.case["id"]), [])
        self.assertFalse((Path(self.service.vault.root) / f"{document['id']}.enc").exists())

        plan = self.service.create_case(
            {"mode": "plan", "subject_name": "Sample Subject", "jurisdiction": "Demo"}
        )
        self.service.advance_workflow(plan["id"])
        self.assertEqual(self.service.advance_workflow(plan["id"])["stage"], "claims")
        claim = self.service.add_claim(
            plan["id"], {"institution": "Demo Provider", "next_action": "Request forms"}
        )
        self.assertEqual(claim["status"], "not_started")
        updated = self.service.update_claim(
            plan["id"], claim["id"], {"status": "documents_requested"}
        )
        self.assertEqual(updated["status"], "documents_requested")

    def test_existing_database_schema_is_migrated(self) -> None:
        database = Path(self.temp_dir.name) / "legacy.db"
        db = sqlite3.connect(database)
        try:
            db.executescript(
                """
                CREATE TABLE cases (
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, subject_name TEXT NOT NULL,
                    subject_dob TEXT, jurisdiction TEXT NOT NULL, stage TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE evidence (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, file_name TEXT NOT NULL,
                    source_type TEXT NOT NULL, text_sha256 TEXT NOT NULL,
                    entities_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE assets (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, evidence_id TEXT,
                    asset_type TEXT NOT NULL, institution TEXT, masked_reference TEXT,
                    description TEXT NOT NULL, confidence REAL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE beneficiaries (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, name TEXT NOT NULL,
                    relationship TEXT NOT NULL, share_percent REAL, created_at TEXT NOT NULL
                );
                CREATE TABLE audit_events (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
        finally:
            db.close()
        service = TroveService(database)
        case = service.create_case({"subject_name": "Legacy Subject", "jurisdiction": "Demo"})
        self.assertEqual(case["death_status"], "not_provided")
        beneficiary = service.add_beneficiary(
            case["id"], {"name": "Legacy Beneficiary", "relationship": "child"}
        )
        self.assertEqual(beneficiary["verification_status"], "unverified")

    def test_case_export_and_confirmed_delete(self) -> None:
        evidence = self.service.add_evidence(
            self.case["id"],
            {"file_name": "note.txt", "text": "Life insurance policy statement"},
        )
        export = self.service.export_case(self.case["id"])
        self.assertEqual(export["case"]["id"], self.case["id"])
        self.assertEqual(export["evidence"][0]["id"], evidence["id"])
        with self.assertRaises(ValidationError):
            self.service.delete_case(self.case["id"], "no")
        self.service.delete_case(self.case["id"], "DELETE")
        with self.assertRaises(NotFoundError):
            self.service.get_case(self.case["id"])

    def test_mistral_response_is_restricted_to_valid_asset_types(self) -> None:
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                complete=lambda **kwargs: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "asset_type": "insurance",
                                                "institution": "Aviva",
                                            },
                                            {"asset_type": "unknown", "institution": None},
                                        ]
                                    }
                                )
                            )
                        )
                    ]
                )
            )
        )
        agent = MistralDiscoveryAgent(client=fake_client)
        result = agent.analyze(
            "Aviva life insurance policy", "email.txt", "Asha", None
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0]["asset_type"], "insurance")
        self.assertEqual(
            result.candidates[0]["description"],
            "Possible insurance identified by Mistral; human verification required",
        )

    def test_assistant_requires_consent_and_sends_only_case_summary(self) -> None:
        assistant = FakeAssistantAgent()
        service = TroveService(
            Path(self.temp_dir.name) / "assistant.db", assistant_agent=assistant
        )
        case = service.create_case(
            {"subject_name": "Asha", "jurisdiction": "IN"}
        )
        with self.assertRaises(ValidationError):
            service.ask_assistant(case["id"], {"question": "What next?"})
        self.assertEqual(assistant.calls, [])
        result = service.ask_assistant(
            case["id"],
            {
                "question": "What next?",
                "consent_to_send_case_summary_to_mistral": True,
            },
        )
        self.assertEqual(result["provider"], "mistral")
        self.assertNotIn("subject_name", assistant.calls[0][1])

class HTTPAPITests(unittest.TestCase):
    def test_server_rejects_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            make_server("0.0.0.0", 0, ":memory:")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = make_server(
            "127.0.0.1", 0, str(Path(self.temp_dir.name) / "api.db")
        )
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        setup = Request(
            f"{self.base_url}/auth/setup",
            data=json.dumps({"password": "Http-local-owner-test-password-1"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(setup) as response:
            self.assertEqual(response.status, 200)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def test_create_case_and_submit_evidence_over_http(self) -> None:
        with self.opener.open(f"{self.base_url}/integrations/mock-email/status") as response:
            status = json.load(response)
            self.assertEqual(response.status, 200)
        self.assertTrue(status["is_mock"])
        self.assertFalse(status["external_connection"])
        self.assertFalse(status["password_required"])

        with self.opener.open(self.base_url) as response:
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])
            self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("Trove", page)

        with self.opener.open(f"{self.base_url}/api") as response:
            root = json.load(response)
            self.assertEqual(response.status, 200)
        self.assertIn("create_case", root["endpoints"])
        self.assertIn("assistant", root["endpoints"])
        request = Request(
            f"{self.base_url}/cases",
            data=json.dumps(
                {"mode": "discovery", "subject_name": "Asha Patel", "jurisdiction": "IN"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            case = json.load(response)
            self.assertEqual(response.status, 201)
        request = Request(
            f"{self.base_url}/cases/{case['id']}/evidence",
            data=json.dumps(
                {
                    "file_name": "statement.txt",
                    "text": "HDFC Bank fixed deposit account number 987654321",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            result = json.load(response)
            self.assertEqual(response.status, 201)
        self.assertFalse(result["raw_text_stored"])
        self.assertEqual(result["assets_created"][0]["masked_reference"], "***4321")

        payload = {
            "file_name": "sample.txt",
            "category": "other",
            "content_base64": base64.b64encode(b"Fictional document").decode(),
        }
        request = Request(
            f"{self.base_url}/cases/{case['id']}/documents",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            document = json.load(response)
            self.assertEqual(response.status, 201)
        download_url = (
            f"{self.base_url}/cases/{case['id']}/documents/{document['id']}/download"
        )
        with self.opener.open(download_url) as response:
            self.assertEqual(response.read(), b"Fictional document")

    def test_case_api_requires_owner_session(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/cases")
        self.assertEqual(context.exception.code, 401)
        context.exception.close()

    def test_mock_inbox_api_has_no_real_email_connector(self) -> None:
        request = Request(
            f"{self.base_url}/cases",
            data=json.dumps(
                {"subject_name": "Alex Morgan", "jurisdiction": "Demo"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            case = json.load(response)
        request = Request(
            f"{self.base_url}/cases/{case['id']}/integrations/mock-email/connect",
            data=json.dumps({"email": "demo.family@example.test"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            inbox = json.load(response)
        self.assertTrue(inbox["demo_only"])
        self.assertGreater(len(inbox["messages"]), 0)
        request = Request(
            f"{self.base_url}/cases/{case['id']}/integrations/mock-email/sync",
            data=json.dumps(
                {
                    "email": inbox["email"],
                    "message_ids": [inbox["messages"][0]["id"]],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request) as response:
            result = json.load(response)
        self.assertFalse(result["external_services_used"])
        self.assertGreaterEqual(len(result["results"]), 1)

    def test_cross_site_write_is_rejected(self) -> None:
        request = Request(
            f"{self.base_url}/cases",
            data=json.dumps(
                {"subject_name": "Alex Morgan", "jurisdiction": "Demo"}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(request)
        self.assertEqual(context.exception.code, 422)
        context.exception.close()


if __name__ == "__main__":
    unittest.main()
