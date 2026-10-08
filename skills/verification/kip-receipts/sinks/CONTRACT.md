# Sink contract

A sink publishes a receipt run somewhere other than the local folder: an
issue tracker, a PR, a chat channel, a bucket. Sinks are the `destinations`
in Kip's config (`kip.py config`), and each is either:

- **script-backed**: a Python module (standard library only) that
  `receipt.py` loads by the destination's `type` and calls with its
  settings, or
- **agent-backed**: `via` a connector (an MCP server or agent tool). There's
  no module; the agent posts and records the result.

`local` is built in and always on: the run folder under `~/.kip/receipts` is
the source of truth every other sink publishes from.

## Configuring a destination

```json
{
  "connectors": {"linear-mcp": {"kind": "mcp", "about": "Linear's MCP server"}},
  "destinations": {
    "jira-oss": {"type": "jira", "requires": {"env": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]},
                 "settings": {"JIRA_BASE_URL": "https://oss.atlassian.net"},
                 "env": {"JIRA_API_TOKEN": "JIRA_OSS_TOKEN"}},
    "linear": {"via": "linear-mcp"}
  },
  "default_destination": "jira-oss"
}
```

- `type`: the module name; defaults to the destination's name.
- `requires.env`: the settings the module reads. Each comes from a literal
  in `settings`, else the env var `env` maps it to, else the env var of the
  same name. Secret-looking names (`*TOKEN*`, `*KEY*`, …) are refused in
  `settings`: secrets only come from the environment.
- `format`: renderer for the comment, overriding the module's `FORMAT`
  (and the only way to set it for agent-backed destinations).
- `null` deletes a destination a lower layer defined.

A module in `<repo>/.kip/sinks/` that no config names is still available,
under its own name with no settings.

## Where modules live

Checked in order, first match wins:

1. `<repo>/.kip/sinks/<name>.py`: project sinks, to add one or override
   Kip's for a single repo.
2. `skills/kip-receipts/sinks/<name>.py`: Kip's sinks.

`receipt.py sinks` lists every destination, whether it's ready, and which is
recommended.

## The interface

```python
"""<One line: what this sink publishes.>

Requires <ENV_VARS and where to get them>.
Issue key: <what a target looks like, e.g. ENG-123>.
"""

FORMAT = "markdown"        # renderer for the comment: "markdown" or "jira"


def upload(conf, issue, path, content_type):
    """Upload one file (pathlib.Path) for this issue. conf holds the
    destination's resolved settings (see Configuring a destination).
    Return the reference the comment should use: a URL for markdown sinks,
    an attachment name for jira. Called once per file per run."""


def publish(conf, issue, body, comment_id):
    """Post body as a comment on issue, or update comment_id if it isn't None.
    Return (comment_id, url)."""
```

`receipt.py publish` does the rest, identically for every sink: checks the
destination's settings are all set, uploads each file not yet uploaded to this sink, renders the
comment with this sink's `FORMAT` and upload references, creates or updates
the run's single comment, and records the result in `receipt.json`. A sink
never builds the comment body itself, so every sink shows the same ledger.

## Adding a sink

1. Write `<name>.py` with the three names above. The docstring is its docs,
   including the settings it reads. Add a destination for it in config with
   those settings under `requires.env`.
2. If it needs a markup Kip doesn't render yet, add a renderer to
   `RENDERERS` in `receipt.py` and test it in `test_receipt.py`.
3. Run `receipt.py sinks` to confirm it's found, then
   `receipt.py render --sink <name>` to preview what it would post.

## Rules for every sink

- **Tokens come from the environment** (through `conf`), never from chat,
  never written into config, the record, or the comment. `receipt.py` redacts secret-looking env values
  from output and errors.
- **Raise on failure.** `publish` reports the error, skips that sink, and
  keeps going with the others; the local record is unaffected.
- **Idempotent.** Re-publishing a run updates the same comment and never
  re-uploads a file.
