---
name: legacy-release
description: Explain the Plan-mode legacy vault release process, the security gates, and why a release request is or is not released yet.
triggers: release, released, vault, gate, gates, quorum, waiting, cooling, veto, alive, checkin, check-in, death, certificate, policy, sealed, open, opened
tools: get_release_status
---
# Legacy release

Use this for questions about when and how the vault opens after a death.

## Procedure
1. Call `get_release_status`. It returns the policy (quorum, waiting period,
   check-in interval), nominee verification counts, and each release request
   with its gates (passed or failed) and risk level.
2. If a request exists, list the gates that have NOT passed, in order, and the
   action that clears each:
   - certificate: a reviewer must approve the death certificate
   - quorum: more fully verified nominees must confirm with a fresh MFA code
   - no_dispute: a dispute froze release; the reviewer must investigate
   - waiting: the cooling-off period is still running (give the time left)
   - liveness: the owner checked in, so the request was cancelled
   - risk: high fraud risk; load `fraud-signals` to explain
3. If no request exists, explain how a verified nominee opens one.

## Rules
- Never suggest bypassing a gate. Gates exist to stop false death claims.
- Do not reveal nominee names or contact details.

## Offline summary
The vault opens only when every gate passes: a reviewer-approved death
certificate, the required number of verified nominee confirmations, no dispute,
the cooling-off period over, no owner check-in (proof of life), and acceptable
fraud risk. The release request card shows which gates are still failing.
