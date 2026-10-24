# Receipts

[`kip-receipts`](../skills/verification/kip-receipts/SKILL.md) turns every
statement of done-ness into a claim, and every claim needs evidence: a
rerunnable, deterministic check whose observed output confirms or refutes it.

## The gate

`install` adds the receipts
[gate](../skills/verification/kip-receipts/SKILL.md#gate) to each host's hooks
(Claude Code's `settings.json`, Cursor's `hooks.json`), leaving your own
hooks alone: once code changes, the agent can't end a turn with unaddressed
claims, evidence the code has since outgrown, or failing
[required checks](../skills/verification/kip-receipts/SKILL.md#required-checks),
or without a receipts run, or open a PR with refuted claims or without its
tidy and review [stages](../skills/verification/kip-receipts/SKILL.md#stages).
Edits wait for `kip-decompose`, with one nudge toward `kip-messy-bun`, and a
turn that changed code can't end before `kip-marie-kondo`
([`workflow.skills`](configuration.md)); Cursor has no hook before an edit, so
there only the stop holds it.
Each session starts knowing where the branch's run stands; a run whose work has
landed on the base branch is finished and drops out, and one set aside with
`receipt.py orphan` (or `sweep`, for branches merged or deleted elsewhere) does
too, leaving its branch to start a fresh run. Cursor can't block a stop,
so there the gate asks once, as a follow-up message.

Stages and required checks are set under `workflow` in
[configuration](configuration.md).

## Worktrees

Kip expects work to happen in linked worktrees, one per task or parallel
unit: `kip.py worktree new NAME` makes one at `.kip/worktrees/NAME` on
branch `kip/NAME`, copies the gitignored files `.worktreeinclude` names, and
runs `workflow.worktree.setup` there; the host's own worktrees work the same.
Every run records the worktree it started in, and evidence is judged only
where its code is checked out. A [plan](../skills/analysis/kip-decompose/SKILL.md#recording-the-plan)
hands each unit's claims to the unit's run (`--parent RUN --unit N`) and
shows their verdicts. The gate steers edits off a main checkout
(`workflow.worktrees`), holds a worktree-isolated subagent's stop to the same
rules as a session's in its worktree, and flags evidence against a dev server
running another checkout's code. A host's worktrees made inside the checkout
(`.claude/worktrees/`) never count as that checkout's changes.

## Storage

`kip-receipts` records evidence with `python3` (standard library only) in
`~/.kip/receipts/kip.db`, a SQLite database that `install` creates (importing
any runs from the JSON files that came before it), `doctor` reports on, and
`uninstall` leaves in place. Local is the default; each session asks whether to also
publish to another [destination](configuration.md), and a local run can be published later.
Script-backed destinations read their settings from config or the
environment (secrets only ever from the environment):

- Linear: `LINEAR_API_KEY`
- Jira: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
- GitHub: the `gh` CLI, logged in; the ledger goes in the PR body (`github`) or an
  issue comment (`github-issue`)

## CI

To hold every PR in a repo to its receipts, whoever opened it, add Kip's
action. It fails the PR while the ledger the `github` sink published is
missing, was published for an older commit than the PR's head, or has
claims that aren't done. Republish after each push.

```yaml
on:
  pull_request:
    types: [opened, edited, synchronize, reopened]
jobs:
  receipts:
    runs-on: ubuntu-latest
    steps:
      - uses: casthewiz/Kip@main
```
