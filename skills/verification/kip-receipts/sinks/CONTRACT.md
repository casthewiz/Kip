# Sink contract

A sink publishes a receipt run somewhere other than the local folder: an
issue tracker, a PR, a chat channel, a bucket. Each sink is one Python module
(standard library only) that `receipt.py` loads by name.

`local` is built in and always on: the run folder under `~/.kip/receipts` is
the source of truth every other sink publishes from.

## Where sinks live

Checked in order, first match wins:

1. `<repo>/.kip/sinks/<name>.py`: project sinks, to add one or override
   Kip's for a single repo.
2. `skills/kip-receipts/sinks/<name>.py`: Kip's sinks.

`receipt.py sinks` lists every sink found and whether it's configured.

## The interface

```python
"""<One line: what this sink publishes.>

Requires <ENV_VARS and where to get them>.
Issue key: <what a target looks like, e.g. ENG-123>.
"""

FORMAT = "markdown"        # renderer for the comment: "markdown" or "jira"
REQUIRES = ["MY_API_KEY"]  # env vars; publish skips the sink if any are unset


def upload(issue, path, content_type):
    """Upload one file (pathlib.Path) for this issue.
    Return the reference the comment should use: a URL for markdown sinks,
    an attachment name for jira. Called once per file per run."""


def publish(issue, body, comment_id):
    """Post body as a comment on issue, or update comment_id if it isn't None.
    Return (comment_id, url)."""
```

`receipt.py publish` does the rest, identically for every sink: checks
`REQUIRES`, uploads each file not yet uploaded to this sink, renders the
comment with this sink's `FORMAT` and upload references, creates or updates
the run's single comment, and records the result in `receipt.json`. A sink
never builds the comment body itself, so every sink shows the same ledger.

## Adding a sink

1. Write `<name>.py` with the four names above. The docstring is its docs.
2. If it needs a markup Kip doesn't render yet, add a renderer to
   `RENDERERS` in `receipt.py` and test it in `test_receipt.py`.
3. Run `receipt.py sinks` to confirm it's found, then
   `receipt.py render --sink <name>` to preview what it would post.

## Rules for every sink

- **Tokens come from the environment**, never from chat, never written into
  the record or the comment. `receipt.py` redacts secret-looking env values
  from output and errors.
- **Raise on failure.** `publish` reports the error, skips that sink, and
  keeps going with the others; the local record is unaffected.
- **Idempotent.** Re-publishing a run updates the same comment and never
  re-uploads a file.
