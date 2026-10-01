# Provider contract

Every provider, Kip's or a project's `.kip/receipts.md`, is a markdown file
with these sections. Keep it short; a provider is a procedure, not an essay.

```markdown
# <name> provider

## Handles
Which claim surfaces this provider covers, in one or two lines.

## Requires
How to detect it can run here, checked in order. Each line is something the
agent can verify (a file exists, a command succeeds, a tool is available).
If any is unmet, fall through to: <next provider, usually generic>.

## lite
Cheapest evidence for a claim on this surface.

## full
Behavior evidence. Must exercise the claim, not just its presence.

## ultra
Everything in full, plus how to prove falsifiability on this surface, the
edge cases that matter here, and what "end to end" means here.

## Observation
Exactly what to record in the ledger's Result column (status codes,
assertion counts, screenshot paths, log excerpts). Never a paraphrase.
```

## Project providers

A project's `.kip/receipts.md` uses the same sections, one block per surface
it covers, headed `# <surface> provider`. It only needs to fill in what's
specific to that repo (commands, ports, seed users, base URLs) and may say
"otherwise as Kip's <surface> provider" to inherit the rest.
