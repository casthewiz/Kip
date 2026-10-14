# Kip

A portable harness for agentic software development: skills and rules that
work in any project, with Claude Code or Cursor.

## Skills

| Skill | Purpose |
| --- | --- |
| **[analysis/](skills/analysis/)** | *How problems get understood* |
| [kip-decompose](skills/analysis/kip-decompose/SKILL.md) | Understand a problem and break it into units of work |
| **[implementation/](skills/implementation/)** | *How code gets written* |
| [kip-ponytail](skills/implementation/kip-ponytail/SKILL.md) | Laziest solution that actually works |
| [kip-marie-kondo](skills/implementation/kip-marie-kondo/SKILL.md) | Tidy the current branch diff before a PR |
| **[verification/](skills/verification/)** | *How work gets proven* |
| [kip-receipts](skills/verification/kip-receipts/SKILL.md) | Back every claim of done-ness with deterministic evidence |

Each skill is a folder with a `SKILL.md` (YAML frontmatter + instructions),
the format both Claude Code and Cursor load. Supporting files live inside the
skill's folder (e.g. [kip-receipts/providers](skills/verification/kip-receipts/providers/)).
Skills can be grouped into category folders like `implementation/`; the
install step flattens them, so skill names must be unique across categories.

## Install

```bash
git clone https://github.com/casthewiz/Kip.git ~/Documents/GitHub/Kip
python3 ~/Documents/GitHub/Kip/kip.py install
```

Or ask your agent to do it, in Claude Code or Cursor:

> Clone https://github.com/casthewiz/Kip.git to ~/Documents/GitHub/Kip, run
> `python3 kip.py install`, then `python3 kip.py doctor` and tell me what
> it reports.

Approve the edit to your agent's settings when asked (that's the hooks),
and paste the rules yourself if `install` says to. Then start a new
session: skills, rules, and hooks load at session start.

`install` links every skill (any folder with a `SKILL.md`, however deeply
nested; names must be unique) into each **host** it detects, wires up the always-on
[rules](rules/user-rules.md) (lazy senior developer, claims and evidence,
implementation workflow), and links the repo at `~/.kip/kip`. Links mean
a `git pull` updates every host; re-run `install` after adding or moving a
skill (links to moved skills are pruned). Options:

- `--host cursor`: install for one host, even if it isn't detected.
- `--project`: link into this repo's `.claude/skills/`, `.cursor/skills/`
  instead of your user folders.

`python3 kip.py uninstall` (same options) undoes all of it: Kip's links,
rules, and hooks go, and your own settings, rules, and hooks stay.

`install` also adds the receipts
[gate](skills/verification/kip-receipts/SKILL.md#gate) to each host's hooks
(Claude Code's `settings.json`, Cursor's `hooks.json`), leaving your own
hooks alone: once code changes, the agent can't end a turn with unaddressed
claims, evidence the code has since outgrown, or failing required checks,
or without a receipts run, or open a PR with refuted claims or without its
tidy and review passes. Each session starts knowing where the
branch's run stands; a run whose work has landed on the base branch is
finished and drops out. Cursor can't block a stop, so there the gate asks once,
as a follow-up message. Cursor keeps a copy of the rules, so `doctor` says
when it's stale and `install` needs re-running.

The hooks also keep a session log, one file per day in `~/.kip/receipts/events/`
(the dashboard keeps 14 days): every
gate decision and every tool call, redacted and cut short. Neither host has a
hook for skills, so a skill counts as used when a prompt names it (`/name`) or
attaches it, or a tool reads its `SKILL.md`. Each prompt and the agent's reply are kept too
(redacted, up to 20,000 characters), so a session's timeline reads as the conversation; `install --no-conversation`
turns that off (saved as `dashboard.conversation` in `~/.kip/config.json`, or the repo's with
`--project`), and `--conversation` turns it back on. A user `install` runs a live
dashboard at <http://127.0.0.1:7377> as a login service (a launchd agent on macOS,
a systemd user unit on Linux) that's always up; it runs from a copy at `~/.kip/dashboard`,
since macOS stalls background agents that read `~/Documents` and the like, so re-run
`install` after updating Kip. Elsewhere, a session's start launches it and it exits after
30 idle minutes. `KIP_DASHBOARD=0` never starts it; `KIP_DASHBOARD_PORT` moves the one a
session starts. It
lists sessions with the stage each is in (decompose, implement, tidy, review,
receipts), how many stops in a row the gate blocked, and its run's open
reasons, and streams each session's timeline as it happens. A session's page
splits what it touched into segments: **Issues** (the Linear or Jira issue its
runs publish to, and issue keys its Linear, Jira or Atlassian MCP calls named),
**Sinks** (where each run's receipts go, linked once published), **Sources** (MCP
servers it called, web searches, URLs its tools fetched, links in prompts), and
**Receipts**: every run the session touched, each opening a review panel with
every claim's verdict, its evidence (command, exit, excerpt, commit, log and
patch), and its screenshots and videos inline. The dashboard serves only files a
run recorded, and only while their hashes still match. Run it yourself with
`receipt.py serve`.

Skills are always also linked into `~/.kip/skills/`, a host-neutral path
that skills use to call their own scripts. Claude Code gets the rules as an
`@` import in `~/.claude/CLAUDE.md`. Cursor keeps User Rules in its state
database, so `install` writes them there between `kip rules` markers,
keeping your own rules, and you restart Cursor to load them. Cursor holds a
copy rather than an import, so re-run `install` after pulling. If the
database isn't found, `install` prints what to paste.

## Surfaces

Everything Kip plugs into is a **surface**, declared in one layered config.
Later layers win:

1. [`defaults.json`](defaults.json) in this repo
2. `~/.kip/config.json` (or `$KIP_HOME/config.json`): yours, across repos
3. `<repo>/.kip/config.json`: one project's

Dicts merge, other values replace, and `null` deletes. `python3 kip.py
config` prints the merged result; `python3 kip.py doctor` shows what each
surface needs and whether it's here.

| Kind | What it is | Defaults |
| --- | --- | --- |
| `hosts` | Agent tools that load skills: `skills` dir, `project_skills` dir, how `rules` are installed (`append` an import line, `sqlite` databases with a `key` to write the rules into, or `manual` instructions as the fallback), and `hooks` merged into the host's `settings` (or `project_settings`) | kip, claude-code (Stop and PR gates), cursor |
| `connectors` | The agent's own tools a surface can run through: `kind` is `mcp` or `tool` | browser |
| `evidence` | [Receipts providers](skills/verification/kip-receipts/providers/CONTRACT.md): a `doc` procedure, plus a `fallback` | frontend (Playwright `viewports`), browser, api, generic |
| `destinations` | [Receipts sinks](skills/verification/kip-receipts/sinks/CONTRACT.md): a module `type` with its `settings`, or `via` a connector; plus `default_destination` | local, linear, jira, github |

Plus `workflow.stages`: the [stages](skills/verification/kip-receipts/SKILL.md#stages)
a PR needs a claim for (default `["tidy", "review"]`; `[]` turns it off), and
`workflow.checks`: [required checks](skills/verification/kip-receipts/SKILL.md#required-checks),
commands like `{"tests": "npm test"}` that must pass on the current code
before a turn can end or a PR open (default none). And `dashboard.conversation`:
whether the session log keeps prompts and replies (default `true`).

Every surface can declare `requires` (`env`, `cmd`, `file`), which doctor
checks and `install` uses to detect hosts. A surface with `via` runs through
a connector, i.e. through the agent rather than a script. A script can't see
the agent's tools, so doctor reports those as **agent-confirm** and the
agent checks the tool is connected before using it.

A user config that adds a second Jira site, posts Linear comments through
Linear's MCP server, and only desk-tests mobile:

```json
{
  "connectors": {"linear-mcp": {"kind": "mcp"}},
  "destinations": {
    "jira-oss": {"type": "jira", "requires": {"env": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]},
                 "settings": {"JIRA_BASE_URL": "https://oss.atlassian.net"},
                 "env": {"JIRA_API_TOKEN": "JIRA_OSS_TOKEN"}},
    "linear": {"via": "linear-mcp"}
  },
  "evidence": {"frontend": {"viewports": {"tablet": null, "desktop": null}}}
}
```

### Receipts storage

`kip-receipts` records evidence with `python3` (standard library only) in
`~/.kip/receipts/kip.db`, a SQLite database that `install` creates (importing
any runs from the JSON files that came before it), `doctor` reports on, and
`uninstall` leaves in place. Local is the default; each session asks whether to also
publish to another destination, and a local run can be published later.
Script-backed destinations read their settings from config or the
environment (secrets only ever from the environment):

- Linear: `LINEAR_API_KEY`
- Jira: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
- GitHub: the `gh` CLI, logged in; the ledger goes in the PR body

### CI

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

## Verify

Start a new session in either tool and ask what skills are available, or
invoke one directly (`/kip-ponytail`, `/kip-receipts`). Skills load at session start,
so restart any session that was open during install.

## License

MIT — see [LICENSE](LICENSE).
