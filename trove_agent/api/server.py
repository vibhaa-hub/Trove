"""Standard-library JSON HTTP API for Trove.

Development: loopback only, SQLite, dev outbox.
Production (TROVE_ENV=production): runs behind a TLS reverse proxy (Caddy),
PostgreSQL, Secure cookies, Origin pinned to TROVE_PUBLIC_ORIGIN, independent
reviewer accounts, real notifier, background worker as a separate process.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from trove_agent import config
from trove_agent.core import jurisdictions
from trove_agent.agents.assistant import SkillAssistant
from trove_agent.discovery.bootstrap import DiscoveryBootstrap
from trove_agent.discovery.connectors import ConnectorHub
from trove_agent.services.graph import KnowledgeGraph
from trove_agent.services.legacy import ForbiddenError, LegacyService
from trove_agent.services.notify import deliver_pending
from trove_agent.services.reviewer import ReviewerService
from trove_agent.core.store import NotFoundError, TroveError, TroveService, ValidationError

# React build output (frontend/ -> `npm run build` -> trove_agent/web/)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
PAGES = {"/": "index.html", "/nominee": "nominee.html", "/reviewer": "reviewer.html"}
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".json": "application/json",
}
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
UI_NOT_BUILT = (b"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Trove</title></head>"
                b"<body><main><h1>Trove UI is not built</h1><p>Run <code>npm --prefix frontend ci</code> "
                b"and <code>npm --prefix frontend run build</code>, then reload. The API is running.</p>"
                b"</main></body></html>")


def resolve_static(path: str) -> tuple[Path, str, bool] | None:
    """Map a URL path to a file in the React build. Returns (file, content type, immutable)."""
    if path in PAGES:
        file, immutable = WEB_DIR / PAGES[path], False
    elif path == "/favicon.svg":
        file, immutable = WEB_DIR / "favicon.svg", False
    elif path.startswith("/assets/") and ASSET_NAME.fullmatch(path[len("/assets/"):]):
        file, immutable = WEB_DIR / "assets" / path[len("/assets/"):], True
    else:
        return None
    content_type = CONTENT_TYPES.get(file.suffix)
    if content_type is None or not file.is_file():
        return None
    return file, content_type, immutable


NOMINEE_COOKIE = "trove_nominee"
STAFF_COOKIE = "trove_staff"
log = logging.getLogger("trove.http")


class TroveHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(host: str, port: int, database_path: str,
                settings: config.Settings | None = None) -> TroveHTTPServer:
    settings = settings or config.current()
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.production:
        raise ValueError("Development server is loopback-only; set TROVE_ENV=production "
                         "and run behind a TLS reverse proxy to expose it")
    service = TroveService(database_path)
    legacy = LegacyService(service)
    discovery = DiscoveryBootstrap(service)
    assistant = SkillAssistant(service, legacy, discovery)
    reviewers = ReviewerService(service, legacy)
    graph = KnowledgeGraph(service)
    connectors = ConnectorHub(service, discovery)
    secure = "; Secure" if settings.secure_cookies else ""

    def cookie(name: str, token: str | None, max_age: int) -> dict[str, str]:
        if token is None:
            return {"Set-Cookie": f"{name}=; Path=/; HttpOnly; SameSite=Strict{secure}; Max-Age=0"}
        return {"Set-Cookie": f"{name}={token}; Path=/; HttpOnly; SameSite=Strict{secure}; "
                              f"Max-Age={max_age}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "Trove"
        request_id = "-"
        status_sent = 0

        def _send(
            self, status: int, payload: Any, extra_headers: dict[str, str] | None = None
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.status_sent = status
            self.send_response(status)
            self.send_header("X-Request-ID", self.request_id)
            if settings.secure_cookies:
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _session_token(self, name: str = "trove_session") -> str | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = cookie.get(name)
            return morsel.value if morsel else None

        def _nominee_cookie(self, token: str | None) -> dict[str, str]:
            return cookie(NOMINEE_COOKIE, token, 7200)

        def _client(self) -> str:
            if settings.trust_proxy:
                forwarded = self.headers.get("X-Forwarded-For", "")
                if forwarded:
                    return forwarded.split(",")[0].strip()[:64]
            return self.client_address[0]

        def _reviewer_dispatch(self, path: str, method: str) -> None:
            token = self._session_token(STAFF_COOKIE)
            parts = path[len("/api/reviewer"):].strip("/").split("/")
            if method == "POST" and parts == ["login"]:
                new_token = reviewers.login(self._body(), self._client())
                self._send(200, {"signed_in": True}, cookie(STAFF_COOKIE, new_token, 28800))
            elif method == "POST" and parts == ["logout"]:
                reviewers.logout(token)
                self._send(200, {"signed_in": False}, cookie(STAFF_COOKIE, None, 0))
            elif method == "GET" and parts == ["queue"]:
                self._send(200, reviewers.queue(token))
            elif (method == "POST" and len(parts) == 5 and parts[0] == "cases"
                  and parts[2] == "nominees" and parts[4] == "identity-review"):
                self._send(200, reviewers.review_identity(token, parts[1], parts[3], self._body()))
            elif (method == "POST" and len(parts) == 5 and parts[0] == "cases"
                  and parts[2] == "requests" and parts[4] == "certificate-review"):
                self._send(200, reviewers.review_certificate(token, parts[1], parts[3], self._body()))
            elif (method == "POST" and len(parts) == 7 and parts[0] == "cases"
                  and parts[2] == "requests" and parts[4] == "legal-documents"
                  and parts[6] == "review"):
                self._send(200, reviewers.review_legal_document(token, parts[1], parts[3], parts[5],
                                                                self._body()))
            elif (method == "GET" and len(parts) == 4 and parts[0] == "cases"
                  and parts[2] == "documents"):
                self._send_file(*reviewers.download(token, parts[1], parts[3]))
            else:
                self._send(404, {"error": "Route not found"})

        def _send_file(self, metadata: dict[str, Any], content: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(metadata['file_name'])}",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _nominee_dispatch(self, path: str, method: str) -> None:
            token = self._session_token(NOMINEE_COOKIE)
            client = self._client()
            route = path[len("/api/nominee"):] or "/"
            parts = route.strip("/").split("/")
            if method == "POST" and route == "/enroll":
                new_token = legacy.enroll(self._body(), client)
                self._send(200, {"enrolled": True}, self._nominee_cookie(new_token))
            elif method == "POST" and route == "/login":
                new_token = legacy.login(self._body(), client)
                self._send(200, {"signed_in": True}, self._nominee_cookie(new_token))
            elif method == "POST" and route == "/logout":
                legacy.logout(token)
                self._send(200, {"signed_in": False}, self._nominee_cookie(None))
            elif method == "GET" and route == "/me":
                self._send(200, legacy.me(token))
            elif method == "GET" and route == "/outbox":
                self._send(200, {"dev_outbox": True, "messages": legacy.outbox(token)})
            elif method == "POST" and route == "/otp/resend":
                self._send(200, legacy.resend_otp(token, self._body()))
            elif method == "POST" and route == "/otp/verify":
                self._send(200, legacy.verify_contact(token, self._body()))
            elif method == "POST" and route == "/mfa/setup":
                self._send(200, legacy.mfa_setup(token))
            elif method == "POST" and route == "/mfa/confirm":
                new_token = legacy.mfa_confirm(token, self._body())
                self._send(200, {"mfa_enabled": True}, self._nominee_cookie(new_token))
            elif method == "POST" and route == "/step-up":
                self._send(200, legacy.step_up(token, self._body()))
            elif method == "POST" and route == "/identity":
                self._send(200, legacy.submit_identity(token, self._body(14_000_000)))
            elif method == "POST" and route == "/release-requests":
                self._send(201, legacy.open_release_request(token, self._body(7_000_000)))
            elif (method == "POST" and len(parts) == 3 and parts[0] == "release-requests"
                  and parts[2] == "legal-documents"):
                self._send(200, legacy.submit_legal_document(token, parts[1], self._body(7_000_000)))
            elif method == "POST" and len(parts) == 3 and parts[0] == "release-requests":
                if parts[2] == "attest":
                    self._send(200, legacy.attest(token, parts[1], self._body()))
                elif parts[2] == "evaluate":
                    self._send(200, legacy.evaluate_for_nominee(token, parts[1]))
                else:
                    self._send(404, {"error": "Route not found"})
            elif method == "GET" and route == "/vault":
                self._send(200, legacy.vault_view(token))
            elif method == "GET" and len(parts) == 3 and parts[:2] == ["vault", "documents"]:
                self._send_file(*legacy.vault_download(token, parts[2]))
            else:
                self._send(404, {"error": "Route not found"})

        def _legacy_dispatch(self, case_id: str, parts: list[str], method: str) -> None:
            sub = parts[3:]
            if settings.separate_reviewer and (sub[-1:] == ["identity-review"]
                                               or sub[-1:] == ["certificate-review"]
                                               or (len(sub) == 5 and sub[2] == "legal-documents")):
                raise ForbiddenError("Reviews are performed by an independent Trove reviewer, "
                                     "not by the account owner")
            if not sub and method == "GET":
                self._send(200, legacy.overview(case_id))
            elif sub == ["policy"] and method in ("PUT", "POST"):
                self._send(200, legacy.update_policy(case_id, self._body()))
            elif sub == ["checkin"] and method == "POST":
                self._send(200, legacy.owner_checkin(case_id))
            elif sub == ["nominees"] and method == "POST":
                self._send(201, legacy.add_nominee(case_id, self._body()))
            elif len(sub) == 3 and sub[0] == "nominees" and method == "POST":
                if sub[2] == "revoke":
                    self._send(200, legacy.revoke_nominee(case_id, sub[1]))
                elif sub[2] == "reinvite":
                    self._send(200, legacy.reinvite(case_id, sub[1]))
                elif sub[2] == "identity-review":
                    self._send(200, legacy.review_identity(case_id, sub[1], self._body()))
                else:
                    self._send(404, {"error": "Route not found"})
            elif len(sub) == 3 and sub[0] == "requests" and method == "POST":
                if sub[2] == "certificate-review":
                    self._send(200, legacy.review_certificate(case_id, sub[1], self._body()))
                elif sub[2] == "evaluate":
                    self._send(200, legacy.evaluate(case_id, sub[1]))
                else:
                    self._send(404, {"error": "Route not found"})
            elif (len(sub) == 5 and sub[0] == "requests" and sub[2] == "legal-documents"
                  and sub[4] == "review" and method == "POST"):
                self._send(200, legacy.review_legal_document(case_id, sub[1], sub[3], self._body(),
                                                             actor="owner"))
            elif sub == ["audit"] and method == "GET":
                self._send(200, {"chain": legacy.verify_audit_chain(case_id),
                                 "events": legacy.list_audit(case_id)})
            else:
                self._send(404, {"error": "Route not found"})

        def _discovery_dispatch(self, case_id: str, parts: list[str], method: str) -> None:
            sub = parts[3:]
            if not sub and method == "GET":
                self._send(200, discovery.overview(case_id))
            elif sub == ["seeds"] and method == "POST":
                self._send(200, discovery.add_seeds(case_id, self._body()))
            elif sub == ["sources"] and method == "POST":
                self._send(200, discovery.ingest(case_id, self._body(7_000_000)))
            elif len(sub) == 2 and sub[0] == "leads" and method == "PATCH":
                self._send(200, discovery.update_lead(case_id, sub[1], self._body()))
            elif len(sub) == 2 and sub[0] == "connectors" and method == "POST":
                self._send(200, connectors.run(case_id, sub[1], self._body()))
            else:
                self._send(404, {"error": "Route not found"})

        def _send_static(self, file_path: Path | None, content_type: str, immutable: bool,
                         status: int = 200) -> None:
            body = file_path.read_bytes() if file_path else UI_NOT_BUILT
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "public, max-age=31536000, immutable" if immutable else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _body(self, max_length: int = 1_000_000) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("application/json"):
                raise ValidationError("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValidationError("Invalid Content-Length") from exc
            if length < 1 or length > max_length:
                raise ValidationError(
                    f"Request body must be between 1 byte and {max_length // 1_000_000} MB"
                )
            try:
                value = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValidationError("Request body must contain valid JSON") from exc
            if not isinstance(value, dict):
                raise ValidationError("Request body must be a JSON object")
            return value

        def _dispatch(self) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            parts = path.strip("/").split("/") if path != "/" else []
            method = self.command
            if method == "GET" and (path in PAGES or path == "/favicon.svg"
                                    or path.startswith("/assets/")):
                static = resolve_static(path)
                if static:
                    self._send_static(*static)
                elif path in PAGES:
                    self._send_static(None, CONTENT_TYPES[".html"], False, status=503)
                else:
                    self._send(404, {"error": "Not found"})
                return
            if path.startswith("/api/nominee"):
                self._nominee_dispatch(path, method)
                return
            if path.startswith("/api/reviewer"):
                self._reviewer_dispatch(path, method)
                return
            if method == "GET" and path == "/api":
                self._send(
                    200,
                    {
                        "service": "Trove Agent API",
                        "status": "ok",
                        "message": "Use the API endpoints below to manage Trove cases.",
                        "endpoints": {
                            "health": "GET /health",
                            "auth": "GET /auth/status, POST /auth/setup|login|logout",
                            "list_cases": "GET /cases",
                            "create_case": "POST /cases",
                            "mock_email_status": "GET /integrations/mock-email/status",
                            "mock_email_connect": "POST /cases/{id}/integrations/mock-email/connect",
                            "mock_email_sync": "POST /cases/{id}/integrations/mock-email/sync",
                            "assistant": "POST /cases/{id}/assistant (skills; local unless use_llm + consent)",
                            "skills": "GET /skills",
                            "privacy_export": "GET /cases/{id}/export",
                            "privacy_delete": "DELETE /cases/{id}",
                            "documents": "POST/GET /cases/{id}/documents",
                            "relationships": "GET /cases/{id}/relationships",
                            "claims": "GET/POST /cases/{id}/claims",
                        },
                    },
                )
                return
            if method == "GET" and path == "/skills":
                self._send(200, {"skills": [
                    {"name": c.name, "description": c.description, "tools": list(c.tools),
                     "resources": list(c.resources)} for c in assistant.skills.public()]})
                return
            if method == "GET" and path == "/health":
                self._send(200, {"status": "ok", "service": "trove-agent"})
                return
            if method == "GET" and path == "/ready":
                checks = {}
                try:
                    checks["database"] = service.db.ping()
                except Exception:
                    checks["database"] = False
                try:
                    service.vault._fernet()
                    checks["vault_key"] = True
                except Exception:
                    checks["vault_key"] = False
                checks["vault_writable"] = service.vault.root.exists() and service.vault.root.is_dir()
                ready = all(checks.values())
                self._send(200 if ready else 503, {"ready": ready, "checks": checks,
                                                   "env": settings.env,
                                                   "data_region": settings.data_region or None,
                                                   "database": service.db.dialect})
                return
            if method == "GET" and path == "/jurisdictions":
                self._send(200, {"countries": jurisdictions.catalog(),
                                 "data_region": settings.data_region or None,
                                 "enforce_residency": settings.enforce_residency,
                                 "disclaimer": jurisdictions.DISCLAIMER})
                return
            if method == "GET" and len(parts) == 2 and parts[0] == "jurisdictions":
                if parts[1].upper() not in jurisdictions.PROFILES:
                    self._send(404, {"error": "Unknown country"})
                else:
                    self._send(200, jurisdictions.profile(parts[1]).as_dict())
                return
            if method == "GET" and path == "/connectors":
                self._send(200, {"connectors": connectors.catalog()})
                return
            if method == "GET" and path == "/auth/status":
                self._send(200, service.auth_status(self._session_token()))
                return
            if method == "POST" and path in ("/auth/setup", "/auth/register", "/auth/login"):
                if not legacy.limiter.hit(f"owner-auth:{self._client()}", 20, 900):
                    self._send(429, {"error": "Too many attempts; wait a few minutes"})
                    return
                data = self._body()
                email = data.get("email") or None
                token = (
                    service.login(data.get("password"), email)
                    if path == "/auth/login"
                    else service.create_owner(data.get("password"), email)
                )
                self._send(200, {"authenticated": True}, cookie("trove_session", token, 43200))
                return
            if method == "POST" and path == "/auth/logout":
                service.logout(self._session_token())
                self._send(200, {"authenticated": False}, cookie("trove_session", None, 0))
                return
            user = service.session_user(self._session_token())
            if user is None:
                self._send(401, {"error": "Sign in to access Trove data"})
                return
            if method == "GET" and path == "/cases":
                self._send(200, {"cases": service.list_cases(user["id"])})
                return
            if method == "POST" and path == "/cases":
                self._send(201, service.create_case(self._body(), user["id"]))
                return
            if method == "GET" and path == "/integrations/mock-email/status":
                self._send(200, service.mock_email_status())
                return
            if len(parts) < 2 or parts[0] != "cases":
                self._send(404, {"error": "Route not found"})
                return
            case_id = parts[1]
            if service.case_owner(case_id) != user["id"]:
                # same response whether the case is missing or someone else's
                raise NotFoundError(f"Case {case_id} was not found")
            if len(parts) == 3 and parts[2] == "compliance" and method == "GET":
                self._send(200, legacy.compliance(case_id))
                return
            if len(parts) >= 3 and parts[2] == "graph" and method == "GET":
                self._send(200, graph.build(case_id))
                return
            if len(parts) == 2:
                if method == "GET":
                    self._send(200, service.get_case(case_id))
                elif method == "DELETE":
                    data = self._body()
                    service.delete_case(case_id, data.get("confirmation"))
                    self._send(200, {"deleted": True, "case_id": case_id})
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            resource = parts[2]
            if resource == "legacy":
                self._legacy_dispatch(case_id, parts, method)
                return
            if resource == "discovery":
                self._discovery_dispatch(case_id, parts, method)
                return
            if (
                resource == "integrations"
                and len(parts) == 5
                and parts[3] == "mock-email"
                and method == "POST"
            ):
                data = self._body()
                if parts[4] == "connect":
                    self._send(200, service.connect_mock_email(case_id, data))
                elif parts[4] == "sync":
                    self._send(200, service.sync_mock_email(case_id, data))
                else:
                    self._send(404, {"error": "Route not found"})
                return
            if resource == "assistant" and method == "POST" and len(parts) == 3:
                self._send(200, assistant.ask(case_id, self._body()))
                return
            if resource == "export" and method == "GET" and len(parts) == 3:
                self._send(200, service.export_case(case_id))
                return
            if resource == "evidence":
                if method == "POST" and len(parts) == 3:
                    self._send(201, service.add_evidence(case_id, self._body()))
                elif method == "GET" and len(parts) == 3:
                    self._send(200, {"evidence": service.list_evidence(case_id)})
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "documents":
                if method == "GET" and len(parts) == 3:
                    self._send(200, {"documents": service.list_documents(case_id)})
                elif method == "POST" and len(parts) == 3:
                    self._send(
                        201,
                        service.upload_document(case_id, self._body(7_000_000)),
                    )
                elif method == "DELETE" and len(parts) == 4:
                    service.delete_document(case_id, parts[3])
                    self._send(200, {"deleted": True, "document_id": parts[3]})
                elif len(parts) == 4 and parts[3] == "death-certificate" and method == "POST":
                    self._send(
                        200,
                        service.review_death_certificate(case_id, self._body()),
                    )
                elif len(parts) == 5 and parts[4] == "review" and method == "POST":
                    self._send(
                        200,
                        service.review_document(case_id, parts[3], self._body()),
                    )
                elif (
                    len(parts) == 5
                    and parts[4] == "download"
                    and method == "GET"
                ):
                    metadata, content = service.get_document(case_id, parts[3])
                    self.send_response(200)
                    self.send_header("Content-Type", metadata["mime_type"])
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header(
                        "Content-Disposition",
                        f"attachment; filename*=UTF-8''{quote(metadata['file_name'])}",
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "assets":
                if method == "POST" and len(parts) == 3:
                    self._send(201, service.add_asset(case_id, self._body()))
                elif method == "GET" and len(parts) == 3:
                    self._send(200, {"assets": service.list_assets(case_id)})
                elif method == "PATCH" and len(parts) == 4:
                    self._send(200, service.review_asset(case_id, parts[3], self._body()))
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "beneficiaries":
                if method == "POST" and len(parts) == 3:
                    self._send(201, service.add_beneficiary(case_id, self._body()))
                elif method == "GET" and len(parts) == 3:
                    self._send(
                        200, {"beneficiaries": service.list_beneficiaries(case_id)}
                    )
                elif method == "PATCH" and len(parts) == 4:
                    self._send(
                        200,
                        service.review_beneficiary(case_id, parts[3], self._body()),
                    )
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "people" and method == "GET" and len(parts) == 3:
                self._send(200, {"people": service.list_people(case_id)})
                return
            if (
                resource == "people"
                and len(parts) == 4
                and parts[3] == "entity-resolution"
                and method == "GET"
            ):
                self._send(
                    200,
                    {"suggestions": service.entity_match_suggestions(case_id)},
                )
                return
            if resource == "relationships" and method == "GET" and len(parts) == 3:
                self._send(200, service.relationship_graph(case_id))
                return
            if resource == "claims":
                if method == "GET" and len(parts) == 3:
                    self._send(200, {"claims": service.list_claims(case_id)})
                elif method == "POST" and len(parts) == 3:
                    self._send(201, service.add_claim(case_id, self._body()))
                elif method == "PATCH" and len(parts) == 4:
                    self._send(
                        200,
                        service.update_claim(case_id, parts[3], self._body()),
                    )
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "workflow":
                if method == "GET" and len(parts) == 3:
                    self._send(200, service.get_workflow(case_id))
                elif method == "POST" and len(parts) == 4 and parts[3] == "advance":
                    self._send(200, service.advance_workflow(case_id))
                else:
                    self._send(405, {"error": "Method not allowed"})
                return
            if resource == "audit" and method == "GET" and len(parts) == 3:
                self._send(200, {"events": service.list_audit_events(case_id)})
                return
            self._send(404, {"error": "Route not found"})

        def _check_write_origin(self) -> None:
            if self.command not in ("POST", "PATCH", "PUT", "DELETE"):
                return
            if self.headers.get("Sec-Fetch-Site", "").casefold() == "cross-site":
                raise ValidationError("Cross-site state-changing requests are not allowed")
            origin = self.headers.get("Origin")
            if not origin:
                return
            if settings.public_origin:
                if origin.rstrip("/") != settings.public_origin:
                    raise ValidationError("Request Origin does not match this site")
                return
            parsed_origin = urlparse(origin)
            host = self.headers.get("Host", "")
            expected = urlparse(f"http://{host}")
            origin_port = parsed_origin.port or 80
            expected_port = expected.port or 80
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed_origin.hostname != expected.hostname
                or origin_port != expected_port
                or parsed_origin.path not in ("", "/")
                or parsed_origin.query
                or parsed_origin.fragment
                or expected.hostname not in ("127.0.0.1", "localhost", "::1")
            ):
                raise ValidationError("Request Origin does not match the local app")

        def _handle(self) -> None:
            started = time.perf_counter()
            incoming = self.headers.get("X-Request-ID", "")
            self.request_id = incoming[:64] if incoming.isalnum() else secrets.token_hex(8)
            try:
                self._check_write_origin()
                self._dispatch()
            except TroveError as exc:
                self._send(exc.status_code, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                log.exception("unhandled request failure", extra={"request_id": self.request_id})
                self._send(500, {"error": "Internal server error", "request_id": self.request_id})
            finally:
                route = urlparse(self.path).path
                # ids are stripped from logged paths; bodies and query strings are never logged
                safe_route = "/".join(
                    part if len(part) < 20 else ":id" for part in route.split("/"))
                log.info("request", extra={"request_id": self.request_id, "method": self.command,
                                           "route": safe_route, "status": self.status_sent,
                                           "ms": round((time.perf_counter() - started) * 1000, 1)})
                if self.command != "GET" and settings.notifier != "dev":
                    try:
                        deliver_pending(service)
                    except Exception:
                        log.exception("notification delivery failed")

        do_GET = _handle
        do_POST = _handle
        do_PATCH = _handle
        do_PUT = _handle
        do_DELETE = _handle

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = TroveHTTPServer((host, port), Handler)
    server.trove = (service, legacy)  # type: ignore[attr-defined]
    return server


def run(host: str = "127.0.0.1", port: int = 8000, database_path: str = "trove.db",
        settings: config.Settings | None = None) -> None:
    settings = settings or config.current()
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    server = make_server(host, port, database_path, settings)
    stop = None
    if settings.run_worker:
        from trove_agent.services.worker import start_thread

        stop = start_thread(*server.trove, settings.worker_interval)  # type: ignore[attr-defined]
    log.info("listening", extra={"url": f"http://{host}:{server.server_port}", "env": settings.env,
                                 "database": server.trove[0].db.dialect})  # type: ignore[attr-defined]
    print(f"Trove listening at http://{host}:{server.server_port} ({settings.env})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Trove")
    finally:
        if stop is not None:
            stop.set()
        server.server_close()
