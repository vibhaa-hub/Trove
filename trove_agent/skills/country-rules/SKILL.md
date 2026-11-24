---
name: country-rules
description: Explain what changes by country (India, UK, US, EU): succession and probate documents, release requirements, deadlines, privacy law and data residency.
triggers: country, law, legal, probate, succession, heir, certificate, grant, letters, testamentary, deadline, deadlines, tax, privacy, gdpr, dpdp, ccpa, rights, residency, jurisdiction, india, uk, us, eu
tools: get_country_rules
---
# Country rules

Use this when the question depends on where the person lived or died.

## Procedure
1. Call `get_country_rules`. It returns how entitlement works in that country, the legal
   documents a release request must carry (and whether they are required or advisory),
   key deadlines (with due dates when the date of death is known), and the privacy law.
2. Answer with the country-specific steps. Name the documents exactly as listed.
3. Flag overdue deadlines first.
4. Always include the disclaimer: this is organisational guidance, not legal advice; rules
   vary by state or region and should be confirmed with a local lawyer.

## Offline summary
Trove applies a rules profile per country. The release gate "Legal documents" asks for the
country's proof of entitlement (India: legal-heir or succession certificate or probate; UK:
grant of probate / letters of administration / Confirmation; US: letters testamentary or a
small-estate affidavit; EU: European Certificate of Succession or a national certificate).
Open "Country & compliance" in the workspace for deadlines and privacy rights.
