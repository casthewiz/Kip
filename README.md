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

`install` links every skill (any folder with a `SKILL.md`, however deeply
nested; names must be unique) into each **host** it detects, wires up the always-on
[rules](rules/user-rules.md) (lazy senior developer, claims and evidence,
implementation workflow), and links the repo at `~/.kip/kip`. Links mean
a `git pull` updates every host; re-run `install` after adding or moving a
skill (links to moved skills are pruned). Options:

- `--host cursor`: install for one host, even if it isn't detected.
- `--project`: link into this repo's `.claude/skills/`, `.cursor/skills/`
  instead of your user folders.

Skills are always also linked into `~/.kip/skills/`, a host-neutral path
that skills use to call their own scripts. Claude Code gets the rules as an
`@` import in `~/.claude/CLAUDE.md`; Cursor keeps user rules in settings, so
`install` prints what to paste.

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
| `hosts` | Agent tools that load skills: `skills` dir, `project_skills` dir, how `rules` are installed (`append` an import line, or `manual` instructions) | kip, claude-code, cursor |
| `connectors` | The agent's own tools a surface can run through: `kind` is `mcp` or `tool` | browser |
| `evidence` | [Receipts providers](skills/verification/kip-receipts/providers/CONTRACT.md): a `doc` procedure, plus a `fallback` | frontend (Playwright `viewports`), browser, api, generic |
| `destinations` | [Receipts sinks](skills/verification/kip-receipts/sinks/CONTRACT.md): a module `type` with its `settings`, or `via` a connector; plus `default_destination` | local, linear, jira |

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

`kip-receipts` records evidence with `python3` (standard library only) under
`~/.kip/receipts/`. Local is the default; each session asks whether to also
publish to another destination, and a local run can be published later.
Script-backed destinations read their settings from config or the
environment (secrets only ever from the environment):

- Linear: `LINEAR_API_KEY`
- Jira: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`

## Verify

Start a new session in either tool and ask what skills are available, or
invoke one directly (`/kip-ponytail`, `/kip-receipts`). Skills load at session start,
so restart any session that was open during install.

## License

MIT — see [LICENSE](LICENSE).
