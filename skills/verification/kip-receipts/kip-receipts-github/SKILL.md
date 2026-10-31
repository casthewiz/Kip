---
name: kip-receipts-github
description: >
  Gets a receipts run's images and videos onto a GitHub pull request or issue from any
  agent host (Claude Code, Cursor, Codex, anything with Playwright MCP), and keeps
  that working. GitHub has no API for attachments, so the agent uploads
  each file through the PR's or issue's comment box with its own host's browser tools,
  reads back the github.com/user-attachments URL, records it with
  `receipt.py record --file`, and publishes again, so images embed and videos
  play inline in the ledger. The steps are surface-agnostic; how each host
  and GitHub do them lives in kip-receipts/interfaces/. Use when
  `receipt.py publish` prints "github: not uploaded" or "github-issue: not
  uploaded", when the user wants screenshots or videos on a PR or issue, or when the upload breaks. Do NOT use for
  Linear or Jira (their sinks upload by API) or when no file is attached to
  the run.
license: MIT
---

# Receipts on GitHub: attach files to a PR or issue

The `github` sink ([`../sinks/github.py`](../sinks/github.py)) writes the
ledger into the PR body with `gh`, and the `github-issue` sink
([`../sinks/github-issue.py`](../sinks/github-issue.py)) into an issue comment,
but nothing in `gh` or the API can attach files: only the page's comment box can. So the agent uploads them with
its browser and `receipt.py` records where they landed.

This is an **intent**: the steps below use the browser operations in
[`../interfaces/CONTRACT.md`](../interfaces/CONTRACT.md) and name no host.
Before starting, read:

- the **host interface** for where you're running:
  [claude-code](../interfaces/claude-code.md), [cursor](../interfaces/cursor.md),
  [codex](../interfaces/codex.md), or [playwright-mcp](../interfaces/playwright-mcp.md) (none fits: add one,
  per the contract);
- the **site interface** [github](../interfaces/github.md), for the
  selectors, accepted files, and result URLs these steps rely on.

## Steps

1. `receipt.py publish --sink github --issue <PR>` (or `--sink github-issue
   --issue <issue>`; use that sink's name in every step below). It prints
   `github: not uploaded: files/…` for files with no URL yet; those paths
   are relative to `receipt.py path`.
2. Check each file against GitHub's accepted types and sizes, and the host's
   limits; convert or copy it as they say.
3. `open` the PR or issue page. A sign-in page: stop and ask the user to sign in to
   GitHub in that browser; never type credentials.
4. For each file:
   1. `set_files` on the comment box's file input with its absolute path.
   2. Poll `eval` of the textarea until it holds the `user-attachments`
      URL (seconds; longer for video). Take the URL.
   3. `receipt.py record --sink github --file <files/NAME> --url <URL>`.
   4. Clear the textarea with `eval`. **Never submit the comment**: the
      ledger already lives in the PR body or its own issue comment, and
      another comment would duplicate it.
5. `receipt.py publish` again. `render --sink github` shows the result:
   images as a thumbnail gallery under the claims table, videos alone on a
   line in their claim's details, which GitHub plays inline.
6. Reload the page and check each image rendered (the site interface says
   how).

If the host has no browser, or the user declines, leave the files "not
uploaded" (the ledger names each with its hash) and say so. Don't host them
anywhere else without asking.

## Maintenance

When this breaks, fix the surface that changed, not each run:

- **GitHub's page changed** (input not found, no URL, new URL shape): update
  [github.md](../interfaces/github.md). If videos no longer come back as a
  bare URL, also update the branch in `receipt.py`'s
  `render_markdown` that puts a GitHub video's URL alone on a line, and its
  test in `test_receipt.py`.
- **A host's tools changed or refused a call**: update that host's
  interface, e.g. Cursor's `browser_cdp` refusing `DOM.setFileInputFiles`
  means its `set_files` row moves to `eval`.
- **Smoke test** (needs the user's go-ahead: it uploads to GitHub): on a PR
  the user names, run step 4 for one small PNG, check it renders (step 6),
  record it as receipts evidence with `observe`, then update the "Last
  verified" lines in both interfaces you used.
- **GitHub ships an upload API**: implement `upload()` in `sinks/github.py`
  with it and retire this skill.
