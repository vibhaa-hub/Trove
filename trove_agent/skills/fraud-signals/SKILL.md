---
name: fraud-signals
description: Explain fraud-risk levels on a death claim (what raises risk, what a reviewer should check) without teaching how to evade detection.
triggers: fraud, risk, suspicious, flag, flags, high, override, fake, forged, dispute, disputed
tools: get_release_status, get_graph_insights
---
# Fraud signals

## Procedure
1. Call `get_release_status` and read each request's risk level and flag codes.
2. Explain to the REVIEWER what to check for each flag:
   - owner_recently_active / checkin_after_death: contact the owner on every
     registered channel before anything else
   - future_death_date: the certificate is invalid as entered
   - certificate_name_mismatch: compare the certificate with the owner's name;
     request a certified copy
   - recent_nominee: confirm the owner really added this nominee
   - identity_mismatches / auth_failures: possible account takeover attempt
   - dispute: speak to every nominee; do not release while unresolved
3. A reviewer override must record a written reason.

## Rules
- Never explain how to avoid triggering a flag.
- Risk scores are signals for human review, not a fraud finding.

## Offline summary
High risk blocks release until a reviewer investigates and records an override
reason. Contact the owner first when they were recently active, verify the
certificate with the issuing registry, and resolve every dispute.
