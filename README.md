# Kip

Personal Cursor skills and user rules — generic enough to reuse on any project.

## Skills

| Skill | Purpose |
| --- | --- |
| [ponytail](skills/ponytail/SKILL.md) | Laziest solution that actually works |
| [marie-kondo](skills/marie-kondo/SKILL.md) | Tidy the current branch diff before a PR |

## Install

Clone this repo, then symlink each skill into Cursor’s user skills folder:

```bash
git clone https://github.com/casthewiz/Kip.git ~/Documents/GitHub/Kip
for s in ~/Documents/GitHub/Kip/skills/*; do
  ln -sfn "$s" ~/.cursor/skills/"$(basename "$s")"
done
```

## User rules

Copy the contents of [rules/user-rules.md](rules/user-rules.md) into **Cursor Settings → Rules → User Rules**.

## License

MIT — see [LICENSE](LICENSE).
