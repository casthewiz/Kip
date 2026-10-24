# Dashboard

## Session log

The hooks keep a session log, one file per day in `~/.kip/receipts/events/`
(the dashboard keeps 14 days): every gate decision and every tool call,
redacted and cut short. Neither host has a hook for skills, so a skill counts
as used when a prompt names it (`/name`) or attaches it, or a tool reads its
`SKILL.md`. Each prompt and the agent's reply are kept too (redacted, up to
20,000 characters), so a session's timeline reads as the conversation;
`install --no-conversation` turns that off (saved as `dashboard.conversation`
in `~/.kip/config.json`, or the repo's with `--project`), and `--conversation`
turns it back on.

## Running it

A user `install` runs a live dashboard at <http://127.0.0.1:7377> as a login
service (a launchd agent on macOS, a systemd user unit on Linux) that's always
up; it runs from a copy at `~/.kip/dashboard`, since macOS stalls background
agents that read `~/Documents` and the like; the [git hooks](install.md#what-install-does) refresh it
whenever Kip changes. Elsewhere, a session's start launches it and it exits after 30 idle
minutes. `KIP_DASHBOARD=0` never starts it; `KIP_DASHBOARD_PORT` moves the one
a session starts. Run it yourself with `receipt.py serve`; one a Claude Code
session starts that way is stopped when that session ends. To preview
dashboard changes from a checkout, start the `dashboard` configuration in
`.claude/launch.json` (port 7388), which the app stops for you.

## What it shows

It lists sessions with the stage each is in (decompose, implement, tidy, review,
receipts), how many stops in a row the gate blocked, and its run's open
reasons, and streams each session's timeline as it happens. Each timeline row but
prompts and blocked ones starts folded to a short gist (`Bash git push`,
`Edit dashboard.html`) and opens to the full invocation. Kip the
[mascot](../assets/mascot) shows each session's state, in the list and on its page:
working mid-turn, waiting on you, celebrating a finished run, or rained on when the
gate blocked its stop. A session's page
splits what it touched into segments:

- **Issues**: the Linear, Jira or GitHub issue its runs publish to, and issue keys its
  Linear, Jira, Atlassian or GitHub MCP calls named.
- **Sinks**: where each run's receipts go, linked once published.
- **Sources**: MCP servers it called, web searches, URLs its tools fetched,
  links in prompts.
- **Receipts**: every run the session touched, each opening a review panel with
  every claim's verdict, its evidence (command, exit, excerpt, commit, log and
  patch), and its screenshots and videos inline.

## Runs

The **Runs** tab lists every receipts run, newest first, for triage: whether
it's open, finished or orphaned, and how it settled (`checkout`, `merged`,
`pr #N`, `branch gone`, `branch recreated`, `manual`). A run's page shows the
commits it covers (where it started, where its branch left the base branch,
where it was published and finished), the sessions that wrote to it, the
sessions that only shared its branch when a hook fired, and its claims grouped
by `kip-decompose` unit.

Flags mark what looks wrong: open on a branch that has merged, superseded by
a newer run on its branch, no writer recorded, sessions linked only by branch,
and settled before Kip recorded how. Only an open run's flags need attention,
which is what **Needs attention** filters to; a settled run's flags are
history, shown muted.

The dashboard serves only files a run recorded, and only while their hashes
still match.
