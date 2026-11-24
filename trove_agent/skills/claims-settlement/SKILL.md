---
name: claims-settlement
description: Explain the post-death case workflow stages, what blocks advancing, and how to organise institution claims.
triggers: workflow, stage, advance, claim, claims, settlement, institution, insurer, transfer, next, review, asset, register
tools: get_workflow_summary
---
# Claims and settlement

## Procedure
1. Call `get_workflow_summary` (stage, death review status, asset counts by
   status, unreviewed beneficiaries, claim tracker counts).
2. Explain what blocks the next stage: death certificate review, unreviewed
   asset candidates, unreviewed beneficiary records.
3. For each confirmed asset, suggest a claim tracker entry: contact the
   institution's bereavement team with a certified death certificate, the
   claimant's ID, and any nominee or succession documents the institution asks for.

## Rules
- Trove does not submit claims or move money. Say so if asked.
- For legal or tax questions, recommend a qualified professional.

## Offline summary
Advance stages by reviewing the death certificate, confirming or rejecting every
asset candidate, and reviewing beneficiary records. Then create a claim tracker
per institution and update it as each bereavement team responds.
