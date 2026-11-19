---
name: kip-dress-rehearsal
description: >
  Writes a test plan for a change that touches a live system (a CRM, a billing
  platform, a partner API, production data), framed as a staged rollout:
  sandbox, then dev test records in production, then a few proposed real
  records someone watches, then a wider internal group. Finds candidate
  records by querying the live system read-only, checks every ID in both
  systems, and marks every real record as proposed until its owner approves
  it, with collapsible lists of other criteria to pick by. Supports levels:
  lite (stages and cases, no lookups), full (default: candidates queried and
  verified), ultra (also history checks and a natural example for every
  failure path). Use when the user says "test plan", "rollout plan", "how do
  we test this in prod", "find test properties/accounts/records", "dress
  rehearsal", or "staged rollout", and before turning on a write path to an
  external system. Do NOT use for unit or browser tests of the change itself
  (write those) or to prove work is done (kip-receipts).
argument-hint: "[lite|full|ultra] [ticket | spec | PR]"
license: MIT
---

# Dress rehearsal

A change that writes to a system you don't own gets tested on that system
before customers see it, one ring at a time. This skill writes that plan. It
does not run it.

## Persistence

Runs once per change. Default: **full**. Switch:
`/kip-dress-rehearsal lite|full|ultra`.

## Levels

| Level | Candidates | Failure paths | Checks |
|---|---|---|---|
| **lite** | Name the kind of record each stage needs. No lookups. | List the cases. | None. |
| **full** | Query the live system for real candidates. Check each ID in both systems. | Name a candidate for each failure case if one turns up. | Every ID on the page came from a query you ran. Default. |
| **ultra** | Full, plus each candidate's change history and anything billed against it. | Find a natural example of every failure reason, or say why there is none. | Full, plus the plan is re-read after it is saved. |

## Rules

- **Read-only.** Planning never writes to the live system. Queries only.
- **Real records are proposed, never chosen.** Every record a customer owns
  sits under a "Proposed, not approved" callout that names who approves it.
  You find candidates; people with the relationship pick them.
- **Every ID is checked.** An ID on the page came from a query in the
  appendix, and it resolves in both systems (yours and theirs). Note where
  names repeat and which ID is the right one.
- **Name the clock.** If a value only exists for a while (an accepted month,
  a token lifetime, a promo window), state the dates and when it moves.
- **Quote constraints exactly.** If someone limited where writes may go, copy
  their words and their source.

## 1. Frame

Read the ticket, the spec, and the code that writes. Write down:

- The loop: what sends, what carries it, what writes, and what reports back.
- Every outcome the loop can report, success and each failure reason.
- The switches: feature flags, config, kill switches, and who owns each.
- How a write is undone, and whether the external system keeps history.

## 2. Stages

Stages are rollout rings. Each widens exposure only after the one before it
passes. Adjust the count, but keep the order.

| | 0 Sandbox | 1 Dev test records | 2 Proposed real records | 3 Wider internal group |
|---|---|---|---|---|
| Proves | Every path, including failures | The happy path in production | It holds on records someone watches | It holds under real use |
| Who is exposed | Nobody | Internal test records | A few proposed records | Internal users' own work |
| Switches | | | | |
| Writes | | | | |
| Rollback | | | | |
| Go/no-go | | | | |
| Exit criteria | | | | |

Fill every cell. Name a person for each go/no-go, and say so when you assumed
one. Say which cases need stage 0, because production can't safely stage
them.

## 3. Candidates

Find them with the system's own CLI or API, read-only. Put each query in the
appendix.

- **Sandbox (stage 0).** List the records it needs. Mark IDs unchecked if you
  can't reach the sandbox.
- **Dev test records (stage 1).** Search by name (`test`, `demo`, `sandbox`),
  by an internal owner, and by any constraint someone gave. Check that
  nothing is billed against them, and flag any that are.
- **Real records (stage 2).** Pick by criteria that keep risk small, for
  example a quiet history, exactly one row where the code expects one, and
  no cascading effects. Put them under the proposed callout. Below the table,
  add a collapsed toggle, "Other criteria we could use", with alternatives
  such as only future periods, owner volunteers, the lowest revenue at risk,
  or one parent account.
- **Failure paths.** For each failure reason, look for a record that hits it
  naturally (duplicate rows, a missing row). These are proposed too, and the
  plan says what should *not* happen to them. Add the same collapsed toggle,
  including "build the case in the sandbox instead".

For each candidate, give its ID in both systems and the rows the change will
touch.

## 4. Cases

One table: an ID, a case, the expected outcome, and a column per stage saying
where it runs. Cover the happy path, input validation, every failure reason,
replays and races, and rollback. Each case becomes a kip-receipts claim when
the plan runs.

## 5. Cleanup

What to undo after each stage, how to check it was undone, and who does it.

## Output

Write the plan where the team keeps specs (Notion, Confluence, a docs repo)
and link it from the spec. If there is no such place, write a markdown file
beside the spec. The layout:

```
<At a glance: the change, the stages, the decision needed and from whom>
1. Preconditions
2. Stages        (table, then candidates per stage, proposed callouts, criteria toggles)
3. Cases         (table with a column per stage)
4. Cleanup
Appendix         (collapsed: every query that produced an ID)
```

Then give the user the link, say what is unverified (sandbox IDs, assumed
owners, unset limits), and offer to ask the approvers.

## Boundaries

Dress rehearsal plans testing in the live system. kip-decompose plans the
build, kip-objection reviews the code, and kip-receipts proves each case when
the plan runs.

Rehearse on the real stage before the audience arrives.
