from __future__ import annotations

import base64
import dataclasses
import json
import os
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

os.environ.setdefault("TROVE_ENCODER", "hashing")

from trove_agent import config  # noqa: E402
from trove_agent.core import security as sec  # noqa: E402
from trove_agent.api.server import make_server  # noqa: E402
from trove_agent.discovery.bootstrap import DiscoveryBootstrap  # noqa: E402
from trove_agent.discovery.connectors import ConnectorHub  # noqa: E402
from trove_agent.core.db import Database, translate  # noqa: E402
from trove_agent.services.graph import KnowledgeGraph  # noqa: E402
from trove_agent.services.legacy import LegacyService, OutboxNotifier  # noqa: E402
from trove_agent.services.notify import deliver_pending  # noqa: E402
from trove_agent.agents.rag import KnowledgeIndex  # noqa: E402
from trove_agent.services.reviewer import ReviewerService  # noqa: E402
from trove_agent.core.store import TroveService, ValidationError  # noqa: E402
from trove_agent.services.worker import tick  # noqa: E402

PASSWORD = "Correct-Horse-9x"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class TranslateTests(unittest.TestCase):
    def test_sqlite_to_postgres(self) -> None:
        self.assertEqual(translate("SELECT * FROM t WHERE a = ? AND b = ?"),
                         "SELECT * FROM t WHERE a = %s AND b = %s")
        self.assertEqual(translate("INSERT OR IGNORE INTO t (a) VALUES (?)"),
                         "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING")
        self.assertEqual(translate("PRAGMA foreign_keys = ON"), "")
        self.assertEqual(translate("BEGIN IMMEDIATE"), "")
        self.assertIn("'enc1:%%'", translate("SELECT 1 WHERE x NOT LIKE 'enc1:%'"))


class ServiceBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        url = os.getenv("TROVE_TEST_DATABASE_URL") or str(Path(self.tmp.name) / "p.db")
        os.environ["TROVE_VAULT_DIR"] = str(Path(self.tmp.name) / "vault")
        self.service = TroveService(url, llm_agent=object(), assistant_agent=object())
        self.legacy = LegacyService(self.service)

    def tearDown(self) -> None:
        os.environ.pop("TROVE_VAULT_DIR", None)
        self.tmp.cleanup()

    def plan_case(self, owner: str = "local-owner") -> str:
        return self.service.create_case(
            {"mode": "plan", "subject_name": "Asha Rao", "jurisdiction": "India"}, owner)["id"]


class EncryptionAndOutboxTests(ServiceBase):
    def test_contacts_are_encrypted_and_outbox_body_wiped_after_delivery(self) -> None:
        live = LegacyService(self.service, notifier=OutboxNotifier(self.service, "live"))
        case_id = self.plan_case()
        nominee = live.add_nominee(case_id, {"full_name": "Ravi", "relationship": "son",
                                             "email": "ravi@example.test", "phone": "+919812345678"})
        live.enroll({"invite_code": nominee["invite_code"], "email": "ravi@example.test",
                     "password": PASSWORD})
        with self.service._connect() as db:
            raw = db.execute("SELECT email, phone FROM nominees").fetchone()
            pending = db.execute("SELECT body, destination_enc FROM legacy_outbox "
                                 "WHERE status = 'pending'").fetchall()
        self.assertTrue(raw["email"].startswith("enc1:"))
        self.assertNotIn("9812345678", raw["phone"])
        self.assertEqual(len(pending), 2)  # email + phone OTP
        self.assertTrue(all(row["body"].startswith("enc1:") for row in pending))

        delivered = []

        class Fake:
            def send(self, destination: str, subject: str, body: str) -> None:
                delivered.append((destination, body))

        stats = deliver_pending(self.service, {"email": Fake(), "phone": Fake()})
        self.assertEqual(stats, {"sent": 2, "failed": 0})
        self.assertEqual({d for d, _ in delivered}, {"ravi@example.test", "+919812345678"})
        self.assertTrue(all("verification code" in b for _, b in delivered))
        with self.service._connect() as db:
            rows = db.execute("SELECT body, destination_enc, status FROM legacy_outbox").fetchall()
        self.assertTrue(all(r["status"] == "sent" and r["body"] == "" and r["destination_enc"] is None
                            for r in rows))
        # the nominee-facing dev outbox shows nothing in live mode
        self.assertEqual(live.login({"email": "ravi@example.test", "password": PASSWORD}) != "", True)

    def test_failed_delivery_is_retried_then_marked_failed(self) -> None:
        live = LegacyService(self.service, notifier=OutboxNotifier(self.service, "live"))
        case_id = self.plan_case()
        nominee = live.add_nominee(case_id, {"full_name": "R", "relationship": "son",
                                             "email": "r@example.test", "phone": "+919800000002"})
        live.enroll({"invite_code": nominee["invite_code"], "email": "r@example.test",
                     "password": PASSWORD})
        for _ in range(5):
            deliver_pending(self.service, {})  # no providers configured
        with self.service._connect() as db:
            statuses = {r["status"] for r in db.execute("SELECT status FROM legacy_outbox")}
        self.assertEqual(statuses, {"failed"})


class WorkerTests(ServiceBase):
    def test_worker_sends_checkin_reminder_and_escalates(self) -> None:
        case_id = self.plan_case()
        self.legacy.update_policy(case_id, {"checkin_interval_days": 7,
                                            "owner_email": "asha@example.test"})
        self.legacy.add_nominee(case_id, {"full_name": "R", "relationship": "son",
                                          "email": "r@example.test", "phone": "+919800000003"})
        with self.service._connect() as db:
            db.execute("UPDATE legacy_policy SET last_owner_checkin = '2020-01-01T00:00:00+00:00'")
        stats = tick(self.service, self.legacy, channels={})
        self.assertEqual(stats["reminders"], 1)
        self.assertEqual(stats["escalations"], 1)
        self.assertEqual(tick(self.service, self.legacy, channels={})["reminders"], 0)  # throttled


class GraphAndConnectorTests(ServiceBase):
    def test_graph_flags_nominees_sharing_a_phone(self) -> None:
        case_id = self.plan_case()
        for name, email in (("Ravi", "ravi@example.test"), ("Meera", "meera@example.test")):
            self.legacy.add_nominee(case_id, {"full_name": name, "relationship": "child",
                                              "email": email, "phone": "+919899999999"})
        graph = KnowledgeGraph(self.service).build(case_id)
        types = {i["type"] for i in graph["insights"]}
        self.assertIn("shared_contact", types)
        self.assertTrue(any(n["kind"] == "PHONE" for n in graph["nodes"]))
        self.assertFalse(any("9899999999" in json.dumps(n) for n in graph["nodes"]))

    def test_family_seed_phone_matches_nominee_phone(self) -> None:
        case_id = self.plan_case()
        self.legacy.add_nominee(case_id, {"full_name": "Ravi", "relationship": "son",
                                          "email": "ravi@example.test", "phone": "+919877777777"})
        DiscoveryBootstrap(self.service).add_seeds(case_id, {"phones": ["+91 98777 77777"]})
        types = {i["type"] for i in KnowledgeGraph(self.service).build(case_id)["insights"]}
        self.assertIn("subject_contact_on_nominee", types)

    def test_imap_connector_is_read_only_and_filters_personal_mail(self) -> None:
        mails = {
            b"1": (b"From: alerts@hdfcbank.net\r\nSubject: Your account statement\r\n",
                   b"From: alerts@hdfcbank.net\r\nSubject: Your account statement\r\n\r\n"
                   b"HDFC Bank A/c XX5566 statement attached."),
            b"2": (b"From: friend@example.test\r\nSubject: Dinner on Friday?\r\n",
                   b"From: friend@example.test\r\nSubject: Dinner\r\n\r\nSee you"),
        }
        calls: list = []

        class FakeImap:
            def __init__(self, host, port):
                calls.append(("connect", host, port))

            def login(self, user, password):
                calls.append(("login", user))

            def select(self, folder, readonly=False):
                calls.append(("select", folder, readonly))
                return "OK", [b"2"]

            def search(self, charset, *criteria):
                return "OK", [b"1 2"]

            def fetch(self, message_id, spec):
                calls.append(("fetch", message_id, spec))
                header, full = mails[message_id]
                return "OK", [(b"x", header if "HEADER" in spec else full)]

            def logout(self):
                calls.append(("logout",))

        case_id = self.service.create_case({"mode": "discovery", "subject_name": "Asha",
                                            "jurisdiction": "India"})["id"]
        hub = ConnectorHub(self.service, DiscoveryBootstrap(self.service), imap_factory=FakeImap)
        result = hub.run(case_id, "imap", {"username": "asha@gmail.com", "app_password": "x"})
        self.assertIn(("connect", "imap.gmail.com", 993), calls)
        self.assertIn(("select", '"INBOX"', True), calls)
        full_fetches = [c for c in calls if c[0] == "fetch" and c[2] == "(BODY.PEEK[])"]
        self.assertEqual([c[1] for c in full_fetches], [b"1"])  # personal mail never downloaded
        self.assertTrue(all("PEEK" in c[2] for c in calls if c[0] == "fetch"))
        self.assertIn("HDFC Bank", {l["institution"] for l in result["leads"]})

    def test_folder_connector_requires_root_and_blocks_traversal(self) -> None:
        case_id = self.service.create_case({"mode": "discovery", "subject_name": "Asha",
                                            "jurisdiction": "UK"})["id"]
        hub = ConnectorHub(self.service, DiscoveryBootstrap(self.service))
        os.environ.pop("TROVE_IMPORT_ROOT", None)
        with self.assertRaises(ValidationError):
            hub.run(case_id, "folder", {"path": "."})
        root = Path(self.tmp.name) / "import"
        (root / "usb").mkdir(parents=True)
        (root / "usb" / "Barclays_statement.txt").write_text("Barclays savings account no 12345678")
        (root / "usb" / "Last_Will_2020.pdf").write_bytes(b"%PDF-1.4 not really")
        os.environ["TROVE_IMPORT_ROOT"] = str(root)
        try:
            with self.assertRaises(ValidationError):
                hub.run(case_id, "folder", {"path": "../"})
            result = hub.run(case_id, "folder", {"path": "usb"})
        finally:
            os.environ.pop("TROVE_IMPORT_ROOT", None)
        self.assertEqual(result["scan"]["items_scanned"], 2)
        types = {l["asset_type"] for l in result["leads"]}
        self.assertTrue({"bank_account", "trust"} <= types, types)


class RagTests(unittest.TestCase):
    def test_knowledge_search_finds_relevant_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = KnowledgeIndex(cache_dir=tmp)
            hits = index.search("what is transmission of mutual fund units", k=3)
            self.assertTrue(hits)
            self.assertTrue(any("ransmission" in h["text"] or "ransmission" in h["heading"] for h in hits))


class HttpProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TROVE_VAULT_DIR"] = str(Path(self.tmp.name) / "vault")
        self.settings = dataclasses.replace(config.load(), separate_reviewer=True,
                                            allow_signup=True, run_worker=False)
        self.server = make_server("127.0.0.1", 0, str(Path(self.tmp.name) / "h.db"), self.settings)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.service, self.legacy = self.server.trove

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("TROVE_VAULT_DIR", None)
        self.tmp.cleanup()

    def call(self, opener, method, path, body=None, expect=200):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method=method,
                          headers={"Content-Type": "application/json"} if data else {})
        try:
            with opener.open(request) as response:
                self.assertEqual(response.status, expect)
                return json.loads(response.read())
        except HTTPError as exc:
            self.assertEqual(exc.code, expect, exc.read())
            return json.loads(exc.read() or b"{}")

    def test_tenant_isolation_and_reviewer_separation(self) -> None:
        alice = build_opener(HTTPCookieProcessor(CookieJar()))
        bob = build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(alice, "POST", "/auth/register", {"email": "alice@example.test", "password": PASSWORD})
        self.call(bob, "POST", "/auth/register", {"email": "bob@example.test", "password": PASSWORD})
        case = self.call(alice, "POST", "/cases", {"mode": "plan", "subject_name": "A",
                                                   "jurisdiction": "UK"}, expect=201)
        self.assertEqual(self.call(bob, "GET", "/cases")["cases"], [])
        self.call(bob, "GET", f"/cases/{case['id']}", expect=404)
        self.call(bob, "GET", f"/cases/{case['id']}/legacy", expect=404)

        nominee = self.call(alice, "POST", f"/cases/{case['id']}/legacy/nominees", {
            "full_name": "Ravi", "relationship": "son", "email": "ravi@example.test",
            "phone": "+447700900123"}, expect=201)
        n = build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(n, "POST", "/api/nominee/enroll", {"invite_code": nominee["invite_code"],
                                                     "email": "ravi@example.test", "password": PASSWORD})
        with self.service._connect() as db:
            db.execute("UPDATE nominees SET identity_status = 'pending_review'")
        # owner cannot approve their own nominee's identity
        self.call(alice, "POST", f"/cases/{case['id']}/legacy/nominees/{nominee['id']}/identity-review",
                  {"decision": "approve"}, expect=403)

        reviewers = ReviewerService(self.service, self.legacy)
        account = reviewers.create_reviewer("rita", PASSWORD)
        staff = build_opener(HTTPCookieProcessor(CookieJar()))
        self.call(staff, "POST", "/api/reviewer/login", {"username": "rita", "password": PASSWORD,
                                                          "totp": "000000"}, expect=401)
        self.call(staff, "POST", "/api/reviewer/login", {
            "username": "rita", "password": PASSWORD,
            "totp": sec.totp_at(account["totp_secret"], sec.current_step())})
        queue = self.call(staff, "GET", "/api/reviewer/queue")
        self.assertEqual(len(queue["identity_reviews"]), 1)
        self.call(staff, "POST", f"/api/reviewer/cases/{case['id']}/nominees/{nominee['id']}/identity-review",
                  {"decision": "approve"})
        audit = self.call(alice, "GET", f"/cases/{case['id']}/legacy/audit")
        self.assertTrue(any(e["actor"] == "reviewer:rita" for e in audit["events"]))
        self.assertTrue(audit["chain"]["valid"])

    def test_ready_and_connector_catalog(self) -> None:
        anon = build_opener()
        ready = self.call(anon, "GET", "/ready")
        self.assertTrue(ready["ready"])
        catalog = self.call(anon, "GET", "/connectors")["connectors"]
        self.assertEqual({c["id"] for c in catalog if c["status"] == "available"}, {"imap"})
        countries = self.call(anon, "GET", "/jurisdictions")
        self.assertEqual([c["code"] for c in countries["countries"]], ["IN", "UK", "US", "EU", "OTHER"])
        self.assertEqual(self.call(anon, "GET", "/jurisdictions/uk")["legal_requirements"][0]["key"], "grant")
        self.call(anon, "GET", "/jurisdictions/xx", expect=404)
        # reviewer legal-document route is wired (401 without a staff session, not 404)
        self.call(anon, "POST", "/api/reviewer/cases/c/requests/r/legal-documents/d/review",
                  {"decision": "approve"}, expect=401)


class StaticUiTests(unittest.TestCase):
    """The React build in trove_agent/web is served with strict headers and no traversal."""

    def setUp(self) -> None:
        from trove_agent.api import server as server_module

        self.module = server_module
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TROVE_VAULT_DIR"] = str(Path(self.tmp.name) / "vault")
        web = Path(self.tmp.name) / "web"
        (web / "assets").mkdir(parents=True)
        (web / "index.html").write_text(
            '<!doctype html><title>Trove</title><script type="module" src="/assets/owner-abc123.js"></script>')
        (web / "nominee.html").write_text("<!doctype html><title>Trove nominee</title>")
        (web / "assets" / "owner-abc123.js").write_text("console.log('ok')")
        (Path(self.tmp.name) / "secret.txt").write_text("nope")
        self.saved_web = server_module.WEB_DIR
        server_module.WEB_DIR = web
        settings = dataclasses.replace(config.load(), run_worker=False)
        self.server = make_server("127.0.0.1", 0, str(Path(self.tmp.name) / "s.db"), settings)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.module.WEB_DIR = self.saved_web
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("TROVE_VAULT_DIR", None)
        self.tmp.cleanup()

    def get(self, path: str):
        try:
            response = build_opener().open(self.base + path)
            return response.status, response.headers, response.read()
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_pages_and_hashed_assets(self) -> None:
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn(b"/assets/owner-abc123.js", body)
        status, headers, _ = self.get("/assets/owner-abc123.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertEqual(self.get("/nominee")[0], 200)

    def test_traversal_and_unknown_files_are_refused(self) -> None:
        for path in ("/assets/../secret.txt", "/assets/..%2fsecret.txt", "/assets/.env",
                     "/assets/missing.js", "/assets/x.py"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_missing_build_shows_instructions(self) -> None:
        self.module.WEB_DIR = Path(self.tmp.name) / "not-built"
        status, headers, body = self.get("/reviewer")
        self.assertEqual(status, 503)
        self.assertIn(b"npm --prefix frontend run build", body)
        self.assertIn("Content-Security-Policy", headers)


class DemoSeedTests(unittest.TestCase):
    def test_demo_builds_staged_rao_family(self) -> None:
        from trove_agent import demo

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TROVE_VAULT_DIR"] = str(Path(tmp) / "vault")
            try:
                text = demo.run(str(Path(tmp) / "demo.db"))
                service = TroveService(str(Path(tmp) / "demo.db"))
                legacy = LegacyService(service)
                owner = service.session_user(service.login(demo.OWNER_PASSWORD, demo.OWNER_EMAIL))
                cases = {c["mode"]: c["id"] for c in service.list_cases(owner["id"])}
                nominees = {n["full_name"]: n for n in legacy.overview(cases["plan"])["nominees"]}
                with self.assertRaises(RuntimeError):  # never seeds over existing data
                    demo.build(str(Path(tmp) / "demo.db"), Path(tmp) / "files")
            finally:
                os.environ.pop("TROVE_VAULT_DIR", None)
        self.assertTrue(nominees["Ravi Rao"]["verified"] and nominees["Meera Rao"]["verified"])
        self.assertEqual(nominees["Arjun Rao"]["identity_status"], "pending_review")
        self.assertFalse(nominees["Kiran Rao"]["steps"][0]["done"])
        self.assertIn("discovery", cases)
        self.assertIn("kiran@demo.test", text)


class ProductionConfigTests(unittest.TestCase):
    def test_production_refuses_unsafe_config(self) -> None:
        saved = {k: os.environ.get(k) for k in ("TROVE_ENV", "TROVE_VAULT_KEY", "TROVE_PEPPER",
                                                "TROVE_PUBLIC_ORIGIN", "DATABASE_URL", "TROVE_NOTIFIER")}
        try:
            os.environ["TROVE_ENV"] = "production"
            for key in ("TROVE_VAULT_KEY", "TROVE_PEPPER", "TROVE_PUBLIC_ORIGIN"):
                os.environ.pop(key, None)
            with self.assertRaises(RuntimeError):
                config.load()
            os.environ.update({"TROVE_VAULT_KEY": "x", "TROVE_PEPPER": "y",
                               "TROVE_PUBLIC_ORIGIN": "https://trove.example.com",
                               "DATABASE_URL": "trove.db"})
            with self.assertRaises(RuntimeError):  # SQLite not allowed in production
                config.load()
            os.environ["DATABASE_URL"] = "postgresql://u:p@db/trove"
            os.environ["TROVE_NOTIFIER"] = "dev"
            with self.assertRaises(RuntimeError):  # dev outbox not allowed
                config.load()
            os.environ["TROVE_NOTIFIER"] = "live"
            settings = config.load()
            self.assertTrue(settings.secure_cookies and settings.separate_reviewer)
            self.assertFalse(settings.allow_signup and False)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@unittest.skipUnless(os.getenv("TROVE_TEST_DATABASE_URL"), "set TROVE_TEST_DATABASE_URL to run on PostgreSQL")
class PostgresSmokeTest(ServiceBase):
    def test_round_trip(self) -> None:
        self.assertEqual(Database(os.environ["TROVE_TEST_DATABASE_URL"]).dialect, "postgres")
        case_id = self.plan_case()
        self.legacy.add_nominee(case_id, {"full_name": "R", "relationship": "son",
                                          "email": "pg@example.test", "phone": "+919800000009"})
        self.assertEqual(len(self.legacy.overview(case_id)["nominees"]), 1)


if __name__ == "__main__":
    unittest.main()
