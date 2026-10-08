---
name: kip-decompose
description: >
  Understands a problem statement before anything is built, and helps the
  prompter understand it too, then breaks it into units of work. Each unit
  has a goal, dependencies, whether it can run in parallel, and the claims
  that will mean it's done (verified later by kip-receipts). Supports effort
  levels: lite (shallow, efficient chunking and sequencing), full (default),
  ultra (deep: the underlying problem traced through the real code and data,
  assumptions and open questions surfaced, units grounded in what was found).
  Use before implementing anything non-trivial, multi-step, or ambiguous, and
  whenever the user says "decompose", "break this down", "plan this", "scope
  this", "what would this take", "units of work", "what's the real problem",
  or "help me understand this". Do NOT use for one-line changes or questions
  that need an answer, not a plan.
argument-hint: "[lite|full|ultra]"
license: MIT
---

# Decompose

Understand the problem, then cut it into units of work. Don't write
implementation code here; the output is understanding and a plan someone
(you, another agent, the user) can execute.

## Persistence

Runs once per problem statement, then hands off. Default: **full**. Switch:
`/kip-decompose lite|full|ultra`.

## Effort

| Level | Understanding | Decomposition |
|---|---|---|
| **lite** | Take the request roughly as stated. Skim only enough code to name where the work lands. | Focus on efficient chunking: the fewest units that cover it, clearly sequenced or marked parallel. |
| **full** | Restate the problem in one or two lines. Read the code the request touches and trace the main flow through it. | Units grounded in the files and functions they touch, with dependencies and claims. Default. |
| **ultra** | Dig into the underlying problem, not just the request: why it's being asked, what behavior actually has to change, who or what depends on it. Trace it through every layer the model can see (code, schema, data, config, docs, history). Surface assumptions and open questions. | Units grounded in what was found, including the ones the request didn't name but the problem needs (migrations, callers, edge cases). Note risks per unit. |

At any level, if the understanding step finds the request rests on a wrong
premise (the bug isn't where they think, the feature already exists), stop
and say so before decomposing.

## Understanding

Separate three things; requests usually blur them:

1. **What was asked**: the request in the prompter's words.
2. **What the problem is**: the behavior or functionality that actually has
   to exist or change. At full and ultra, say this plainly so the prompter
   can confirm it.
3. **What exists today**: what the code, data, or system currently does
   here, from what you read, not what you assume. At lite, this can be one
   line or skipped.

Open questions only block the plan when the answer changes the units. Ask
those (with the question tool, if available). Otherwise state the assumption
and continue.

## Units of work

A unit is the smallest piece of work that delivers something verifiable on
its own. Each one has:

- **Goal**: one line, the behavior it adds or changes.
- **Depends on**: units that must land first, or `—`.
- **Parallel**: whether it can run alongside other units (`yes` if it has no
  unmet dependencies and touches different files).
- **Claims**: the falsifiable statements that will mean it's done. These are
  `kip-receipts` claims, written now, verified later. Name the provider surface
  when it's obvious (`api`, `frontend`).
- **Touches**: files, functions, tables, or services, at full and ultra.

Cutting rules:

- Apply kip-ponytail's first rung to every unit: does it need to exist? Flag
  speculative units and leave them out.
- A unit too big to state in one goal line gets split. A unit too small to
  verify on its own gets merged into its neighbor.
- Order by dependency, then by risk: the unit most likely to invalidate the
  plan goes first.

## Output

```
## Problem
<what was asked> → <what the problem actually is>   (full, ultra)
<what exists today>                                  (full, ultra)
Assumptions: … · Open questions: …                  (ultra; full if any)

## Units
| # | Goal | Depends on | Parallel | Claims | Touches |
|---|------|------------|----------|--------|---------|
| 1 | Accept `null` due dates in `POST /invoices` | — | yes | api: `{"due":null}` → 201 and stored as null | `invoices/handler.ts`, `invoices` table |
| 2 | Show "No due date" on the invoice form | — | yes | frontend: blank date renders "No due date" | `InvoiceForm.tsx` |
| 3 | Render empty dates in invoice PDFs | 1 | no | generic: PDF snapshot test passes for null date | `pdf/invoice.ts` |

Skipped: <speculative units left out, and when they'd be needed>
Effort: full
```

Lite drops the Problem section to one line and the Touches column. Then one
line on where to start: the first unit, or every parallel unit at once.

## Recording the plan

The claims column is the task's record, not just a table. Write it into a
local `kip-receipts` run so verification picks up the same claims, tagged
with their unit, instead of retyping them:

```bash
R=~/.kip/skills/kip-receipts/receipt.py
python3 $R init --level full                     # the plan's effort; local only, no destination yet
python3 $R claim "POST /invoices accepts null due" --unit 1 --provider api
python3 $R claim "Blank date renders 'No due date'" --unit 2 --provider frontend
```

One `claim` per claim in the table, at the ultra floor (`--level ultra`)
where kip-receipts requires it. The run records the code as it is now; once
work lands, the host's hooks won't let the session end while any of these
claims is unaddressed. If the user changes the plan, start a new run.

## Boundaries

Decompose governs understanding and planning. kip-ponytail governs how each unit
gets built, kip-marie-kondo tidies it, and kip-receipts verifies the claims written
here, in the run it records. Don't start implementing inside this skill
unless the user asks to go straight on.

Understand it before you cut it.
