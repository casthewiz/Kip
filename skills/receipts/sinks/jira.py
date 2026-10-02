"""Jira Cloud: one issue comment in wiki markup; files become issue attachments,
images shown as thumbnails, videos and logs linked.

Requires JIRA_BASE_URL (e.g. https://acme.atlassian.net), JIRA_EMAIL, and
JIRA_API_TOKEN (id.atlassian.com → Security → API tokens).
Issue key: a Jira key like PROJ-123.
"""
import base64
import json
import os
import urllib.request
import uuid

FORMAT = "jira"
REQUIRES = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]


def _base():
    return os.environ["JIRA_BASE_URL"].rstrip("/")


def _req(method, path, data=None, headers=None):
    auth = base64.b64encode(f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}".encode()).decode()
    req = urllib.request.Request(_base() + path, data=data, method=method,
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def upload(issue, path, content_type):
    existing = _req("GET", f"/rest/api/3/issue/{issue}?fields=attachment")["fields"]["attachment"]
    if path.name not in {a["filename"] for a in existing}:
        b = uuid.uuid4().hex
        body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
        _req("POST", f"/rest/api/3/issue/{issue}/attachments", body,
             {"Content-Type": f"multipart/form-data; boundary={b}", "X-Atlassian-Token": "no-check"})
    return path.name  # wiki markup references attachments by name


def publish(issue, body, comment_id):
    data = json.dumps({"body": body}).encode()
    headers = {"Content-Type": "application/json"}
    if comment_id:
        c = _req("PUT", f"/rest/api/2/issue/{issue}/comment/{comment_id}", data, headers)
    else:
        c = _req("POST", f"/rest/api/2/issue/{issue}/comment", data, headers)
    return c["id"], f"{_base()}/browse/{issue}?focusedCommentId={c['id']}"
