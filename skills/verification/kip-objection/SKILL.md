---
name: kip-objection
description: >
  Adversarial code review that runs the same way on every host and model.
  Goal: find what breaks, not confirm it works. Raises objections along fixed
  attack axes, rules each one sustained only when a throwaway probe proves
  it, ranks by a fixed severity scale, triages with the user, then posts one
  review with inline comments. Supports effort levels: lite (read-only, diff
  and PR context), full (default: trace dependencies, probe High and Medium),
  ultra (probe every objection, attack callers and neighbors the diff didn't
  touch). Use for Kip's review stage, before opening a PR, and whenever the
  user says "objection", "adversarial review", "review this PR", "tear this
  apart", "what breaks", "red team this", or "poke holes in this". Do NOT use
  to tidy style (kip-marie-kondo) or to prove the work is done (kip-receipts).
argument-hint: "[lite|full|ultra] [PR URL | number | branch]"
license: MIT
---

# Objection

Find what breaks, not what works. Every model reviews differently; this file
is the review. Follow its steps in order, on its axes, with its severity
scale and output, whatever host you're in. Don't swap in the host's own
review tool. Subagents may split the axes, but each follows this file.

## Persistence

Runs once per review target. Default: **full**. Switch:
`/kip-objection lite|full|ultra`.

## Effort

| Level | Trace | Prove | Scope |
|---|---|---|---|
| **lite** | Diff and PR context only. | No probes; every objection stays unproven. | The diff. |
| **full** | Every dependency the diff leans on (step 3). | Probe every High and Medium. | The diff and what it calls. Default. |
| **ultra** | Full, plus callers of everything the diff changed. | Probe every objection. | Also neighbors the diff didn't touch but now break. |

## 1. Gather

The target is a PR (URL or number), a branch, or, with none given, the
current branch against its base.

- PR: `gh pr view <n> --json title,body,files,comments,reviews,headRefOid,baseRefName`
  and `gh pr diff <n>`.
- Branch: `git diff "$(git merge-base origin/<base> HEAD)"`.
- At full and ultra, check out the head in a scratch worktree so probes never
  touch the user's checkout:
  `git worktree add "${TMPDIR:-/tmp}/objection-<n>" <head sha> --detach`.

## 2. Frame the stakes

Two or three lines, before reading for bugs:

- What was asked vs. what the change actually controls (money, auth, data,
  external systems, other teams' contracts).
- The three questions that matter here: **who can do this, what can they
  send, can our state ever disagree with reality?**

## 3. Trace what the diff leans on

Don't trust names. Open the definition of everything the change relies on:

- **Auth**: route access mode, required roles, how roles are granted, which
  guards apply by default (API keys, service accounts).
- **Data**: column types (timestamps, time zones, precision), defaults,
  foreign keys and cascades, validation hooks, migrations.
- **Side effects**: queues, publishers, webhooks, emails, and what they do
  when disabled, failing, or retried; registries and auto-wiring; seed or
  export lists.
- **Reused helpers**: a "same check as X" claim is confirmed by diffing
  against X, not by the name.

## 4. Attack

Raise an objection for each way the change can break, along every axis. An
axis with nothing to object to is listed under "what holds up", not skipped
silently.

| Axis | Attack |
|---|---|
| **Authorization** | The least-privileged caller who still passes. Open policy questions fail closed. |
| **Trust boundary** | What junk the input accepts: unknown keys, wrong types, negative, zero, huge, empty, past or absurd dates, unicode. |
| **Consistency** | Order of commit vs. side effect; what a failure midway leaves behind; a crash between steps; retries and double submits; concurrent writers. |
| **Contracts** | Schemas, events, or APIs that freeze on merge; vocabulary mismatches between producer and consumer; breaking changes for existing callers. |
| **Clocks** | DB vs. app time, timestamps without a zone, precision ties, DST, ordering by time. |
| **Lifecycle** | Cascades deleting history, soft vs. hard delete, data copied to other environments, rollback of the migration. |
| **Failure paths** | Errors swallowed or misreported, missing timeouts, unbounded loops or queries, resource leaks. |

Each objection names: the axis, the file and line at the head commit, the
concrete input or sequence that breaks it, and the impact.

**Severity** is fixed, not felt:

- **High**: data loss or corruption, auth bypass, money moved wrongly,
  security exposure, or a crash on a normal path.
- **Medium**: a wrong result or stuck state a real user can reach on an edge
  path, or a failure that leaves state inconsistent but recoverable.
- **Low**: an unlikely edge case, or a trap that will cause the next bug.

Style, naming, and taste are not objections.

## 5. Rule

Each objection gets a ruling:

- **Sustained**: a probe proves it. Write a throwaway test or script in the
  scratch worktree that **passes only if the bug is real** (asserts the bad
  behavior). Pure checks (validators, role checks) need no services; use the
  real DB or app only if it's already up.
- **Unproven**: reasoned from reading, not probed (lite, or a probe needs
  infrastructure that isn't here; say which).
- **Overruled**: the trace or a probe shows it can't happen. Drop it from the
  findings; it goes under "what holds up".

Delete the probes and the worktree afterwards
(`git worktree remove --force "${TMPDIR:-/tmp}/objection-<n>"`). Keep a probe's
command and output for the report.

## 6. Ruling

```
## Ruling: <block | fix first | ship>
<the stakes, 2–3 lines>

| # | Sev | Ruling | Axis | Where | Objection | Repro / impact | Suggested fix |
|---|-----|--------|------|-------|-----------|----------------|---------------|
| 1 | High | sustained | Trust boundary | `dto.ts:42` | `amount` accepts negatives | `{"amount":-5}` → refund issued; probe `c1.spec.ts` passes | `@Min(0.01)` |

What holds up: <axes and checks that survived attack, one line each>
Effort: full
```

**block** if any High is sustained, **fix first** for any High unproven or
Medium sustained, **ship** otherwise. At full and ultra, add a units-of-work
table for the fixes in kip-decompose's shape (goal, depends on, parallel,
claim, touches).

## 7. Triage

Before anything leaves the machine, the user marks each objection: **fix in
this PR**, **callout**, **new ticket**, or **intentional** (with the question
tool, if available). Never post without this.

Reviewing this session's own branch for Kip's review stage, there's nothing
to post: fix every sustained High and Medium without asking, and triage only
the rest. Each fix is recorded as its own
`--stage review` claim ("Fixed: …") with the probe, now failing, as
evidence. With no objection to fix, record
`claim "Review: no sustained objections" --stage review` with the ruling as
evidence.

## 8. Post

One review per PR, only after triage:

- Line numbers at the head SHA: `git show <sha>:<path> | nl -ba`.
- Post once: `gh api -X POST repos/<owner>/<repo>/pulls/<n>/reviews --input review.json`,
  with `commit_id`, `body`, `event`, and `comments` (each `path`, `line`,
  optional `start_line`, `side: "RIGHT"`).
- `event: "COMMENT"` on the user's own PR (GitHub refuses the rest),
  otherwise `REQUEST_CHANGES` if anything blocks, else `COMMENT`. The body
  leads with the ruling and names the blockers.
- Each comment: the problem, the repro or impact, the suggested fix. Skip
  objections marked intentional.
- Confirm they landed on the intended lines:
  `gh api repos/<owner>/<repo>/pulls/<n>/comments --jq '.[] | [.path, .line, .body[:60]]'`.

## Boundaries

kip-objection finds what breaks. kip-marie-kondo tidies, kip-receipts proves
what was claimed, kip-decompose plans the fixes. Don't fix anything inside
this skill unless the user triages it to "fix in this PR".

An objection is proven or labeled unproven. Never a vibe.
