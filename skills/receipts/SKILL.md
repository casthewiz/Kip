---
name: receipts
description: >
  Makes the agent prove what it says it did. Every statement of done-ness is a
  claim; every claim needs evidence, a rerunnable deterministic check whose
  observed output confirms or refutes it. Ends work with a claims ledger
  instead of a vibe. Supports thoroughness levels: lite, full (default), ultra,
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
| # | Claim | Evidence | Result | Verdict |
|---|-------|----------|--------|---------|
| 1 | `parseDate(null)` returns null instead of throwing | `npm test -- parseDate` (red on main, green on branch) | 4 passed, 0 failed | ✅ |
| 2 | All 3 callers handle the null return | `rg "parseDate\(" src/` + `npm test -- invoices orders` | 3 call sites; 12 passed | ✅ |
| 3 | Invoice PDF renders with empty date | needs the PDF service running locally | — | ⚠️ run `make pdf-dev` then `/invoices/123.pdf` |

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
