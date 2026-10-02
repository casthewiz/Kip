# Kip

A portable harness for agentic software development: skills and rules that
work in any project, with Claude Code or Cursor.

## Skills

| Skill | Purpose |
| --- | --- |
| **[analysis/](skills/analysis/)** | *How problems get understood* |
| [decompose](skills/analysis/decompose/SKILL.md) | Understand a problem and break it into units of work |
| **[implementation/](skills/implementation/)** | *How code gets written* |
| [ponytail](skills/implementation/ponytail/SKILL.md) | Laziest solution that actually works |
| [marie-kondo](skills/implementation/marie-kondo/SKILL.md) | Tidy the current branch diff before a PR |
| **Verification** | *How work gets proven* |
| [receipts](skills/receipts/SKILL.md) | Back every claim of done-ness with deterministic evidence |

Each skill is a folder with a `SKILL.md` (YAML frontmatter + instructions),
the format both Claude Code and Cursor load. Supporting files live inside the
skill's folder (e.g. [receipts/providers](skills/receipts/providers/)).
Skills can be grouped into category folders like `implementation/`; the
install step flattens them, so skill names must be unique across categories.

## Install

Clone the repo:

```bash
git clone https://github.com/casthewiz/Kip.git ~/Documents/GitHub/Kip
```

Symlink every skill into each tool's user skills folder. Both tools only
discover skills one level deep, so this links each folder that contains a
`SKILL.md`, wherever it's nested. Symlinks mean a `git pull` updates both
tools at once; re-run the loop after adding or moving a skill:

```bash
for dir in ~/.claude/skills ~/.cursor/skills; do
  mkdir -p "$dir"
  find ~/Documents/GitHub/Kip/skills -name SKILL.md | while read -r f; do
    s=$(dirname "$f")
    ln -sfn "$s" "$dir/$(basename "$s")"
  done
done
```

Drop either folder from the loop if you only use one tool. To scope skills to
a single project instead, symlink into that repo's `.claude/skills/` or
`.cursor/skills/`.

### Receipts storage

`receipts` records evidence with `python3` (standard library only) under
`~/.kip/receipts/`, and asks each session where else to publish it. To make
a tracker available, export its credentials in your shell profile:

- Linear: `LINEAR_API_KEY`
- Jira: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`

New destinations are one Python module each; see
[skills/receipts/sinks/CONTRACT.md](skills/receipts/sinks/CONTRACT.md).

## Rules

[rules/user-rules.md](rules/user-rules.md) holds the always-on rules (lazy
senior developer, claims and evidence, implementation workflow).

**Claude Code** — import the file from your global `~/.claude/CLAUDE.md` so it
stays in sync with the repo:

```bash
echo '@~/Documents/GitHub/Kip/rules/user-rules.md' >> ~/.claude/CLAUDE.md
```

**Cursor** — user rules live in settings, not on disk. Copy the file's
contents into **Cursor Settings → Rules → User Rules**, and re-paste after
pulling changes.

## Verify

Start a new session in either tool and ask what skills are available, or
invoke one directly (`/ponytail`, `/receipts`). Skills load at session start,
so restart any session that was open during install.

## License

MIT — see [LICENSE](LICENSE).
