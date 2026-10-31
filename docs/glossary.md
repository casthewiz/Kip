# Glossary

Every term Kip uses, one line each, with a link to the doc that owns it.
When you add or rename a term, update it here; `test_kip.py` fails when a
verdict, stage, config key, hook mode, or subcommand defined in code is
missing.

## Packaging

- **Kip**: this repo, a portable harness of skills, rules, and hooks, linked at `~/.kip/kip`. Also the mascot, whose pose shows a session's or run's state. [README](../README.md)
- **host**: an agent tool that loads skills (Claude Code, Cursor, Codex, and `kip` for the host-neutral `~/.kip/skills`). [configuration](configuration.md)
- **skill**: a folder with a `SKILL.md` (frontmatter and instructions), grouped under category folders (`analysis/`, `implementation/`, `verification/`) and flattened on install. [install](install.md#adding-a-skill)
- **rules**: the always-on instructions in [`rules/user-rules.md`](../rules/user-rules.md), installed into each host (an `@` import for Claude Code, a copy for Cursor and Codex).
- **hooks**: host events (session start, stop, tool use, prompt) wired to `receipt.py check`. [receipts](receipts.md#the-gate)
- **hook trust**: a host's own approval of each hook before it runs (Codex's `/hooks`); `install` never grants it, and `doctor` reports it. [install](install.md#per-host)
- **gate**: what the hooks enforce: no edit before decomposing, no stop with open claims, no PR without its stages. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#gate)
- **surface**: anything Kip plugs into, declared in config under one of the surface kinds. [configuration](configuration.md)
- **connector**: one of the agent's own tools (`mcp` or `tool`) that a surface can run through with `via`. [configuration](configuration.md)
- **agent-confirm**: what `kip.py doctor` reports for a surface `via` a connector, since a script can't see the agent's tools; the agent checks the tool is connected. [configuration](configuration.md)
- **setup step**: a one-time step a person does themselves (Playwright's download), reminded at session start until done or skipped. [configuration](configuration.md)
- **intent**: what a browser task must get done, written only in browser operations, without naming a surface. [interfaces](../skills/verification/kip-receipts/interfaces/CONTRACT.md)
- **interface**: how one surface carries out intents: a host interface maps browser operations to its tools, a site interface gives URLs and selectors. [interfaces](../skills/verification/kip-receipts/interfaces/CONTRACT.md)
- **browser operation**: `open`, `find`, `set_files`, `eval`, `screenshot`, the vocabulary intents are written in. [interfaces](../skills/verification/kip-receipts/interfaces/CONTRACT.md)

## Workflow

The loop every non-trivial task follows; a session's **stage** is where it is in it. [README](../README.md#how-it-works)

- **decompose**: understand the problem and cut it into units of work with claims (`kip-decompose`). [kip-decompose](../skills/analysis/kip-decompose/SKILL.md)
- **implement**: build it the laziest way that works (`kip-messy-bun`). [kip-messy-bun](../skills/implementation/kip-messy-bun/SKILL.md)
- **tidy**: strip the branch's diff of noise and dead weight (`kip-marie-kondo`); a PR needs a `--stage tidy` claim. [kip-marie-kondo](../skills/implementation/kip-marie-kondo/SKILL.md)
- **review**: a correctness pass over the branch diff; a PR needs a `--stage review` claim. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#stages)
- **receipts**: back every claim with evidence (`kip-receipts`). [kip-receipts](../skills/verification/kip-receipts/SKILL.md)
- **level**: `lite`, `full` (default), or `ultra`. Each skill reads it its own way: effort (decompose), intensity (messy-bun), what gets cut (marie-kondo), thoroughness (receipts).
- **the ladder**: messy-bun's order of preference: YAGNI, reuse, stdlib, native, installed dependency, one line, minimum code. [kip-messy-bun](../skills/implementation/kip-messy-bun/SKILL.md#the-ladder)
- **nudge**: a gate block that fires once a session, then lets the action through; the middle of `required`, `nudge`, `off`. [configuration](configuration.md)

## Planning

- **problem**: decompose's split of what was asked, what has to change, and what exists today. [kip-decompose](../skills/analysis/kip-decompose/SKILL.md#understanding)
- **unit** (of work): the smallest piece that is verifiable on its own: goal, dependencies, parallel or not, claims, touches. [kip-decompose](../skills/analysis/kip-decompose/SKILL.md#units-of-work)
- **plan run**: the receipts run decompose records, its claims tagged `--unit N`. [kip-decompose](../skills/analysis/kip-decompose/SKILL.md#recording-the-plan)
- **unit run**: a run started with `init --parent <plan run> --unit N`, usually in the unit's worktree, which takes over that unit's open claims. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#recording)
- **delegated claim**: a plan claim handed to a unit run; its verdict is the unit run's, and it returns to the plan if that run is orphaned. [dashboard](dashboard.md#runs)

## Receipts

- **run**: one task's record of claims and evidence, per branch, in `kip.db`; commands default to the branch's latest run. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#recording)
- **claim**: one falsifiable statement of done-ness, written before gathering evidence. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#claims)
- **evidence**: a deterministic, rerunnable check plus its observed output. Strongest to weakest: behavior shown to fail without the change, behavior, static, presence. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#evidence)
- **check**: the command behind evidence; runs of the same command in the same directory are one check, and its latest run decides it. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#verdicts)
- **fix loop**: what a claim's failed run opens: each rerun of that check, or a corrected command (`run --replace`), is an **attempt** that supersedes the one before; after `workflow.loop`'s attempts or minutes it **closes**, refusing runs until a person reopens it. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#fix-loops)
- **superseded**: an earlier attempt in a fix loop; it stays in the record and the ledger but no longer decides the verdict.
- **observation**: evidence that is the agent's account (`receipt.py observe`), for checks with no command equivalent such as a browser pane.
- **attachment**: a screenshot, video, trace, or log on a claim (`receipt.py attach`, or `run --collect`).
- **ledger**: the rendered table of claims, verdicts, and evidence (`receipt.py render`), posted verbatim by every sink.
- **target**: what a run verifies: this checkout (evidence pinned to a commit and patch) or a remote URL (`init --target`, not pinned).
- **baseline** / **`--red`**: a run of the check against code that lacks the change, expected to fail; red-then-green shows falsifiability. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#whose-work)
- **falsifiability**: the check could fail if the claim were false; required at ultra.
- **thoroughness**: how much effort goes into evidence per claim, by level. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#thoroughness)
- **ultra floor**: claims touching money, auth, security, permissions, deletion, or migrations are held to ultra (`claim --level ultra`).
- **required check**: a `workflow.checks` command; `init` adds one claim for it, and only that command settles it. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#required-checks)
- **desk test**: one Playwright spec run against a URL at each configured viewport, via `playwright/desktest.py`. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#desk-testing-websites)
- **viewport**: a named browser size (mobile, tablet, desktop) under `evidence.frontend.viewports`.
- **capture**: a screenshot or recording of a site made by `kip-kubrick`, in a spec or with no spec (`kubrick.py shot|record`), collected as a claim's attachments. [kip-kubrick](../skills/verification/kip-kubrick/SKILL.md)
- **shot**: a named PNG from `shot(page, name)`, taken after the assertions with animations finished, at full height even inside a scrolling container. [kip-kubrick](../skills/verification/kip-kubrick/SKILL.md#steps)
- **provider**: the procedure for producing evidence on one claim surface (frontend, browser, api, generic), with a `fallback`; an `evidence` surface. [providers](../skills/verification/kip-receipts/providers/CONTRACT.md)

### Verdicts

`receipt.py` computes exactly one per claim. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#verdicts)

- ✅ **verified**: all evidence passed.
- ❌ **refuted**: a check on the new code failed.
- 🙈 **check can't fail**: a `--red` run passed, so the check proves nothing.
- ⚠️ **unverified**: no evidence, or marked `unverified` with a reason.
- 🧪 **falsifiability not shown**: an ultra claim with no `--red` run.
- 🕰️ **stale**: the code changed since the check last passed.
- 🗣️ **self-reported**: only observations, nothing attached.
- 🎲 **flaky**: the same check failed and passed on the same code.
- 🥅 **check changed after failing**: the check's own files changed between failing and passing.
- 🌐 **passed after failing, code not pinned**: a remote target passed after failing, with no record of whether its code changed.
- 🔀 **served from another checkout**: the local URL was served by another worktree's dev server.
- **accepted**: not a verdict; the user took an unverified claim as it stands (`receipt.py accept`), so the dashboard stops asking.

## Run lifecycle

- **open**: a run still taking claims and evidence; the only state whose flags need attention.
- **finished**: its commits are on the base branch and nothing changed since; takes no new claims. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#gate)
- **orphaned**: set aside as abandoned (`receipt.py orphan`, or by sweep); its branch counts as having no run.
- **settled**: finished or orphaned, recorded with how: `checkout`, `merged`, `pr #N`, `branch gone`, `branch recreated`, `worktree removed`, `manual`. [dashboard](dashboard.md#runs)
- **sweep**: `receipt.py sweep` settles a repo's leftovers: merged runs finish, gone branches orphan, Kip-made worktrees are removed. The dashboard sweeps hourly.
- **flag**: a dashboard warning that something looks wrong with a run (open on a merged branch, superseded, no writer, …). [dashboard](dashboard.md#runs)

## Publishing

- **destination**: where a run's ledger goes, a `destinations` surface; `local` is built in and always on, others are opt-in per session. [kip-receipts](../skills/verification/kip-receipts/SKILL.md#destination)
- **sink**: the thing that publishes to a destination: **script-backed** (a Python module with `upload` and `publish`) or **agent-backed** (`via` a connector, posted by the agent then `receipt.py record`). [sinks](../skills/verification/kip-receipts/sinks/CONTRACT.md)
- **issue**: the target a run publishes to (a Linear or Jira key, a PR or issue number); one per run.
- **renderer** / **`FORMAT`**: the markup a sink's ledger is rendered in: `markdown`, `jira`, or `github`.
- **CI action**: Kip's GitHub Action, which fails a PR whose published ledger is missing, out of date, or not done (`receipt.py check --pr`). [receipts](receipts.md#ci)

## Config

Layered: `defaults.json`, then `~/.kip/config.json`, then `<repo>/.kip/config.json`; dicts merge, `null` deletes. [configuration](configuration.md)

- `hosts`, `connectors`, `evidence`, `destinations`, `setup`: the surface kinds.
- `default_destination`: the destination `receipt.py sinks` recommends (default `local`).
- `workflow`: the gate's settings:
  - `workflow.stages`: stages a PR needs a claim for (default `tidy`, `review`).
  - `workflow.checks`: required check commands.
  - `workflow.worktrees`: how hard edits are steered off a main checkout (`nudge`, `required`, `off`).
  - `workflow.skills`: which workflow skills the gate holds a session to (`decompose`, `implement`, `tidy`).
  - `workflow.loop`: a fix loop's bound, `attempts` and wall-clock `minutes` from the first failure (default 3 and 10).
  - `workflow.worktree.setup`: a command run in each new `kip.py worktree new` worktree.
- `dashboard`: `dashboard.conversation`, whether the session log keeps prompts and replies.
- `requires`: what a surface needs (`env`, `cmd`, `file`), checked by doctor.
- `via`: the connector a surface runs through.

## Worktrees and storage

- **main checkout**: a repo's primary working tree; the gate steers edits off it. [receipts](receipts.md#worktrees)
- **linked worktree**: one per task or parallel unit; Kip's are at `.kip/worktrees/NAME` on branch `kip/NAME`, a host's (`.claude/worktrees/`) work the same.
- **`.worktreeinclude`**: gitignored files copied into each new Kip worktree.
- **base branch**: `origin`'s default branch (or local `main` with no remote); landing there finishes a run.
- **`kip.db`**: the SQLite store at `~/.kip/receipts/kip.db`; each run's files sit beside it in `<repo>/<run-id>/`. [receipts](receipts.md#storage)
- **session log**: per-day event files in `~/.kip/receipts/events/`: gate decisions, tool calls, prompts and replies. [dashboard](dashboard.md#session-log)
- **dashboard**: the local web view at <http://127.0.0.1:7377> of sessions, runs, and receipts (`receipt.py serve`). [dashboard](dashboard.md)
- **segments**: a session page's Issues, Sinks, Sources, and Receipts. [dashboard](dashboard.md#what-it-shows)

## Commands

`kip.py` (install and config):

- `kip.py install` / `kip.py uninstall`: link or unlink skills, rules, hooks, and the dashboard. [install](install.md)
- `kip.py config`: print the merged config.
- `kip.py doctor`: what each surface needs and whether it's here.
- `kip.py worktree new NAME [--parent RUN --unit N]`: make a linked worktree, optionally starting a unit run.
- `kip.py setup [--skip NAME]`: list or skip setup steps.

`receipt.py` (receipts; `~/.kip/skills/kip-receipts/receipt.py`):

- `receipt.py init`: start a run (`--level`, `--sink`, `--issue`, `--target`, `--parent`, `--unit`).
- `receipt.py claim`: add a claim (`--provider`, `--level`, `--unit`, `--stage`).
- `receipt.py run`: run a check as evidence for claims (`--red`, `--static`, `--collect`, `--replace`).
- `receipt.py observe`: record an observation (`--passed` / `--failed`).
- `receipt.py attach`: attach a file to a claim.
- `receipt.py unverified`: mark a claim unverified with a reason.
- `receipt.py accept`: accept an unverified claim.
- `receipt.py reopen`: a person reopens a claim's closed fix loop.
- `receipt.py orphan`: set the run aside.
- `receipt.py sweep`: settle the repo's leftover runs.
- `receipt.py render`: print the ledger (`--sink` for one sink's markup).
- `receipt.py publish`: post the run to its sinks.
- `receipt.py record`: record what an agent-backed or browser upload returned.
- `receipt.py sinks`: list destinations and readiness.
- `receipt.py path` / `receipt.py json` / `receipt.py db`: the run's folder, its record, the database.
- `receipt.py check`: the gate, run by hooks with `--hook` (or `--pr N` in CI).
- `receipt.py serve`: run the dashboard.

`kubrick.py` (captures; `~/.kip/skills/kip-kubrick/kubrick.py`):

- `kubrick.py shot CLAIM URL` / `kubrick.py record CLAIM URL`: a PNG or WebM per viewport through `desktest.py`, with no spec (`--viewports`, `--run`).
- `kubrick.py mp4 VIDEO [OUT]`: an H.264 MP4 under 10 MB that GitHub plays inline.

`--hook` modes, one per host event: `start`, `stop`, `subagent`, `pr`, `edit`, `tool`, `ask` (a tool call waiting on approval), `prompt`, `response`, `end`.
