"""GitHub issue: one issue comment with the ledger, the way the Linear sink does it.

Settings: none; uses the `gh` CLI's own login (`gh auth status`).
Issue key: `123` for an issue in the current repo, or `owner/repo#123`. GitHub has no attachment
API, so `upload` leaves files to the agent's browser and `receipt.py record --file`
(skill kip-receipts-github), on the issue's comment box instead of the PR's.
"""
import json
import subprocess

FORMAT = "github"  # GitHub plays a bare video URL inline in issue comments too


def upload(conf, issue, path, content_type):
    return None


def publish(conf, issue, body, comment_id):
    repo, _, number = issue.rpartition("#")
    if not number.isdigit():
        raise ValueError(f"issue key {issue!r} is not 123 or owner/repo#123")
    repo = repo or "{owner}/{repo}"  # gh fills these in from the current checkout
    path = f"repos/{repo}/issues/comments/{comment_id}" if comment_id else f"repos/{repo}/issues/{number}/comments"
    r = subprocess.run(["gh", "api", "-X", "PATCH" if comment_id else "POST", path, "--input", "-"],
                       input=json.dumps({"body": body}), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh api {path}: {r.stderr.strip()}")
    c = json.loads(r.stdout)
    return c["id"], c["html_url"]
