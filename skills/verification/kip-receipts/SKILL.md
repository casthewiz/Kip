---
name: kip-receipts
description: >
  Makes the agent prove what it says it did. Every statement of done-ness is a
  claim; every claim needs evidence, a rerunnable deterministic check whose
  observed output confirms or refutes it. Ends work with a claims ledger
  instead of a vibe. Evidence comes from cascading providers (frontend, api,
  generic, or a project's own), is recorded deterministically by receipt.py,
  and published to a destination confirmed each session (local, Linear,
  Jira, or any pluggable sink) as comments, images, and videos. Verifies
  anyone's work, not just this session's: a PR, a ticket's acceptance
  criteria, or a deployed site, including Playwright desk tests across
  mobile, tablet, and desktop viewports. Supports thoroughness levels: lite,
  full (default), ultra, which set how much effort goes into producing
  evidence. Use on ANY task that ends with the agent reporting something as
  done, fixed, working, passing, or verified, and before opening a PR. Also
  use whenever the user says "receipts", "show your work", "prove it",
  "verify that", "are you sure", "did you test it", "claims and evidence",
  "desk test", "QA this", "check it on mobile", or "test across viewports".
  Do NOT use for pure question-answering where nothing was changed or done.
argument-hint: "[lite|full|ultra]"
license: MIT
---

# Receipts

You don't get to say it's done. You get to show it's done. Anything you
believe you accomplished is a **claim** until **evidence** settles it.

## Persistence

ACTIVE EVERY RESPONSE that reports work. Off only: "stop receipts" / "normal
mode". Default: **full**. Switch: `/kip-receipts lite|full|ultra`.

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

## Whose work

Receipts verifies claims, not this session's diff. The work can come from
anyone: this session, another agent, a teammate's PR, a vendor's deploy.
Claims come from wherever the promise was made:

- This session's own work.
- A ticket's acceptance criteria, a PR description, or `kip-decompose` units.
- The user listing behaviors to desk-test ("check signup works on mobile").

Someone else saying "tested" or "works on my machine" is a claim, never
evidence. Verify it like any other.

Point receipts at what's being verified:

- **Code in a checkout** (any author): check out the branch or PR first.
  Evidence pins the commit and any uncommitted changes, as usual.
- **A remote site** (preview deploy, staging, prod): `init --target <URL>`.
  Its code isn't in this checkout, so evidence is marked remote and not
  pinned to a commit. The record says so instead of pinning the wrong code.
  The recorded commands and the specs in the run's `specs/` folder still
  make every check rerunnable.

Falsifiability doesn't require having made the change. Run the same check
with `--red` against a **baseline** that lacks the behavior: the base
branch, prod when the change is on staging, the previous deploy. With no
baseline available, an ultra claim stays ⚠️ "falsifiability not shown",
which is the honest result.

## Desk testing websites

To verify how a website behaves, whoever built it, use Playwright through
[`playwright/desktest.py`](playwright/desktest.py). It runs one spec against
a URL at mobile, tablet, and desktop viewports, each recorded as separate
evidence with screenshots (plus video and a trace at ultra). The
[frontend provider](providers/frontend.md) has the full procedure.

```bash
python3 $R init --target https://staging.example.com --level full
python3 $R claim "Signup shows a confirmation on every viewport" --provider frontend
cp ~/.claude/skills/kip-receipts/playwright/example.spec.ts "$(python3 $R path)/specs/c1.spec.ts"  # edit it
python3 ~/.claude/skills/kip-receipts/playwright/desktest.py c1 https://staging.example.com \
  "$(python3 $R path)/specs/c1.spec.ts"
```

Playwright lives in `~/.kip`, not in the project, so this works on any site.
If it isn't installed, ask before running `playwright/setup.sh`: it downloads
`@playwright/test` from npm and a Chromium build (about 150 MB) unless one is
already cached.

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

## Recording

Evidence is recorded by [`receipt.py`](receipt.py) in this skill's folder
(`~/.claude/skills/kip-receipts/receipt.py` or `~/.cursor/skills/kip-receipts/receipt.py`
once installed), never typed up by hand. The script runs each check itself,
so exit codes and output come from the run, not from your summary of it.

```bash
R=~/.claude/skills/kip-receipts/receipt.py               # or ~/.cursor/...
python3 $R sinks                                     # what's available and configured
python3 $R init --level full                         # local only (the default)
#  or: init --sink linear --issue ENG-123 --level full  # if the user opted into a sink
python3 $R claim "POST /invoices accepts null due" --provider api --level ultra
python3 $R run c1 --red -- npm test -- invoices      # ultra: on the old code, must fail
python3 $R run c1 -- npm test -- invoices            # on the new code, must pass
python3 $R run c2 --static -- npx tsc --noEmit       # static, not behavior
python3 $R observe c3 --passed "form shows 'No due date'"   # browser-tool checks only
python3 $R attach c3 shot.png                        # screenshots, videos, logs
python3 $R unverified c4 "needs the PDF service"
python3 $R render                                    # the ledger
python3 $R publish                                   # post to the run's sinks, if any
python3 $R publish --sink jira --issue PROJ-9        # opt a local run into a sink later
```

Each run is a folder under `~/.kip/receipts/<repo>/<run-id>/` (override with
`$KIP_RECEIPTS_DIR`): a `receipt.json` and a `files/` folder. What makes it
deterministic:

- **Pinned code.** Every piece of evidence records the commit it ran against,
  plus a patch of uncommitted changes, so anyone can recreate the exact tree
  and rerun the exact command.
- **Captured output.** Full output is saved as a log with its sha256; the
  ledger quotes the tail. Values of environment variables named like
  `*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*` are redacted before anything
  is written.
- **Computed verdicts.** You don't pick the verdict; `receipt.py` derives it
  from the recorded results and the claim's thoroughness level.
- **Tamper check.** `render` refuses to run if any recorded file's hash no
  longer matches.
- **Same record, same output.** Rendering is a pure function of
  `receipt.json`.

Prefer `run` over `observe`. An observation is your account of what you saw,
which is weaker; use it only when the check is an agent tool (a browser pane)
with no command equivalent, and attach the screenshot.

## Verdicts

`receipt.py` assigns every claim exactly one:

- ✅ **verified**: all evidence passed.
- ❌ **refuted**: a check on the new code failed. Fix it and re-run, or
  report it plainly. Never quietly drop a refuted claim.
- ❌ **check can't fail**: a `--red` run passed, so the check proves nothing.
  Write a check that would fail without the change.
- ⚠️ **unverified**: no evidence, or marked `unverified` with a reason (needs
  prod credentials, needs hardware, no test harness) and what would verify it.
- ⚠️ **falsifiability not shown**: the claim is at ultra and has no `--red` run.

An unverified claim is never reported as done. "Should work" is ⚠️.

## Thoroughness

Thoroughness sets how much effort goes into producing evidence, not how many
claims you make.

| Level | Evidence required per claim |
|---|---|
| **lite** | Cheapest check that touches the claim: typecheck, build, lint, grep. Behavior claims at lite are ⚠️ unless a check already existed and was run. |
| **full** | At least one **behavior** check that exercises the claim, with output quoted. If none exists, write the smallest one (kip-ponytail's "one runnable check"). Default. |
| **ultra** | Everything in full, plus: prove falsifiability (revert or break the change, watch the check go red, restore it); cover edge cases and every sibling caller of changed code; run it end to end in the real app or environment, not just unit tests. |

**Automatic floor.** Claims touching money, auth, security, permissions, data
deletion, migrations, or anything that risks data loss are held to **ultra**
regardless of the current level. Record them with `claim --level ultra` so
the verdict enforces it.

**Escalate, don't stall.** If evidence at the current level is ambiguous, go
up a level for that claim rather than calling it verified.

## Destination

**Evidence is local first.** Every run is stored locally, and that's the
default destination: `receipt.py init` with no `--sink` records locally and
publishes nowhere. Uploading to Linear, Jira, or another sink is opt-in.

**Every session that produces evidence confirms its destination before
recording anything.** Run `receipt.py sinks`, then ask the user (with the
question tool, if available):

- Where should this session's evidence go? **Local only** is the first,
  recommended option. Then local plus each configured sink (Linear, Jira, or
  a project sink). Leave out sinks whose environment variables are missing,
  and say which ones they are.
- For each cloud sink picked, which issue? Suggest the key from the branch
  name or recent commits, and let the user correct it.

A project's `.kip/receipts.md` may name a different preferred destination;
offer it as the recommended option instead, but still ask. `init` requires
`--issue` for any cloud sink.

The user can also opt in later: if a run was local only, offer once, after
the ledger, to upload it. On a yes, `receipt.py publish --sink <name>
--issue <KEY>` adds that sink to the run and publishes.

The user's choice authorizes publishing this run there, so `publish` needs
no second confirmation. It never authorizes any other sink or issue; a run
stays tied to one issue, so a different issue means a new run.

## Sinks

A sink publishes the record somewhere other than the local folder. `local` is
built in and always on; every other sink is one Python module with two
functions (`upload` a file, `publish` a comment) described in
[`sinks/CONTRACT.md`](sinks/CONTRACT.md). Adding a destination means adding
one module: Kip's live in [`sinks/`](sinks/), and a repo can add or override
its own in `<repo>/.kip/sinks/`.

| Sink | Publishes | Requires |
|---|---|---|
| local | `receipt.json` + files under `~/.kip/receipts/<repo>/<run-id>/` | nothing |
| [linear](sinks/linear.py) | Issue comment with the ledger; images embedded, videos and logs linked | `LINEAR_API_KEY` |
| [jira](sinks/jira.py) | Issue comment in Jira markup; files as attachments, images as thumbnails | `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` |

`receipt.py publish` is the same for every sink: upload each file once, post
`render` output verbatim (never a rewrite), and update the run's existing
comment instead of adding a second one. Preview what a sink will post with
`receipt.py render --sink <name>`. If a sink's variables aren't set but its
MCP server is connected, you may post `render --sink <name>` output through
the MCP server instead; say that files weren't uploaded.

## Output

End the response with `receipt.py render` output, pasted as is. Prose before
it stays short. Then one line: what's ⚠️ or ❌ and what the user should do
about it, plus the link `publish` printed for each sink, or that it was
kept local. If a sink was skipped or failed, say so; the local record is
still complete.

If `receipt.py` can't run (no Python 3), write the ledger by hand in the same
shape, and mark every claim ⚠️ that you can't back with quoted output.

## Boundaries

Receipts governs how you prove work, not what you build (that's kip-ponytail) or
how you tidy it (that's kip-marie-kondo). Evidence checks you write to verify a
claim follow kip-ponytail: smallest thing that can fail, no frameworks. Keep them
if they're useful regression checks; delete throwaway probes.

"stop receipts" / "normal mode": revert. Level persists until changed or
session end.

No receipt, no claim.
