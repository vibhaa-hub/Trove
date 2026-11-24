---
name: discovery-playbook
description: Find a deceased person's unknown assets starting from minimal information (phone, email, one document) and explain coverage gaps and next steps.
triggers: discover, discovery, find, missing, gap, gaps, unknown, lead, leads, sms, email, cloud, statement, where, search, trace, unclaimed, coverage, start, assets
tools: get_discovery_overview, get_graph_insights
---
# Discovery playbook

Use this when the family is looking for assets they do not know about.

## Procedure
1. Call `get_discovery_overview` to see which sources were scanned, how many
   leads exist per category, which categories are gaps, and the current plan.
2. Answer from those facts first. Name the categories that are gaps and the
   single highest-yield next source:
   - unlocked phone: SMS export (bank, insurer and broker alerts)
   - email access: mailbox export (statements, premium reminders)
   - one bank statement: recurring debits reveal insurance, SIPs, loans, cards
   - nothing accessible: lawful account recovery plus official tracing
3. For official tracing services, read the jurisdiction file with
   `read_skill_resource` (`reference/india.md`, `reference/uk.md`,
   `reference/us.md`) instead of relying on memory.
4. Suggest searching other sources for institutions already found. That is
   how one lead turns into several.

## Rules
- Leads are possibilities, not proof of ownership. Say "possible".
- Never ask for or repeat seed phrases, passwords or full account numbers.

## Offline summary
Start with the highest-yield source you can lawfully access: an SMS export from
the phone, a mailbox export, or a single bank statement (every recurring debit
is a new lead). Confirm each institution in a second source, then promote real
leads into the asset register. Use the coverage map for official
unclaimed-asset searches in your jurisdiction.
