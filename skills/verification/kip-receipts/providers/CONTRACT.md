# Provider contract

Every provider, Kip's or a project's, is a markdown file with these
sections, registered as an `evidence` surface in Kip's config. Keep it short; a provider is a procedure, not an essay.

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

## Files
What to capture and attach with `receipt.py attach` (screenshots, videos,
response bodies), at which level. Command output is captured automatically
by `receipt.py run`; list only what it doesn't cover.
```

## Project providers

A project registers its own in `.kip/config.json`, with `doc` relative to
that file:

```json
{"evidence": {"api": {"doc": "api.md", "requires": {"cmd": ["docker"]}, "fallback": "generic"}}}
```

Using an existing name (`api`) overrides Kip's entry; a new name adds a
surface. The doc only needs what's specific to that repo (commands, ports,
seed users, base URLs) and may say "otherwise as Kip's <surface> provider"
to inherit the rest. `requires` is what `kip.py doctor` checks (`env`,
`cmd`, `file`); `via` a connector marks a provider that runs through the
agent's own tools.
