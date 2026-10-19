"""GitHub: the ledger in a marked block of the pull request's body, beside the author's description.

Settings: none; uses the `gh` CLI's own login (`gh auth status`), in the current repo.
Issue key: the pull request number. Editing the body fires `pull_request: edited`, so the
Kip action (`check --pr`) re-checks the PR. GitHub has no attachment API, so `upload` leaves
files to the agent's browser and `receipt.py record --file` (skill kip-receipts-github).
"""
import json
import re
import subprocess

FORMAT = "github"
BEGIN, END = "<!-- kip-receipt -->", "<!-- /kip-receipt -->"


def _gh(*args, stdin=None):
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {args[0]} {args[1]}: {r.stderr.strip()}")
    return r.stdout


def upload(conf, issue, path, content_type):
    return None


def publish(conf, issue, body, comment_id):
    pr = json.loads(_gh("pr", "view", issue, "--json", "body,url"))
    mine = re.sub(f"\n*{re.escape(BEGIN)}.*?{re.escape(END)}", "", pr["body"] or "", flags=re.S).rstrip()
    _gh("pr", "edit", issue, "--body-file", "-", stdin="\n\n".join(filter(None, [mine, f"{BEGIN}\n{body}\n{END}"])))
    return issue, pr["url"]
