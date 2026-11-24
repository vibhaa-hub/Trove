# Security and compliance implementation status

This checklist is a prototype engineering status, not legal advice, an
attestation, certification, or claim of compliance. The blueprint names several
frameworks and regulated sectors, but their exact obligations depend on
jurisdiction, controller/processor role, product design, users, and contracts.
Production use requires a qualified legal/compliance assessment and independent
security review.

## Implemented prototype safeguards

| Blueprint area | Implemented here |
|---|---|
| Zero trust / identity | Loopback-only binding; local owner/reviewer password (PBKDF2). Nominees have separate accounts scoped to one case: one-time invite + registered email, email and phone OTP possession checks, mandatory TOTP MFA (encrypted secret, replay protection), TOTP step-up for attestations and downloads, lockout and rate limits. No SSO, passkeys, or third-party KYC. |
| Beneficiary verification / release | ID last-4 and DOB compared as keyed HMAC hashes, human ID review, reviewer-approved death certificate, k-of-n nominee quorum, dispute freeze, owner proof-of-life veto, cooling-off period, fraud-risk scoring, per-nominee document scopes. Not legal heirship or probate verification. |
| Audit integrity | Legacy events are stored in a per-case SHA-256 hash chain with a verification endpoint; this detects edits but is not an external, write-once log. |
| API security | JSON content-type and size checks, no CORS, same-origin checks for browser writes, security headers, parameterized SQL. |
| Data minimization | Generated mock mail is fictional and local; email/document text is processed in memory; SQLite stores digests and extracted signals, not raw text. |
| Data protection | Uploaded files are Fernet-encrypted in a local vault; references are masked; sensitive AI requests require explicit consent. SQLite `secure_delete` is enabled. |
| Fraud / AI safety | LLM output is restricted to known asset types; document text is treated as untrusted; death-certificate, beneficiary, entity-match, and asset outputs need human review and are not legal determinations. |
| Audit / accountability | Case events are recorded; case JSON export, document download/deletion, and confirmed case deletion are available. |
| Security configuration | Secrets/SQLite database are ignored by Git; the prototype refuses non-loopback server binding. |

These are limited technical controls. Only uploaded vault files are encrypted
at rest; the SQLite database is not encrypted, local key management is not
hardened, and HTTP is local plaintext. There is no managed cryptographic key
lifecycle, MFA, SSO, tenant isolation, multi-user authorization, formal threat
model, vulnerability-management program, tested incident response,
backup/restore guarantees, or compliance audit. The application is a modular
monolith, not a production microservice deployment.


## Production controls added

| Control | Implementation | Remaining gap |
|---|---|---|
| Multi-tenant authorization | Owner accounts; every `/cases/{id}` route checks ownership (404 otherwise) | No organisation/team sharing model |
| Separation of duties | Independent reviewer accounts with mandatory TOTP; owners blocked from reviews in production | No four-eyes approval for very high-value releases |
| Encryption at rest | Vault files, contact fields, TOTP secrets, queued messages (Fernet) + HMAC blind indexes | Key is an env secret, not an HSM/KMS with rotation; full-database encryption should be enabled at the storage layer |
| Transport | Caddy automatic TLS, HSTS, Secure cookies | WAF/DDoS protection is the hosting provider's job |
| Notifications | SMTP / Twilio / webhook via retried transactional outbox | Provider-side delivery receipts not tracked |
| Availability / ops | Health + readiness probes, JSON logs, worker with advisory lock, backup command | No tested restore drill, metrics/alerting stack, or SIEM integration yet |
| Rate limiting | Per-IP and per-account, in process memory | Not shared across multiple web replicas; use Redis or the proxy when scaling out |

## Framework and jurisdiction gap map from `Trove.txt`

| Blueprint reference | Status | What remains to assess/build |
|---|---|---|
| GDPR / UK GDPR | Partial: notice, rights list and transfer/residency guidance per country (`core/jurisdictions.py`); not assessed | Lawful basis, controller/processor roles, notices, rights workflows, DPIA, retention, breach procedures, processor terms, and transfer safeguards. |
| India DPDP Act | Partial: DPDP notice, rights incl. s.14 nomination, grievance-officer duty shown per case; not assessed | Applicability and current rules, notices/consent, processor governance, rights/grievance handling, retention/deletion, breach response, and required operational roles. |
| eIDAS | Not implemented | Applicable trust services, qualified identity/signature provider, assurance level, evidence preservation, and jurisdictional assessment. |
| ISO 27001 | Not certified | ISMS scope, risk register, control selection/SoA, policies, operating evidence, audit, and certification assessment. |
| ISO 27701 | Not certified | Privacy information management system and integration with an established ISMS. |
| SOC 2 | Not attested | Defined system boundary, control operation over time, evidence collection, independent Type I/II examination, and remediation. |
| FCA / PRA | Not assessed | UK entity/product perimeter, authorization/permissions, outsourcing, operational resilience, consumer duty, financial crime, and records requirements. |
| RBI | Not assessed | Product/entity applicability, outsourcing/IT controls, data residency/retention, security, and applicable payment/banking obligations. |
| SEBI | Not assessed | Regulated-entity/product applicability, intermediary requirements, investor protections, cyber resilience, and recordkeeping. |
| IRDAI | Not assessed | Insurer/intermediary role, policyholder data, outsourcing, cyber controls, grievance and claims handling, and retention obligations. |
| SEC / FINRA | Not assessed | Broker/dealer/adviser applicability, supervision, books/records, communications, privacy, and cybersecurity obligations. |
| Probate regulations | Partial: per-country legal-document release gate (IN legal-heir/succession, UK grant, US letters/affidavit, EU certificate of succession) with reviewer approval and deadlines | Lawyer review of each profile, state/member-state variations, institution-specific forms, estate authority, executor/beneficiary verification, evidence standards, forms, and institution procedures. |
| MFA / SSO / biometrics | Partial (nominee TOTP only) | Production identity provider, authentication assurance, recovery, authorization, and audit integration. |
| Encryption / tokenization / masking | Partial | Fernet encryption applies to uploaded vault files and detected references are masked; database encryption, managed key lifecycle, tokens, transport termination, and end-to-end controls do not. |
| WAF / DDoS / EDR / container security | Not implemented | These require a production deployment architecture and managed infrastructure/services. |
| SIEM / SOC / threat detection | Not implemented | Centralized, privacy-aware monitoring, alerting, response staffing, and evidence retention are absent. |

## Country rules (implemented)

Each case stores a country code (IN, UK, US, EU, OTHER). `trove_agent/core/jurisdictions.py`
holds one profile per country with: how entitlement works, legal documents required for
release, policy defaults and production minimums, privacy law/rights/notice, deadlines (computed
from the date of death), official tracing services and the recommended hosting region.

| Control | Behaviour |
|---|---|
| Legal-document gate | Release needs reviewer approval of the country's proof of entitlement when the policy is "required" (default for IN/UK/US/EU; "advisory" for OTHER). Uploaded legal documents never appear in the released vault. |
| Policy minimums | In production the cooling-off period cannot go below the country minimum (IN 48h, others 24h). |
| Privacy notice | Owners see the regime, regulator, rights, retention and transfer rules; nominees see the notice and their rights. |
| Data residency | `TROVE_DATA_REGION` declares where a deployment stores data; `TROVE_ENFORCE_RESIDENCY=1` refuses cases whose country should be hosted elsewhere. |
| Country detection | Free-text jurisdictions are matched by whole names only (fixes "Living in Canada" being read as India). |

The profiles are public-source guidance with a `last_reviewed` date, not legal advice. Have
counsel in each country review them before production use.

## Before production

1. Define countries, customer types, operators, data flows, retention, and
   regulated activities with counsel and compliance owners.
2. Threat-model the application; add real identity, tenant/case authorization,
   MFA, encrypted storage/transit, managed secrets/keys, secure deployment,
   rate limits, monitoring, backups, and tested incident response.
3. Build consent withdrawal and data-subject request workflows with auditable
   outcomes, retention controls, deletion/backup semantics, and vendor review.
4. Validate every connector's scope, authorization, data minimization, and
   deletion path. Do not connect real inboxes in this demo.
5. Complete independent testing and the appropriate formal assessments,
   attestations, certifications, and regulator/contractual approvals.
