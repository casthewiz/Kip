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
Each session starts knowing where the branch's run stands; a run whose work has
landed on the base branch is finished and drops out, and one set aside with
`receipt.py orphan` (or `sweep`, for branches merged or deleted elsewhere) does
too, leaving its branch to start a fresh run. Cursor can't block a stop,
so there the gate asks once, as a follow-up message.

Stages and required checks are set under `workflow` in
[configuration](configuration.md).

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
