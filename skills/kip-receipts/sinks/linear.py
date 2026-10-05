"""Linear: one issue comment with the ledger; images embedded, videos and logs linked.

Requires LINEAR_API_KEY (Linear → Settings → Security & access → Personal API keys).
Issue key: a Linear identifier like ENG-123.
"""
import json
import os
import urllib.request

FORMAT = "markdown"
REQUIRES = ["LINEAR_API_KEY"]
API = "https://api.linear.app/graphql"


def _gql(query, **variables):
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": os.environ["LINEAR_API_KEY"]})
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    if out.get("errors"):
        raise RuntimeError(out["errors"][0].get("message", "GraphQL error"))
    return out["data"]


def upload(issue, path, content_type):
    slot = _gql("""
        mutation($type: String!, $name: String!, $size: Int!) {
          fileUpload(contentType: $type, filename: $name, size: $size) {
            uploadFile { uploadUrl assetUrl headers { key value } }
          }
        }""", type=content_type, name=path.name, size=path.stat().st_size)["fileUpload"]["uploadFile"]
    headers = {"Content-Type": content_type, **{h["key"]: h["value"] for h in slot["headers"]}}
    put = urllib.request.Request(slot["uploadUrl"], data=path.read_bytes(), method="PUT", headers=headers)
    urllib.request.urlopen(put).close()
    return slot["assetUrl"]


def publish(issue, body, comment_id):
    if comment_id:
        c = _gql("""
            mutation($id: String!, $body: String!) {
              commentUpdate(id: $id, input: {body: $body}) { comment { id url } }
            }""", id=comment_id, body=body)["commentUpdate"]["comment"]
    else:
        issue_id = _gql("query($id: String!) { issue(id: $id) { id } }", id=issue)["issue"]["id"]
        c = _gql("""
            mutation($issueId: String!, $body: String!) {
              commentCreate(input: {issueId: $issueId, body: $body}) { comment { id url } }
            }""", issueId=issue_id, body=body)["commentCreate"]["comment"]
    return c["id"], c["url"]
