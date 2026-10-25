# Kip

<img src="assets/mascot/kip-working.svg" width="96" alt="Kip, a round pixel-art mascot with glasses and a messy bun, typing on a laptop">

**A portable harness for agentic software development.** Skills, rules, and
hooks that make Claude Code and Cursor work like a careful senior engineer,
in any project.

Agents are fast but overconfident: they over-build, leave clutter behind, and
say "done" without proof. Kip gives them a workflow, and enforces the end of it.

```bash
git clone https://github.com/casthewiz/Kip.git ~/Documents/GitHub/Kip
python3 ~/Documents/GitHub/Kip/kip.py install
```

## How it works

Every non-trivial task follows the same loop:

1. **Decompose**: understand the problem, then break it into units of work,
   each with the claims that will mean it's done.
2. **Implement, lazily**: YAGNI, reuse, stdlib, native, one line before
   fifty. The best code is the code never written.
3. **Tidy**: strip the branch's diff of dead code, duplication, and noise.
4. **Review**: a pass for correctness bugs.
5. **Receipts**: every claim backed by rerunnable, deterministic evidence.

The loop isn't a suggestion. Kip's gate hooks into the agent: it can't edit
before decomposing, end a turn without tidying or with unproven claims or
failing checks, or open a PR without its tidy and review passes. A
[GitHub Action](docs/receipts.md#ci) holds every PR to the same bar, whoever
opened it.

## Skills

| Skill | Purpose |
| --- | --- |
| [kip-decompose](skills/analysis/kip-decompose/SKILL.md) | Understand a problem and break it into units of work |
| [kip-messy-bun](skills/implementation/kip-messy-bun/SKILL.md) | Laziest solution that actually works |
| [kip-marie-kondo](skills/implementation/kip-marie-kondo/SKILL.md) | Tidy the current branch diff before a PR |
| [kip-receipts](skills/verification/kip-receipts/SKILL.md) | Back every claim of done-ness with deterministic evidence |

Plus always-on [rules](rules/user-rules.md) that tie them together.

## Highlights

- **Works where you work.** One install wires Claude Code and Cursor; a
  `git pull` updates both.
- **Evidence, not vibes.** Receipts record commands, outputs, screenshots, and
  videos, including Playwright desk tests across viewports, and publish them
  locally or to Linear, Jira, a GitHub issue, or the PR.
- **See every session.** A local [dashboard](docs/dashboard.md) streams each
  session's timeline, stage, and receipts as it happens.
- **Worktrees by default.** Parallel work goes in linked worktrees: a plan hands
  each unit's claims to the unit's own run, and the gate steers edits off the
  main checkout ([how](docs/receipts.md#worktrees)).
- **Configurable, not forked.** Hosts, evidence providers, and destinations are
  layered [config](docs/configuration.md), per user or per repo.
- **No dependencies.** Python 3.8+ standard library.

## Docs

- [Install](docs/install.md): setup, options, uninstall, adding skills
- [Receipts](docs/receipts.md): the gate, storage, and CI
- [Dashboard](docs/dashboard.md): the session log and live dashboard
- [Configuration](docs/configuration.md): surfaces and layered config
- [Glossary](docs/glossary.md): every Kip term in one place

## License

MIT — see [LICENSE](LICENSE).
