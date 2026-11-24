# Trove Agent (local demo)

Trove is a local Python prototype of the Plan and Discovery workflows described
in `Trove.txt`. It has a browser workspace, a Mistral case assistant (optional),
an encrypted local document vault, post-death human-review gates, beneficiary
and relationship records, and a claims tracker. The email connector generates
fictional per-case messages and never connects to a real mailbox.

## Project structure

```text
Trove/
├── trove_agent/                 Python package (run with: python -m trove_agent)
│   ├── __main__.py              CLI: serve | worker | create-reviewer | backup | warm-models | check
│   ├── config.py                All settings, read from environment variables
│   │
│   ├── api/                     BACKEND – HTTP layer only
│   │   └── server.py            Routes, cookies, security headers, serves the built UI
│   │
│   ├── core/                    BACKEND – foundations used by everything
│   │   ├── db.py                SQLite (dev) / PostgreSQL (prod) adapter
│   │   ├── store.py             Cases, documents, assets, beneficiaries, claims, owner accounts
│   │   ├── security.py          TOTP MFA, one-time codes, keyed hashes, rate limiter, audit hash chain
│   │   ├── vault.py             Encrypted document storage
│   │   ├── backup.py            Database + vault backup
│   │   └── logging_setup.py     JSON logs
│   │
│   ├── services/                BACKEND – Plan mode and operations
│   │   ├── legacy.py            Nominee verification, release gates, fraud risk (Plan mode)
│   │   ├── reviewer.py          Independent reviewer accounts and review queue
│   │   ├── notify.py            Email (SMTP) and SMS (Twilio / webhook) delivery
│   │   ├── worker.py            Background timers, reminders, retries, cleanup
│   │   └── graph.py             Knowledge graph and its insights
│   │
│   ├── discovery/               BACKEND – Discovery mode
│   │   ├── bootstrap.py         Turns SMS / email / cloud / documents into asset leads
│   │   ├── connectors.py        IMAP mailbox and folder/USB connectors
│   │   └── mock_email.py        Fictional demo inbox
│   │
│   ├── agents/                  AI – code
│   │   ├── assistant.py         Case assistant: loads skills, runs case tools, optional LLM
│   │   ├── skill_registry.py    Reads skills/ with progressive disclosure
│   │   ├── rag.py               Knowledge search over skills/ and knowledge/
│   │   ├── encoders.py          Local encoder model (embeddings, zero-shot classification)
│   │   ├── rules.py             Rule-based document agent and workflow gates
│   │   └── llm.py               Optional Mistral integrations
│   │
│   ├── skills/                  AI – instructions (Markdown, no code)
│   │   └── <skill-name>/SKILL.md (+ reference/*.md)
│   ├── knowledge/               AI – documents the RAG search reads
│   │
│   └── web/                     FRONTEND build output (generated – do not edit)
│
├── frontend/                    FRONTEND source – React 18 + TypeScript + Vite
│   ├── index.html               /           owner workspace entry
│   ├── nominee.html             /nominee    nominee portal entry
│   ├── reviewer.html            /reviewer   reviewer console entry
│   └── src/
│       ├── owner/               Workspace shell, auth, sections/ (overview, plan, discovery,
│       │                        documents, people, claims, graph, assistant)
│       ├── nominee/             Nominee journey: invite → OTP → MFA → ID → claim → vault
│       ├── reviewer/            Independent reviewer queue
│       ├── shared/              API client, hooks, accessible components, design tokens (styles.css)
│       └── test/                Vitest + Testing Library + axe accessibility tests
│
├── tests/                       Python unittest suites
├── deploy/Caddyfile             HTTPS reverse proxy
├── Dockerfile, docker-compose.yml, .env.example
└── Trove.txt                    Original product blueprint
```

Dependencies only point downwards:
`api → services / discovery / agents → core`, and `frontend` only talks to `api` over HTTP.

## Run (inside a virtual environment)

Use Python 3.10 or later. Install libraries into a project-local `venv`, not
into the system Python:

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# build the web UI once (needs Node.js 18+)
npm --prefix frontend ci
npm --prefix frontend run build

python -m trove_agent
```

If PowerShell blocks `Activate.ps1`, either run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip activation
and call the venv interpreter directly: `.\venv\Scripts\python.exe -m trove_agent`.

* Owner / reviewer workspace: `http://127.0.0.1:8000/`
* Nominee & beneficiary portal: `http://127.0.0.1:8000/nominee`
* Independent reviewer console: `http://127.0.0.1:8000/reviewer`

### Working on the UI

Run the API and the Vite dev server side by side; Vite proxies API calls to port 8000 and
reloads instantly on save:

```powershell
python -m trove_agent                 # terminal 1 – API on :8000
npm --prefix frontend run dev         # terminal 2 – UI on http://localhost:5173
npm --prefix frontend test            # component + axe accessibility tests
```

### Accessibility (WCAG 2.2 AA)

* Skip link, landmarks (`header`, `nav`, `main`, `footer`), one `h1` per view; focus moves
  to the heading on every section change, and back/forward work (hash routing).
* Every control has a visible label; hints and errors are tied with `aria-describedby`;
  invalid fields get `aria-invalid`; form errors use `role="alert"`.
* Status never relies on colour alone (icon + text badges); contrast ≥ 4.5:1 for text and
  ≥ 3:1 for control borders and focus rings in light and dark themes.
* WAI-ARIA patterns: tabs (arrow keys / Home / End), native `<dialog>` modals with focus
  return (no `alert`/`confirm`/`prompt`), live regions for toasts and the assistant log,
  sortable table headers with `aria-sort`, steppers with `aria-current="step"`.
* Targets ≥ 24 px (most 44 px), reflow at 320 px, `prefers-reduced-motion`,
  `prefers-color-scheme`, Windows High Contrast (`forced-colors`) and print styles.
* One-time codes accept paste and SMS autofill (`autocomplete="one-time-code"`), passwords
  have show/hide and a live requirements checklist (accessible authentication, 3.3.8).
* The knowledge-graph picture has a full table equivalent.
* `npm test` runs axe-core against each app; a negative-control test proves the checks fire.

## Test every flow live

### 1. Sample use case you can click through yourself

```powershell
python -m trove_agent demo --reset      # builds demo.db with the fictional "Rao family"
python -m trove_agent --db demo.db      # open http://127.0.0.1:8000
```

The command prints (and saves to `demo-logins.txt`) every login, plus scenarios to try.

* **Owner** Asha Rao has a Plan case (her own vault) and a Discovery case (her late father).
* **Nominees** are staged at different points: Ravi and Meera are verified, Arjun's ID is
  waiting for review, and Kiran has only an invite code so you can enrol from scratch.
* **Reviewer** `rita` can sign in at `/reviewer`.
* Sample uploads (SMS export, mailbox, statement, certificates, ID) are in `demo-files/`.
* Authenticator codes: add a printed setup key to an authenticator app, or run
  `python -m trove_agent totp <SETUP KEY>`.

Use a separate private browser window for each nominee, since they share one cookie name.

### 2. Watch the automated journeys in a browser

```powershell
npm --prefix frontend run build
npm --prefix frontend run e2e:watch     # opens Edge and runs slowly so you can follow along
npm --prefix frontend run e2e           # same, headless (about 35 s)
npm --prefix frontend run e2e:report    # HTML report with screenshots/traces of failures
```

Playwright starts the real server on a fresh temporary database and drives your installed
Microsoft Edge (no browser download needed) through:

* **Plan**: owner setup → policy → add nominee → invite → nominee enrols → email and phone
  codes (read from the dev inbox) → authenticator MFA → ID → owner approval → death claim
  → certificate approval → release → nominee unlocks and downloads the will.
* **Discovery**: what you have → SMS export + statement → demo inbox → corroborated lead →
  promote → confirm → knowledge graph → assistant.

At each major screen it also runs axe in the real browser, so colour contrast and target
size are measured, not assumed.

## Production deployment

```text
Internet ─► Caddy (automatic HTTPS) ─► web (python -m trove_agent serve) ─► PostgreSQL
                                        worker (python -m trove_agent worker) ─┘
```

```bash
cp .env.example .env          # fill in every secret (instructions inside)
docker compose up -d --build
docker compose exec web python -m trove_agent create-reviewer alice   # prints TOTP key once
docker compose exec web python -m trove_agent backup --out /data/vault/backups
```

With `TROVE_ENV=production` the app **refuses to start** unless `TROVE_VAULT_KEY`,
`TROVE_PEPPER`, `TROVE_PUBLIC_ORIGIN` and a PostgreSQL `DATABASE_URL` are set, and it
rejects the on-screen dev outbox. It then also:

| Area | Behaviour |
|---|---|
| Accounts | Many owners (`/auth/register`, email + password, lockout). Each owner sees only their own cases; other cases return 404 |
| Separation of duties | Owners cannot approve ID documents or death certificates. Independent reviewers do, at `/reviewer` (password + mandatory TOTP), and can open only documents awaiting their review. Actions are logged as `reviewer:<name>` |
| Encryption | Files are encrypted in the vault. Nominee and owner emails/phones and owner account emails are Fernet-encrypted in the database, with HMAC blind indexes for lookups. TOTP secrets and queued messages are encrypted too |
| Notifications | Transactional outbox → SMTP email and Twilio (or any HTTPS gateway) SMS. Up to 5 retries; message bodies are wiped after delivery |
| Worker | Releases requests when the cooling-off period ends, sends owner check-in reminders, warns nominees after two missed check-ins (this never releases anything), retries delivery, and purges expired sessions and codes. A PostgreSQL advisory lock ensures only one worker runs |
| Web hardening | Secure + HttpOnly + SameSite=Strict cookies, HSTS, Origin pinned to `TROVE_PUBLIC_ORIGIN`, CSP, per-IP rate limits (real client IP taken from the proxy header) |
| Containers | Non-root, read-only filesystem, all capabilities dropped, database on an internal-only network |
| Operations | `/health` (liveness), `/ready` (database, vault key, storage), JSON logs with request IDs (no bodies, ids masked), `backup` command (pg_dump/SQLite backup plus vault files plus SHA-256 manifest; keys are deliberately excluded) |

## Models: encoders first, LLM optional

To keep costs down, Trove uses a local **encoder-only** model (System-1 style:
it turns text into vectors and never generates text). The default is
`BAAI/bge-small-en-v1.5` via ONNX Runtime: about 67 MB, runs on CPU, no per-call cost.
It is used for:
- **RAG** over skills, references and [trove_agent/knowledge/](trove_agent/knowledge/)
- **Semantic skill routing**
- **Zero-shot classification** of bank statement and SMS lines that no rule
  matched (loan EMIs, salary, crypto, and so on). It declines to guess when unsure.
- **Entity resolution** of near-duplicate institutions in the knowledge graph

Mistral (a generative LLM) is used only when a user ticks consent. Set
`TROVE_ENCODER=hashing` for a no-download fallback. The Docker image builds the
model and the RAG index in at build time.

## Country rules (India, UK, US, EU, other)

Pick the country when you start a case (plus an optional state or region). The country drives:

| | India | UK | US | EU | Other |
|---|---|---|---|---|---|
| Release gate: legal documents | Legal-heir / succession certificate or probate | Grant of probate / letters of administration / Confirmation | Letters testamentary / administration or small-estate affidavit | European Certificate of Succession or national certificate | Court/government entitlement document (advisory) |
| Privacy law shown | DPDP Act 2023 | UK GDPR + DPA 2018 | State laws (CCPA/CPRA etc.) | GDPR | Local law; GDPR-level controls |
| Example deadlines | Register death: 21 days | Register death: 5 days; IHT: end of 6th month | Form 706: 9 months | e.g. Germany disclaim: 6 weeks | — |
| Recommended hosting | in | uk | us | eu | any |

* **Country & compliance** (workspace section) shows the succession process, documents,
  deadlines with real due dates once the date of death is known, privacy rights, data
  residency status and official tracing services.
* Nominees upload the legal document on their claim; the owner or an independent reviewer
  approves it. Switch a case to "advisory" in Plan > Release policy.
* `GET /jurisdictions` and `GET /jurisdictions/{code}` expose the profiles;
  `GET /cases/{id}/compliance` returns a case's rules and computed deadlines.
* Set `TROVE_DATA_REGION` (and optionally `TROVE_ENFORCE_RESIDENCY=1`) per deployment.

This is organisational guidance from public sources, not legal advice; rules vary by state and
change over time. Profiles live in `trove_agent/core/jurisdictions.py` for counsel to review.

## Knowledge graph

`GET /cases/{id}/graph` builds a per-case graph:
Person → Email/Phone → Asset → Institution → Evidence, plus nominees and
beneficiaries. The snapshot is saved in `kg_nodes`/`kg_edges` and can be exported to Neo4j.
It flags:
- nominees who share a phone or email (their verifications aren't independent)
- a nominee using the deceased's own contact details
- duplicate institutions or people
- leads confirmed by two or more sources that aren't in the asset register yet
- confirmed assets with no claim tracker

## Connectors

| Connector | Status |
|---|---|
| **IMAP** (Gmail, Outlook.com, Yahoo, iCloud, Zoho, any IMAPS server) | Working. Read-only folder, `BODY.PEEK` (nothing marked as read). Headers are checked first, so only finance-looking messages are downloaded. The app password is used once and never stored |
| **Folder / USB / NAS** | Working. Only under `TROVE_IMPORT_ROOT` (mounted read-only). Path traversal and symlinks are blocked |
| Uploads: mbox/eml, SMS XML/CSV, cloud ZIP, documents | Working (see Discovery mode) |
| Gmail API, Microsoft Graph (Outlook/OneDrive), Google Drive, Dropbox | Planned. Each needs an OAuth client you register; they use the same `scan()` interface |

## The two modes

### Plan mode — legacy vault with verified release

The owner (while alive) uploads documents, registers assets and adds
nominees. For each nominee the owner records the email and phone the nominee
must prove they control, optionally the last 4 characters of their ID and their
date of birth (stored only as keyed HMAC hashes), and which document types that
person may see. Each nominee gets a one-time invite code to hand over in person.

After a death, nothing is released until **every** gate passes:

| # | Gate | What it stops |
|---|---|---|
| 1 | One-time invite code (7-day expiry) + the registered email + strong password | Strangers enrolling |
| 2 | 6-digit OTP to the registered **email** (hashed, 10 min, 5 tries) | Wrong person using the email |
| 3 | 6-digit OTP to the registered **phone** | Single-channel compromise |
| 4 | Mandatory authenticator-app TOTP MFA (RFC 6238, replay-protected, secret encrypted at rest) | Password theft |
| 5 | ID last-4 + DOB matched against owner-registered hashes, ID document reviewed by a human; 3 mismatches suspend the account and alert the owner | Impersonation |
| 6 | Death certificate uploaded by a verified nominee, approved by a reviewer; name in the certificate is cross-checked | Fake death claims |
| 7 | k-of-n verified nominees confirm, each with a fresh TOTP (step-up); any dispute freezes release | A single rogue relative |
| 8 | Owner is alerted on email and phone the moment a claim opens; **"I am alive"** check-in cancels it | Claims against a living owner |
| 9 | Cooling-off period (default 72 h) | Rushed fraud |
| 10 | Fraud-risk scoring: recent owner check-in, check-in after claimed death, future death date, certificate name mismatch, nominee added < 30 days before the claim, identity mismatches, repeated auth failures, disputes. HIGH risk blocks release unless a reviewer records an override note | Pattern-based fraud |

After release each nominee sees only the document types granted to them
(executors see everything). Every download needs a TOTP step-up within the last
10 minutes. Login lockout after 5 failures (15 min) alerts the other nominees.
Every legacy action goes into a per-case SHA-256 **hash-chained audit log**;
editing any past entry is detected and shown in the workspace.

**Try it:** create a *Plan* case → set the policy (use waiting period `0` to
test) → add two nominees → open `/nominee` in a private window and accept an
invite. OTP codes appear in the portal's *dev outbox* (this prototype has no
SMS/email provider; configure SMTP and Twilio, see `services/notify.py`,
in production). Add the setup key to any authenticator app. For scripted
testing only, you can print a current code with:

```powershell
.\venv\Scripts\python.exe -c "from trove_agent.core.security import totp_at, current_step; print(totp_at('YOURSETUPKEY', current_step()))"
```

### Discovery mode — start from whatever the family has

For when the person never used Trove and the family has little or no access.
Open the **Discovery** tab:

1. **What do you have?** Tick what you hold (death certificate, name/DOB, ID,
   email address, email access, phone number, unlocked phone, cloud access, one
   or two documents) and add known emails, phones and employers. Trove builds a
   prioritised starting plan.
2. **Add a source** — any one is enough to begin:
   * **Phone SMS export** (`.xml` from SMS backup apps, `.csv`, or `.txt`):
     bank, card, insurer and broker alerts name the institution and the last digits.
   * **Email export** (`.mbox` from Google Takeout, or `.eml` files).
   * **Cloud export** (`.zip` of Drive/OneDrive/Dropbox folders, or a `.txt`
     file listing): file names like `Last_Will.pdf` or `LIC_policy.pdf` plus
     text inside TXT/CSV/PDF files. ZIP paths and sizes are guarded against
     traversal and zip bombs.
   * **Document** (a single bank statement is ideal): every line is scanned, so
     recurring debits reveal insurance premiums, SIPs, loan EMIs and cards.
3. **Leads** — the same institution found in several independent sources gains
   confidence. Promote real leads into the asset register (they then flow into
   the existing review → claims workflow), or dismiss them.
4. **Coverage map** — shows which asset classes are still gaps, where to look,
   and official unclaimed-asset tracing services for India, the UK and the US
   (check each service's current availability).
5. **No access?** — lawful recovery routes for phones, Google, Apple and
   Microsoft accounts. Never guess passwords or bypass device locks.

Exports are parsed in memory; only institution, asset type, masked references
and short sender/date descriptors are stored. XML is parsed with `defusedxml`.

Open `http://127.0.0.1:8000`. Trove binds to loopback only and stores case data
in `trove.db`; encrypted documents and the local Fernet key are stored in the
adjacent `trove_vault` directory. On first run, create a local owner password
(12+ characters). The optional Mistral assistant reads `MISTRAL_API_KEY` from
the process environment or a `.env` file in the project working directory. Use
dotenv syntax with an equals sign (not a colon):

```text
MISTRAL_API_KEY=your-new-key
```

Restart Trove after changing `.env`. Keep the key private; never paste it into
chat, source code, or screenshots. The assistant only sends a question/minimal
case summary after you check the explicit consent box. Mock email and document
analysis use local rules and send no document or email text to Mistral.

```powershell
$env:MISTRAL_API_KEY = "your-key"
python -m trove_agent
```

## Sample Discovery workflow

1. Sign in, then create a Discovery case with fictional details.
2. Upload a synthetic death-certificate text/PDF file as the **Death
   certificate** document type. Use **Mark document reviewed** only after you
   have manually inspected it. This local review is not legal verification.
3. Add other fictional document examples, or enter any reserved `@example.test`
   address to generate a fictional case-specific mock inbox. Select messages
   and choose **Analyze selected samples**.
4. Review/reject each asset lead. Add beneficiary records and record review of
   their supporting documents. This is not a determination of heirship.
5. Advance through the case workflow, create claim tracker entries, and update
   their statuses manually. Trove does not submit claims to institutions.

There is no OAuth, email provider, or email network request. Inbox content is
generated for the selected case, held in memory, and never fetched from or sent
to an external email service. The app does not create a pre-seeded demo case.
Trove stores only sample message IDs, subjects, digests, extracted signals,
and candidate leads—not sample message bodies. Repeated syncs skip duplicates.

Uploads are limited to 5 MB and supported formats are UTF-8 TXT, EML, CSV,
JSON, and text-based PDF (100 pages maximum). Files are encrypted using
Fernet before being saved; extracted text is analyzed in memory and is not
stored in SQLite. Set `TROVE_VAULT_KEY` to a valid Fernet key if supplying a
key yourself; losing the key makes vault documents unrecoverable. The generated
local key is not a managed key-management solution.

## Architecture and API

The project is a **modular monolith**, not a deployed microservice system:
`api.py` exposes the local HTTP API, `store.py` coordinates SQLite and case
workflows, `agents.py` contains local analysis and workflow gates, `vault.py`
handles encrypted file storage, `mock_email.py` generates synthetic emails,
and `llm_agents.py` contains optional Mistral integrations. Modules are
separated by domain so they can be extracted later; there is no service mesh,
distributed identity, or cross-service transaction layer.

Discovery flow: local owner authentication → case intake → encrypted death
document upload → explicit manual review → evidence analysis and asset-lead
review → beneficiary-document review → claims tracking → manual settlement
tracking. No AI output constitutes legal or financial verification.

The browser UI is at `/`; the JSON route overview is at `/api`. All case routes
require the local owner session. Important endpoints include:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/auth/status` | Check setup and local session state |
| `POST` | `/auth/setup`, `/auth/login`, `/auth/logout` | Local single-owner authentication |
| `POST` | `/cases/{id}/documents` | Encrypted upload and local analysis |
| `GET` | `/cases/{id}/documents` | List vault metadata |
| `GET` | `/cases/{id}/documents/{document_id}/download` | Decrypt/download a document |
| `POST` | `/cases/{id}/documents/death-certificate` | Record human review or rejection |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Delete a vault document and its derived leads |
| `GET` | `/cases/{id}/relationships` | Return people/assets/document graph data |
| `GET` | `/cases/{id}/people/entity-resolution` | Suggest possible duplicate people for human review |
| `GET` / `POST` | `/cases/{id}/claims` | List/create manual claim trackers |
| `GET` | `/cases/{id}/export` | Export case metadata and records as JSON |
| `GET` | `/integrations/mock-email/status` | Describe the mock-only connector |
| `POST` | `/cases/{id}/integrations/mock-email/connect` | Generate a fictional inbox for a `@example.test` address |
| `POST` | `/cases/{id}/integrations/mock-email/sync` | Analyze selected sample messages locally |
| `POST` | `/cases/{id}/assistant` | Ask Mistral with explicit consent |
| `GET` | `/cases/{id}/export` | Export case metadata and records as JSON |
| `DELETE` | `/cases/{id}` | Delete case and associated records (`{"confirmation":"DELETE"}`) |

Other case, asset, beneficiary, workflow, and audit endpoints are listed at
`/api`. Image OCR, real email and financial institution integrations,
automated claim filing, legal identity verification, and automatic entity
merging are not implemented.

## Security and compliance status

This version implements some prototype safeguards: loopback-only binding,
single-owner password authentication with salted PBKDF2 hashes and expiring
HttpOnly/SameSite cookies, same-origin checks for browser state-changing
requests, restrictive CSP/security headers, request-size limits, parameterized
SQL, explicit LLM consent, generated fictional inboxes, no raw email/document
text in SQLite, Fernet-encrypted uploaded files, masked references,
case export/deletion, SQLite `secure_delete`, and human-review gates.

**This is not compliant or certified against GDPR, UK GDPR, India DPDP Act,
eIDAS, ISO 27001, ISO 27701, SOC 2, FCA/PRA, RBI, SEBI, IRDAI, SEC, FINRA, or
probate rules.** A code sample cannot by itself meet these regimes. Among other
things, the prototype still lacks multi-user identity and case-level
authorization, MFA/SSO, encrypted database and managed key lifecycle,
production-grade consent
and data-subject request handling, jurisdiction/retention policies, incident
response, tested backups/recovery, vendor/data-transfer assessments, regulated
institution agreements, independent penetration testing, an ISMS/SOC audit,
and qualified legal/compliance review. SQLite `secure_delete` is not a
cryptographic-erasure guarantee, especially on SSDs/backups.

See [SECURITY_AND_COMPLIANCE.md](./SECURITY_AND_COMPLIANCE.md) for a section-by-section
control/gap map based on the blueprint. Do not use real estate, identity, email,
or financial data in this prototype, do not expose it to a network, and do not
represent it as production-ready.

## Assistant: skills with progressive disclosure

The case assistant is one runtime that loads **skills** from
[trove_agent/skills/](trove_agent/skills/), not a set of separate LLM agents:

1. **Catalog**: only each skill's one-line description is in the prompt.
2. **Instructions**: a skill's `SKILL.md` body is read only when the model calls
   `load_skill` (or the local router picks it).
3. **References**: files such as `discovery-playbook/reference/india.md` are
   read only through `read_skill_resource`.
4. **Tools**: a skill's read-only case tools are offered to the model only
   after that skill is loaded. The case is bound on the server, and tool output
   has no names, contacts, IDs or account numbers.

**The LLM is used only when needed.** With the consent box unticked (or no
`MISTRAL_API_KEY`), questions are answered locally: a keyword router picks
skills, runs their tools, and returns the skill's offline summary with live
case facts. Each answer shows which skills, tools and references were used.
`GET /skills` lists the catalog. To add a skill, add a folder with a `SKILL.md`.

## Tests

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix frontend test
```

`tests/test_legacy_and_discovery.py` covers the full nominee release flow,
owner veto, high-risk blocking, identity-mismatch suspension, single-use
invites, lockout, OTP attempt limits, audit-tamper detection, TOTP replay, and
the SMS / mbox / ZIP / statement discovery parsers.
