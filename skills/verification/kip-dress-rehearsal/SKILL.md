---
name: kip-dress-rehearsal
description: >
  Writes a test plan for a change that reaches something real (a live
  service, a partner API, production data, devices, customers, a release
  channel), framed as a staged rollout: each stage widens exposure one step
  and names the outcome that must be observed before the next. Picks what
  each stage runs against from the real system, read-only, and marks
  anything someone else owns as proposed until they approve it, with
  collapsible lists of other criteria to pick by. Supports levels: lite
  (stages and outcomes, no lookups), full (default: subjects found and
  checked), ultra (also each subject's history and a natural trigger for
  every failure path). Use when the user says "test plan", "rollout plan",
  "how do we test this in prod", "find test subjects", "dress rehearsal", or
  "staged rollout", and before turning on a path that reaches a real system.
  Do NOT use for unit or browser tests of the change itself (write those) or
  to prove work is done (kip-receipts).
argument-hint: "[lite|full|ultra] [ticket | spec | PR]"
license: MIT
---

# Dress rehearsal

A change that reaches something real gets tried where a mistake is cheap,
then a little less cheap, one ring at a time. This skill writes that plan. It
does not run it.

## Persistence

Runs once per change. Default: **full**. Switch:
`/kip-dress-rehearsal lite|full|ultra`.

## Levels

| Level | Subjects | Failure paths | Checks |
|---|---|---|---|
| **lite** | Describe what each stage runs against. No lookups. | List the cases. | None. |
| **full** | Find real subjects in the system, read-only, and check each one exists and is what you think. | Name a subject for each failure case if one turns up. | Every subject on the page traces to a lookup in the appendix. Default. |
| **ultra** | Full, plus each subject's recent history and anything that depends on it. | Find a natural trigger for every failure path, or say why there is none. | Full, plus the plan is re-read after it is saved. |

## Rules

- **Read-only.** Planning never changes the real system. Look, don't touch.
- **What others own is proposed, never chosen.** A subject someone else is
  answerable for (a customer, another team, a user's device) sits under a
  "Proposed, not approved" callout that names who approves it.
- **Every subject is checkable.** Say how to find it again: an ID, a name and
  where it lives, a query, a cohort definition, a percentage. Put the lookup
  in the appendix. Note anything easy to confuse with it.
- **Name the clock.** If something only holds for a while (a time window, a
  token lifetime, a release train), state the dates and when it moves.
- **Quote constraints exactly.** If someone limited what the change may
  touch, copy their words and their source.

## 1. Frame

Read the ticket, the spec, and the code. Write down:

- The path: what starts it, what carries it, what it changes, and what
  reports back.
- Every outcome the path can report or leave behind, success and each
  failure.
- The controls: flags, config, kill switches, and who owns each.
- How an effect is undone, and whether anything records what happened.

## 2. Stages

A stage is one step wider in exposure, run until its outcomes are observed.
Derive the stages from the change, not from a template:

1. List the ways exposure can widen: environment (local, sandbox,
   production), audience (you, your team, internal users, customers), share
   (one subject, a handful, a percentage, everyone), time (one run, a day, a
   billing cycle), and reversibility (undoable, then not).
2. Start where a mistake costs nothing. Widen one dimension per stage, so a
   failure points to what changed.
3. Stop adding stages when the next step is the full rollout.

For each stage, write the outcomes, not the activity:

| Field | Answers |
|---|---|
| Proves | What a pass here means that the stage before could not show. |
| Exposed | Who or what feels a mistake, and how many. |
| Runs against | The subjects (section 3). |
| Controls | The switch positions this stage needs. |
| Pass signal | What you observe, where, and the threshold. Measurable, not "looks fine". |
| Watch | Who watches, and for how long or how many runs. |
| Stop signal | What makes you halt and roll back right away. |
| Rollback | The exact steps, and how you confirm they worked. |
| Go/no-go | The person who decides to widen. Say so if you assumed one. |

A stage whose pass signal you can't observe is not a stage. Fix the signal
or merge it into the next. Say which failure cases need the safest stage,
because a wider one can't safely trigger them.

## 3. Subjects

A subject is whatever a stage runs against: a record, an account, a device, a
build channel, a slice of traffic, a file, a region. Find each one in the real
system, read-only, and put the lookup in the appendix.

- **Safe subjects** (the earliest stages): look for what already exists for
  testing (names like `test`, `demo`, `sandbox`, internal owners, staging
  channels). Check that nothing real depends on them, and flag any that
  something does.
- **Real subjects** (the middle stages): pick by criteria that keep the blast
  radius small and the signal clear, for example quiet history, the simplest
  shape the change expects, and nothing downstream. Put them under the
  proposed callout. Below them, add a collapsed toggle, "Other criteria we
  could use", with alternatives such as owner volunteers, least at stake,
  only future effects, or one group.
- **Failure-path subjects**: for each failure the path can report, look for a
  subject that triggers it naturally. These are proposed too, and the plan
  says what should *not* happen to them. Add the same toggle, including
  "build the case in the safest stage instead".

For each subject, say how to find it and what the change will touch.

## 4. Cases

One table: an ID, a case, the expected outcome, and a column per stage saying
where it runs. Cover the happy path, bad input, every failure the path can
report, repeats and races, and rollback. Each case becomes a kip-receipts
claim when the plan runs.

## 5. Cleanup

What to undo after each stage, how to confirm it was undone, and who does it.

## Output

Write the plan where the team keeps specs (Notion, Confluence, a docs repo)
and link it from the spec. If there is no such place, write a markdown file
beside the spec. The layout:

```
<At a glance: the change, the stages, the decision needed and from whom>
1. Preconditions
2. Stages        (outcomes per stage, then subjects, proposed callouts, criteria toggles)
3. Cases         (table with a column per stage)
4. Cleanup
Appendix         (collapsed: every lookup that found a subject)
```

Then give the user the link, say what is unverified (unreached environments,
assumed owners, unset thresholds), and offer to ask the approvers.

## Boundaries

Dress rehearsal plans trying the change on the real thing. kip-decompose
plans the build, kip-objection reviews the code, and kip-receipts proves each
case when the plan runs.

Rehearse on the real stage before the audience arrives.
