---
name: receipts
description: >
  Makes the agent prove what it says it did. Every statement of done-ness is a
  claim; every claim needs evidence, a rerunnable deterministic check whose
  observed output confirms or refutes it. Ends work with a claims ledger
  instead of a vibe. Evidence comes from cascading providers (frontend, api,
  generic, or a project's own). Supports thoroughness levels: lite, full (default), ultra,
  which set how much effort goes into producing evidence. Use on ANY task that
  ends with the agent reporting something as done, fixed, working, passing, or
  verified, and before opening a PR. Also use whenever the user says
  "receipts", "show your work", "prove it", "verify that", "are you sure",
  "did you test it", or "claims and evidence". Do NOT use for pure
  question-answering where nothing was changed or done.
argument-hint: "[lite|full|ultra]"
license: MIT
---

# Receipts

You don't get to say it's done. You get to show it's done. Anything you
believe you accomplished is a **claim** until **evidence** settles it.

## Persistence

ACTIVE EVERY RESPONSE that reports work. Off only: "stop receipts" / "normal
mode". Default: **full**. Switch: `/receipts lite|full|ultra`.

## Claims

A claim is one falsifiable statement of done-ness, scoped tightly enough that
a check can prove it wrong.

- Good: "`parseDate` no longer throws on `null`, for all 3 callers."
- Bad: "Improved date handling." Vague claims can't be refuted, so they can't
  be verified. Split them until each one can.

Write claims down **before** gathering evidence, from what the task asked for,
not from what you happened to test. Testing first and then claiming whatever
passed is grading your own homework.

Every claim you make in prose ("fixed", "works", "passes", "no regressions")
must appear in the ledger. If it isn't in the ledger, don't say it.

## Evidence

Evidence is a deterministic, rerunnable check plus what you observed when you
ran it. It has three parts:

1. **The check.** An exact command, test, request, query, or grep someone
   else could paste and run. "I read the code and it looks right" is not
   evidence. It's a second claim.
2. **The observation.** The actual output: exit code, pass/fail counts, the
   response body, the matched lines. Quote it, trimmed to what matters. Never
   paraphrase a result you didn't see.
3. **Falsifiability.** The check must be capable of failing if the claim is
   false. A test that passes with or without your change proves nothing. A
   grep for a string you just typed proves only that you typed it.

Ranked from strongest to weakest:

| Strength | Kind | Example |
|---|---|---|
| Strongest | Behavior, shown to fail without the change | Test red on the old code, green on the new |
| Strong | Behavior | Test, script run, `curl` against the running app, DB query |
| Weak | Static | Typecheck, lint, build, compile |
| Weakest | Presence | File exists, grep matches, diff contains the line |

Static and presence checks are fine for claims about static things ("the
export was removed"). They are never sufficient for claims about behavior
("the bug is fixed").

## Providers

How evidence gets produced depends on what a claim touches. A **provider**
knows how to produce evidence for one surface. Providers live next to this
file in [`providers/`](providers/) and follow the contract in
[`providers/CONTRACT.md`](providers/CONTRACT.md).

| Provider | Handles |
|---|---|
| [frontend](providers/frontend.md) | UI renders, interactions, layout, accessibility |
| [api](providers/api.md) | HTTP endpoints: status, body, auth, validation |
| [generic](providers/generic.md) | Anything else: tests, scripts, commands. Always available. |

For each claim, cascade:

1. **Classify** the surfaces it touches: UI, API, data, CLI, library, infra.
2. **Pick the most specific provider** per surface, first match wins:
   1. **Project provider**: `.kip/receipts.md` in the repo being worked on,
      if present. It knows this repo's commands, ports, and fixtures, and
      overrides or extends Kip providers for the surfaces it declares.
   2. **Kip provider**: the matching file in `providers/`.
   3. **generic**.
   4. **None**: the claim is ⚠️, naming the missing provider.
3. **Compose** when a claim spans surfaces. "The signup form creates the
   user" needs frontend AND api. The claim is ✅ only if every required
   provider's evidence confirms it.
4. **Fall through** when a provider's requirements aren't met (no dev server,
   no browser tool, no running API). Drop to the next provider in the cascade
   and note the downgrade in the ledger. Downgraded evidence must still meet
   the thoroughness level, or the claim is ⚠️.

Read a provider's file before using it. Add a provider when a real claim needs
one; don't scaffold providers for surfaces nobody has claimed anything about.

## Verdicts

Every claim ends with exactly one:

- ✅ **verified**: evidence ran and confirmed it.
- ❌ **refuted**: evidence ran and contradicted it. Fix it and re-run, or
  report it plainly. Never quietly drop a refuted claim.
- ⚠️ **unverified**: no evidence could be produced. State why (needs prod
  credentials, needs hardware, no test harness) and what would verify it.

An unverified claim is never reported as done. "Should work" is ⚠️.

## Thoroughness

Thoroughness sets how much effort goes into producing evidence, not how many
claims you make.

| Level | Evidence required per claim |
|---|---|
| **lite** | Cheapest check that touches the claim: typecheck, build, lint, grep. Behavior claims at lite are ⚠️ unless a check already existed and was run. |
| **full** | At least one **behavior** check that exercises the claim, with output quoted. If none exists, write the smallest one (ponytail's "one runnable check"). Default. |
| **ultra** | Everything in full, plus: prove falsifiability (revert or break the change, watch the check go red, restore it); cover edge cases and every sibling caller of changed code; run it end to end in the real app or environment, not just unit tests. |

**Automatic floor.** Claims touching money, auth, security, permissions, data
deletion, migrations, or anything that risks data loss are held to **ultra**
regardless of the current level. Say so in the ledger.

**Escalate, don't stall.** If evidence at the current level is ambiguous, go
up a level for that claim rather than calling it verified.

## Output

End the response with the ledger. Prose before it stays short.

```
| # | Claim | Provider | Evidence | Result | Verdict |
|---|-------|----------|----------|--------|---------|
| 1 | `parseDate(null)` returns null instead of throwing | generic | `npm test -- parseDate` (red on main, green on branch) | 4 passed, 0 failed | ✅ |
| 2 | `POST /invoices` accepts an empty due date | api | `curl -s -XPOST :3000/invoices -d '{"due":null}'` | `201`, body has `"due":null` | ✅ |
| 3 | Invoice form shows "No due date" when blank | frontend → generic (no browser tool) | `npm test -- InvoiceForm` | 2 passed | ✅ (downgraded) |
| 4 | Invoice PDF renders with empty date | none | needs the PDF service running locally | — | ⚠️ run `make pdf-dev` then `/invoices/123.pdf` |

Thoroughness: full (claim 2 escalated to ultra: touches billing)
```

Then one line: what's ⚠️ or ❌ and what the user should do about it. If
everything is ✅, say nothing more.

## Boundaries

Receipts governs how you prove work, not what you build (that's ponytail) or
how you tidy it (that's marie-kondo). Evidence checks you write to verify a
claim follow ponytail: smallest thing that can fail, no frameworks. Keep them
if they're useful regression checks; delete throwaway probes.

"stop receipts" / "normal mode": revert. Level persists until changed or
session end.

No receipt, no claim.
