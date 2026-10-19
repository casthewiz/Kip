# Configuration

Everything Kip plugs into is a **surface**, declared in one layered config.
Later layers win:

1. [`defaults.json`](../defaults.json) in this repo
2. `~/.kip/config.json` (or `$KIP_HOME/config.json`): yours, across repos
3. `<repo>/.kip/config.json`: one project's

Dicts merge, other values replace, and `null` deletes. `python3 kip.py
config` prints the merged result; `python3 kip.py doctor` shows what each
surface needs and whether it's here.

| Kind | What it is | Defaults |
| --- | --- | --- |
| `hosts` | Agent tools that load skills: `skills` dir, `project_skills` dir, how `rules` are installed (`append` an import line, `sqlite` databases with a `key` to write the rules into, or `manual` instructions as the fallback), and `hooks` merged into the host's `settings` (or `project_settings`) | kip, claude-code (Stop and PR gates), cursor |
| `connectors` | The agent's own tools a surface can run through: `kind` is `mcp` or `tool` | browser |
| `evidence` | [Receipts providers](../skills/verification/kip-receipts/providers/CONTRACT.md): a `doc` procedure, plus a `fallback` | frontend (Playwright `viewports`), browser, api, generic |
| `destinations` | [Receipts sinks](../skills/verification/kip-receipts/sinks/CONTRACT.md): a module `type` with its `settings`, or `via` a connector; plus `default_destination` | local, linear, jira, github |
| `setup` | One-time steps a person does themselves (a sign-in, a download): `about`, the `run` command, and `requires` that's met once it's done. Every session start reminds the agent of each one not done, to offer the user; `kip.py setup --skip NAME` (a `null` in your config) stops it | playwright |

Plus `workflow.stages`: the [stages](../skills/verification/kip-receipts/SKILL.md#stages)
a PR needs a claim for (default `["tidy", "review"]`; `[]` turns it off), and
`workflow.checks`: [required checks](../skills/verification/kip-receipts/SKILL.md#required-checks),
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
