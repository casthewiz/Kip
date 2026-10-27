# Install

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

## What `install` does

`install` links every skill (any folder with a `SKILL.md`, however deeply
nested; names must be unique) into each **host** it detects, wires up the always-on
[rules](../rules/user-rules.md) (lazy senior developer, question tool for fixed
choices, claims and evidence, implementation workflow, Kip terms), adds the
[receipts gate](receipts.md#the-gate) to each host's hooks, starts the [dashboard](dashboard.md), and links the repo at
`~/.kip/kip`. Links mean a `git pull` updates every host. For what links can't
reach (new or moved skills, Cursor's copy of the rules, the dashboard's copy),
`install` also adds git hooks to the Kip repo that re-run it after every commit,
pull, branch switch, and rebase, so you never re-run it by hand; uncommitted
edits sync at the next commit. `KIP_GIT_HOOKS=0` skips them, and a hook that
isn't Kip's is left alone. Options:

- `--host cursor`: install for one host, even if it isn't detected.
- `--project`: link into this repo's `.claude/skills/`, `.cursor/skills/`
  instead of your user folders.
- `--no-conversation` / `--conversation`: whether the
  [session log](dashboard.md#session-log) keeps prompts and replies.

Run a user `install` from Kip's main checkout, not a linked worktree: what it
links would vanish with the worktree, so it refuses and names the main
checkout's command (`KIP_INSTALL_FROM_WORKTREE=1` installs from one anyway).
`install` also moves runs an older Kip stored under a worktree's folder name
to their repository.

`python3 kip.py uninstall` (same options) undoes all of it: Kip's links,
rules, and hooks go, and your own settings, rules, and hooks stay.

## Per host

Skills are always also linked into `~/.kip/skills/`, a host-neutral path
that skills use to call their own scripts. Claude Code gets the rules as an
`@` import in `~/.claude/CLAUDE.md`. Cursor keeps User Rules in its state
database, so `install` writes them there between `kip rules` markers,
keeping your own rules, and you restart Cursor to load them. Cursor holds a
copy rather than an import; the git hooks keep it current, and `doctor` says
when it's stale. If the database isn't found, `install` prints what to paste.

## Adding a skill

Each skill is a folder with a `SKILL.md` (YAML frontmatter + instructions),
the format both Claude Code and Cursor load. Supporting files live inside the
skill's folder (e.g. [kip-receipts/providers](../skills/verification/kip-receipts/providers/)).
Skills can be grouped into category folders like `implementation/`; the
install step flattens them, so skill names must be unique across categories.

## Verify

Start a new session in either tool and ask what skills are available, or
invoke one directly (`/kip-messy-bun`, `/kip-receipts`). Skills load at session start,
so restart any session that was open during install. `python3 kip.py doctor`
shows what each [surface](configuration.md) needs and whether it's here.
