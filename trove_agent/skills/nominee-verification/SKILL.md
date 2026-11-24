---
name: nominee-verification
description: Guide owners and nominees through nominee enrolment and identity verification (invite code, email and phone codes, authenticator MFA, ID review) and troubleshoot stuck nominees.
triggers: nominee, nominees, beneficiary, beneficiaries, invite, otp, code, mfa, authenticator, identity, verify, verification, verified, enrol, enroll, locked, suspended, password, stuck
tools: get_nominee_progress
---
# Nominee verification

## Procedure
1. Call `get_nominee_progress` for per-step counts (enrolled, email verified,
   phone verified, MFA, identity approved / pending / rejected, locked,
   suspended).
2. Point to the first step where nominees are stuck, and the fix:
   - not enrolled: the owner issues a new invite code and hands it over in person
   - email/phone code not verified: "Send a new code"; codes last 10 minutes, 5 tries
   - MFA: add the setup key to an authenticator app; codes change every 30
     seconds and each code works only once
   - identity pending: the reviewer must approve or reject in the workspace
   - locked: wait 15 minutes (5 failed sign-ins)
   - suspended: 3 identity mismatches; the owner/reviewer must investigate
3. A nominee is "verified" only when all five steps are done.

## Rules
- Never ask anyone to share a one-time or authenticator code.
- Do not reveal which identity field did not match.

## Offline summary
Nominees must accept the invite with their registered email, verify the email
and phone codes, turn on an authenticator app, and pass identity review. Locks
clear after 15 minutes; suspensions need reviewer action.
