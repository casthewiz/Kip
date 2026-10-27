#!/usr/bin/env python3
"""Self-check for receipt.py and the sink interface: python3 test_receipt.py"""
import datetime
import hashlib
import json
import os
import re
import queue
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("receipt.py")  # 3.8 leaves __file__ relative
DESKTEST = Path(__file__).resolve().with_name("playwright") / "desktest.py"

# Stands in for the playwright CLI: writes a screenshot (and a video at ultra) per
# project, and fails on mobile when the site is "broken".
FAKE_PLAYWRIGHT = '''#!/bin/sh
for arg; do case "$arg" in --project=*) vp="${arg#--project=}";; esac; done
mkdir -p "$KIP_OUT/signup-$vp"
printf 'png' > "$KIP_OUT/signup-$vp/test-finished-1.png"
[ "$KIP_LEVEL" = ultra ] && printf 'webm' > "$KIP_OUT/signup-$vp/video.webm"
echo "1 test on $vp against $BASE_URL"
case "$BASE_URL:$vp" in *broken*:mobile) echo "overflow on mobile"; exit 1;; esac
exit 0
'''

# A project-level sink, loaded from <repo>/.kip/sinks/, that logs calls instead of posting.
# Posts to its FAKE_URL setting, so each configured instance lands somewhere different.
FAKE_SINK = '''
import json, os
from pathlib import Path
FORMAT = "markdown"
def _log(*event):
    p = Path(os.environ["FAKE_SINK_LOG"])
    p.write_text(json.dumps((json.loads(p.read_text()) if p.exists() else []) + [list(event)]))
def upload(conf, issue, path, content_type):
    _log("upload", issue, path.name, content_type)
    return conf.get("FAKE_URL", "https://fake.test") + "/" + path.name
def publish(conf, issue, body, comment_id):
    _log("update" if comment_id else "create", issue, body)
    return comment_id or "cm1", conf.get("FAKE_URL", "https://fake.test") + "/" + issue + "#cm1"
'''

# Stands in for gh: one pull request whose body lives in $FAKE_PR_BODY, and issue comments logged to
# $FAKE_GH_LOG (each call's arguments, then the JSON it was sent).
FAKE_GH = f'''#!/bin/sh
case "$1 $2" in
  "api -X") echo "$*" >> "$FAKE_GH_LOG"; cat >> "$FAKE_GH_LOG"; echo >> "$FAKE_GH_LOG"
            echo \'{{"id": 99, "html_url": "https://github.test/issues/7#issuecomment-99"}}\';;
  "pr view") {sys.executable} -c 'import json, os, sys; print(json.dumps({{"body": open(os.environ["FAKE_PR_BODY"]).read(), "url": "https://github.test/pull/" + sys.argv[1], "headRefOid": os.environ["FAKE_PR_HEAD"]}}))' "$3";;
  "pr edit") cat > "$FAKE_PR_BODY";;
  "pr list") [ -n "$FAKE_MERGED_PRS" ] || exit 1; cat "$FAKE_MERGED_PRS";;
  *) echo "unexpected: gh $*" >&2; exit 1;;
esac
'''

# Two instances of one sink module with their own settings, an agent-backed destination,
# and a project default destination.
PROJECT_CONFIG = {
    "connectors": {"tracker-mcp": {"kind": "mcp"}},
    "destinations": {
        "fake-a": {"type": "fake", "requires": {"env": ["FAKE_URL"]}, "settings": {"FAKE_URL": "https://a.test"}},
        "fake-b": {"type": "fake", "requires": {"env": ["FAKE_URL"]}, "env": {"FAKE_URL": "FAKE_B_URL"}},
        "tracker": {"via": "tracker-mcp"},
        "weird": {"via": "tracker-mcp", "format": "rtf"},
    },
    "default_destination": "fake-a",
}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp, "demo")
        repo.mkdir()
        sink_log = Path(tmp, "sink.json")
        env = {**os.environ, "KIP_RECEIPTS_DIR": str(Path(tmp, "store")), "KIP_HOME": str(Path(tmp, "home")),
               "FAKE_B_URL": "https://b.test", "FAKE_API_TOKEN": "supersecret123",
               "FAKE_SINK_LOG": str(sink_log), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "KIP_DASHBOARD": "0"}
        for v in ("LINEAR_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "CLAUDE_CODE_SESSION_ID"):
            env.pop(v, None)
        Path(tmp, "home").mkdir()
        Path(tmp, "home", "config.json").write_text(json.dumps({  # this machine's setup isn't the test's; skill gates have their own test
            "setup": None, "workflow": {"skills": None}, "hosts": {"cursor": {"logs": [str(Path(tmp, "cursor-logs"))]}}}))

        def sh(*args, ok=True, extra_env=None, cwd=repo):
            r = subprocess.run(args, cwd=cwd, env={**env, **(extra_env or {})}, capture_output=True, text=True)
            assert (r.returncode == 0) == ok, f"{args}: exit {r.returncode}\n{r.stdout}{r.stderr}"
            return (r.stdout + r.stderr).strip()

        def rc(*args, **kw):
            return sh(sys.executable, str(SCRIPT), *args, **kw)

        def run_json(run):
            return json.loads(rc("json", "--run", str(run)))

        def kipdb(sql, *params):
            with sqlite3.connect(Path(tmp, "store", "kip.db")) as con:
                return con.execute(sql, params).fetchall()

        (repo / "app.py").write_text('print("hi")\n')
        (repo / ".kip" / "sinks").mkdir(parents=True)
        (repo / ".kip" / "sinks" / "fake.py").write_text(FAKE_SINK)
        (repo / ".kip" / "config.json").write_text(json.dumps(PROJECT_CONFIG))
        sh("git", "init", "-q")
        sh("git", "add", ".")
        sh("git", "commit", "-qm", "init")

        # Local by default; cloud sinks are opt-in, need an issue; unknown sinks are refused.
        assert "--issue is required" in rc("init", "--sink", "fake", ok=False)
        assert "unknown sink nope" in rc("init", "--sink", "nope", ok=False)
        sinks = rc("sinks")
        assert "local" in sinks and "fake       ready" in sinks and "linear     missing LINEAR_API_KEY" in sinks, sinks
        assert "fake-a     ready (recommended)" in sinks and "tracker    agent-confirm via tracker-mcp (mcp)" in sinks, sinks
        opted = Path(rc("init", "--sink", "fake", "--issue", "ENG-9"))
        assert run_json(opted)["sinks"] == ["local", "fake"]

        run = Path(rc("init", "--level", "full"))
        rec = run_json(run)
        assert rec["sinks"] == ["local"] and rec["issue"] is None, rec
        assert rc("claim", "app prints hi") == "c1"
        rc("claim", "secret is redacted")
        rc("claim", "pdf renders")
        rc("claim", "red check that can't fail")
        rc("claim", "missing binary")
        rc("claim", "billing claim at ultra floor", "--level", "ultra")

        # Flags before `--` belong to receipt.py, not the wrapped command.
        assert "pass" in rc("run", "c1", "--red", "--", sys.executable, "-c", "raise SystemExit(1)")
        assert "pass" in rc("run", "c1", "--", sys.executable, "app.py")
        (repo / "app.py").write_text('print("hi!")\n')
        rc("run", "c2", "--", "sh", "-c", "echo token=$FAKE_API_TOKEN")
        rc("unverified", "c3", "needs PDF service")
        rc("run", "c4", "--red", "--", "true")
        assert "FAIL" in rc("run", "c5", "--", "no-such-binary-kip")
        rc("run", "c6", "--", "true")

        shot = Path(tmp, "shot.png")
        shot.write_bytes(b"\x89PNG fake")
        rc("attach", "c1", str(shot))

        # Evidence speaks for the code it ran on: app.py changed after c1 passed, so c1 is stale until re-run.
        assert "| c1 | app prints hi | generic |" in rc("render") and "⚠️ stale" in rc("render")
        rc("run", "c1", "--", sys.executable, "app.py")
        md = rc("render")
        assert "| c1 | app prints hi | generic |" in md and "✅ verified" in md, md
        assert "⚠️ unverified: needs PDF service" in md, md
        assert "❌ check can't fail" in md, md
        assert "❌ refuted" in md and "exit 127" in md, md
        assert "| c6 | billing claim at ultra floor | generic | `true` (exit 0) | ⚠️ falsifiability not shown |" in md, md
        assert "`c1f1-shot.png` (sha256" in md and "not uploaded" in md, md
        assert md.endswith(f"`kip-receipt {run.name}`"), md
        assert md == rc("render"), "render is not deterministic"
        assert "| # | Claim | Provider | Evidence | Verdict |" in md, "unplanned runs grew a Unit column"
        assert "only an unverified claim" in rc("accept", "c1", ok=False)
        rc("accept", "c3", "--reason", "no PDF service here")
        assert json.loads(rc("json"))["claims"][2]["accepted"] == "no PDF service here"

        jira = rc("render", "--sink", "jira")
        assert jira.startswith("h3. Receipts") and "{{c1f1-shot.png}}" in jira and "not uploaded" in jira, jira

        log = (run / "files" / "c2e1.log").read_text()
        assert "supersecret123" not in log and "[FAKE_API_TOKEN]" in log, log
        assert (run / "files" / "c2e1.patch").exists(), "dirty tree not captured"

        # Local-only run: publish stays local until the run opts into a sink.
        assert f"local only: {run}" in rc("publish")
        assert not sink_log.exists(), "local-only publish touched a sink"
        assert "--issue is required" in rc("publish", "--sink", "fake", ok=False)
        assert run_json(run)["sinks"] == ["local"], "failed opt-in was saved"

        # Opt in after the fact: fake posts, linear is skipped for missing env, so the command fails overall.
        out = rc("publish", "--sink", "fake", "--sink", "linear", "--issue", "ENG-1", ok=False)
        assert "fake: posted https://fake.test/ENG-1#cm1" in out and "linear: skipped, missing LINEAR_API_KEY" in out, out
        assert "start a new run" in rc("publish", "--sink", "fake", "--issue", "OTHER-2", ok=False)
        out = rc("publish", "--sink", "fake", ok=False)  # keeps the run's issue without repeating it
        assert "fake: updated" in out, out
        events = json.loads(sink_log.read_text())
        assert [e[0] for e in events] == ["upload", "create", "update"], events
        assert events[0][1:] == ["ENG-1", "c1f1-shot.png", "image/png"], events
        assert "![c1f1-shot.png](https://fake.test/c1f1-shot.png)" in events[1][2], events[1][2]
        assert events[1][2] == rc("render", "--sink", "fake"), "posted body differs from render output"
        rec = run_json(run)
        assert rec["published"]["fake"] == {"comment_id": "cm1", "url": "https://fake.test/ENG-1#cm1"}, rec

        # Two instances of one module publish with their own settings (a literal, an aliased env var).
        out = rc("publish", "--sink", "fake-a", "--sink", "fake-b", ok=False)
        assert "fake-a: posted https://a.test/ENG-1#cm1" in out and "fake-b: posted https://b.test/ENG-1#cm1" in out, out
        assert "fake-b: skipped, missing FAKE_B_URL" in rc("publish", ok=False, extra_env={"FAKE_B_URL": ""})

        # Agent-backed: nothing is recorded until the agent posts and records it; then publish updates it.
        out = rc("publish", "--sink", "tracker", ok=False)
        assert "tracker: agent-backed via tracker-mcp: post a new comment on ENG-1" in out, out
        assert "tracker" not in run_json(run)["published"]
        assert "isn't one of this run's sinks" in rc("record", "--sink", "nope", "--url", "u", ok=False)
        rc("record", "--sink", "tracker", "--url", "https://t.test/ENG-1#c9", "--comment-id", "c9")
        assert "tracker: agent-backed via tracker-mcp: update comment c9 on ENG-1" in rc("publish", ok=False)
        assert rc("render", "--sink", "tracker") == rc("render"), "agent-backed render differs from markdown"
        assert "unknown format rtf" in rc("render", "--sink", "weird", ok=False)
        rec = run_json(run)
        assert "linear" not in rec["published"] and rec["sinks"][:3] == ["local", "fake", "linear"], rec
        assert rec["issue"] == "ENG-1", rec

        # Desk test a remote site someone else built: per-viewport evidence, collected media,
        # remote marking instead of pinning this checkout's commit, and the spec hash-pinned.
        pw = Path(tmp, "playwright")
        pw.write_text(FAKE_PLAYWRIGHT)
        pw.chmod(0o755)
        remote = Path(rc("init", "--target", "https://staging.example.test", "--level", "ultra"))
        assert (remote / "specs").is_dir()
        rc("claim", "signup works on every viewport", "--provider", "frontend")
        spec = remote / "specs" / "c1.spec.ts"
        spec.write_text("// spec\n")
        dt = sh(sys.executable, str(DESKTEST), "c1", "https://broken.example.test", str(spec),
                ok=False, extra_env={"KIP_PLAYWRIGHT": str(pw)})
        assert "mobile   c1e1: exit 1" in dt and "desktop  c1e3: exit 0" in dt, dt
        sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(spec), "--viewports", "mobile",
           extra_env={"KIP_PLAYWRIGHT": str(pw)})
        # Falsifiability without having made the change: the broken baseline must fail on mobile.
        assert "mobile   c1e5: exit 1 (expected failure) -> pass" in sh(
            sys.executable, str(DESKTEST), "c1", "https://broken.example.test", str(spec), "--red",
            "--viewports", "mobile", extra_env={"KIP_PLAYWRIGHT": str(pw)})
        rec = run_json(remote)
        ev, files = rec["claims"][0]["evidence"], rec["claims"][0]["files"]
        assert [(e["phase"], e["passed"]) for e in ev] == \
            [("green", False), ("green", True), ("green", True), ("green", True), ("red", True)], ev
        assert all(e["remote"] and e["commit"] is None and e["patch"] is None for e in ev), ev
        assert ev[0]["command"][:2] == ["env", "BASE_URL=https://broken.example.test"], ev[0]["command"]
        assert "--project=mobile" in ev[0]["command"] and "KIP_LEVEL=ultra" in ev[0]["command"], ev[0]["command"]
        inputs = {i["path"]: i["inside_run"] for i in ev[0]["inputs"]}
        ran = hashlib.sha256(b"// spec\n").hexdigest()[:12]
        snap = f"specs/c1-{ran}.spec.ts"  # the spec as it ran, kept by its content
        assert inputs.get(snap) is True and inputs.get(str(pw.resolve())) is False, inputs
        names = [Path(f["path"]).name for f in files]
        assert "c1f1-c1e1-signup-mobile-test-finished-1.png" in names, names
        assert sorted(f["type"] for f in files) == ["image"] * 5 + ["video"] * 5, files
        md = rc("render", "--run", str(remote))
        assert md.startswith("### Receipts · https://staging.example.test (remote: state not pinned) · ultra"), md
        assert "· remote · log sha256" in md and "commit `" not in md, md
        table_row = next(line for line in md.splitlines() if line.startswith("| c1 |"))
        assert "desktest mobile https://broken.example.test (exit 1)<br>" in table_row, table_row
        assert "`env " not in table_row, "full commands belong in the details, not the table"
        assert "- desktest mobile https://broken.example.test: `env BASE_URL=https://broken.example.test" in md, md
        # A desk test is one check per spec and viewport, whatever its URL: mobile failed on one host and
        # passed on another, and nothing pins either host's code.
        assert "⚠️ passed after failing, code not pinned" in md, md
        # Viewports come from config: a project that only cares about mobile drops the others.
        (repo / ".kip" / "config.json").write_text(json.dumps(
            {**PROJECT_CONFIG, "evidence": {"frontend": {"viewports": {"tablet": None, "desktop": None}}}}))
        dt = sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(spec),
                extra_env={"KIP_PLAYWRIGHT": str(pw)})
        assert dt.startswith("mobile ") and len(dt.splitlines()) == 1, dt
        cmd = run_json(remote)["claims"][0]["evidence"][-1]["command"]
        assert any(c.startswith('KIP_VIEWPORTS={"mobile":{"browserName":"chromium"') for c in cmd), cmd
        assert "unknown viewport watch" in sh(sys.executable, str(DESKTEST), "c1", "u", str(spec), "--viewports",
                                              "watch", ok=False, extra_env={"KIP_PLAYWRIGHT": str(pw)})
        # Editing the spec after a run leaves its evidence alone (the next run keeps its own copy); altering a
        # copy the evidence ran is tampering.
        spec.write_text("// edited after the run\n")
        rc("render", "--run", str(remote))
        sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(spec), extra_env={"KIP_PLAYWRIGHT": str(pw)})
        assert len(list((remote / "specs").glob("c1-*.spec.ts"))) == 2
        sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(remote / snap), "--viewports", "mobile",
           extra_env={"KIP_PLAYWRIGHT": str(pw)})  # re-running a copy runs that copy
        assert len(list((remote / "specs").glob("c1-*.spec.ts"))) == 2
        # Re-running a desk test on changed code refreshes its claim: the folder its media lands in isn't the check.
        desk_repo = Path(tmp, "desk-fresh")
        desk_repo.mkdir()
        sh("git", "init", "-q", cwd=desk_repo)
        (desk_repo / "a.txt").write_text("1\n")
        sh("git", "add", ".", cwd=desk_repo)
        sh("git", "commit", "-qm", "a", cwd=desk_repo)
        local = Path(sh(sys.executable, str(SCRIPT), "init", cwd=desk_repo))
        sh(sys.executable, str(SCRIPT), "claim", "desk test stays fresh", cwd=desk_repo)
        (local / "specs" / "c1.spec.ts").write_text("// fresh\n")
        for code in ("1\n", "2\n"):
            (desk_repo / "a.txt").write_text(code)
            sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(local / "specs" / "c1.spec.ts"),
               "--viewports", "mobile", cwd=desk_repo, extra_env={"KIP_PLAYWRIGHT": str(pw)})
        row = next(x for x in sh(sys.executable, str(SCRIPT), "render", cwd=desk_repo).splitlines() if x.startswith("| c1 |"))
        assert "✅ verified" in row, row
        outside = Path(tmp, "e2e", "c1.spec.ts")  # a project's own spec runs where it is, relative imports and all
        outside.parent.mkdir()
        outside.write_text("// project spec\n")
        sh(sys.executable, str(DESKTEST), "c1", "https://staging.example.test", str(outside), "--viewports", "mobile",
           extra_env={"KIP_PLAYWRIGHT": str(pw)})
        assert str(outside.resolve()) in run_json(remote)["claims"][0]["evidence"][-1]["command"]
        (remote / snap).write_text("// tampered\n")
        assert f"hash mismatch for {snap}" in rc("render", "--run", str(remote), ok=False)

        # Gate: kip-decompose plans claims into a run; host hooks block finishing once work has
        # landed while a claim is unaddressed, and block opening a PR while one is refuted.
        def check(*args, event=None, cwd=repo, extra_env=None):
            r = subprocess.run([sys.executable, str(SCRIPT), "check", *args], cwd=cwd, env={**env, **(extra_env or {})},
                               input=None if event is None else json.dumps(event), capture_output=True, text=True)
            return r.returncode, (r.stdout + r.stderr).strip()

        assert check(extra_env={"KIP_RECEIPTS_DIR": str(Path(tmp, "empty"))}) == (0, "check: no receipt run for this branch")
        plan = Path(rc("init", "--level", "full"))
        assert rc("claim", "app prints hi!", "--unit", "1") == "c1"
        rc("claim", "app runs on the device", "--unit", "2", "--provider", "api")
        assert run_json(plan)["claims"][1]["unit"] == "2"
        md = rc("render")
        assert "| # | Unit | Claim | Provider | Evidence | Verdict |" in md and "| c2 | 2 | app runs on the device |" in md, md
        assert "||#||Unit||Claim||" in rc("render", "--sink", "jira")
        assert check("--hook", "stop", event={}) == (0, "check: no code changed since the run started")

        (repo / "app.py").write_text('print("hi!!")\n')
        code, out = check("--hook", "stop", event={"cwd": str(repo)}, cwd=tmp)
        assert code == 2 and "c1 ⚠️ unverified: app prints hi!" in out and "c2 ⚠️ unverified" in out, out
        assert check("--hook", "stop", event={"stop_hook_active": True})[0] == 0, "stop hook could loop"
        # A session starts knowing where this branch's run stands.
        code, out = check("--hook", "start", event={"session_id": "s0", "cwd": str(repo)}, cwd=tmp)
        assert code == 0 and "has run" in out and "c1 ⚠️ unverified: app prints hi!" in out, out
        # ...and is reminded of setup a person still has to do, with how to skip it.
        Path(tmp, "home2").mkdir()
        Path(tmp, "home2", "config.json").write_text(json.dumps(
            {"setup": {"demo": {"about": "a demo step", "requires": {"file": [str(Path(tmp, "nope"))]}, "run": "do-it"}}}))
        out = check("--hook", "start", event={"session_id": "s0s", "cwd": str(repo)}, cwd=tmp,
                    extra_env={"KIP_HOME": str(Path(tmp, "home2"))})[1]
        assert "kip setup: `demo` isn't done (a demo step)" in out and "run `do-it`" in out and "--skip demo" in out, out
        code, out = check("--hook", "start", event={"session_id": "s0c", "workspace_roots": [str(repo)]}, cwd=tmp)
        assert "c1 ⚠️ unverified" in json.loads(out)["additional_context"], out
        # Cursor's events carry workspace_roots: same gate, answered in JSON. Stop asks for one follow-up.
        cursor = {"workspace_roots": [str(repo)], "loop_count": 0}
        code, out = check("--hook", "stop", event=cursor, cwd=tmp)
        assert code == 0 and "c1 ⚠️ unverified" in json.loads(out)["followup_message"], out
        assert check("--hook", "stop", event={**cursor, "loop_count": 1}) == (0, "{}"), "cursor stop could loop"
        assert check("--hook", "stop", event={**cursor, "status": "aborted"}) == (0, "{}"), "re-prompted a user's stop"
        assert check("--hook", "pr", event={"tool_input": {"command": "git commit -m x"}})[0] == 0
        assert check("--hook", "pr", event={"tool_input": {"command": "gh pr create --fill"}})[0] == 2
        pr_cmd = "gh pr " + "create"  # spelled apart so this file's own edits don't trip the gate
        assert check("--hook", "pr", event={"tool_input": {"command": f"git push &&\n  {pr_cmd} --fill"}})[0] == 2
        assert check("--hook", "pr", event={"tool_input": {"command": f"echo '{pr_cmd}'"}})[0] == 0, "text isn't a PR"
        # Opening a PR without gh pr create: a POST to /pulls through gh api, or an MCP tool.
        gh_api = "gh " + "api repos/o/r/pulls"
        assert check("--hook", "pr", event={"tool_input": {"command": f"{gh_api} -f title=x -f head=b"}})[0] == 2
        assert check("--hook", "pr", event={"tool_input": {"command": f"cd x && {gh_api} --method POST --input b"}})[0] == 2
        assert check("--hook", "pr", event={"tool_input": {"command": gh_api}})[0] == 0, "listing PRs isn't opening one"
        assert check("--hook", "pr", event={"tool_input": {"command": f"{gh_api}/7/comments -f body=x"}})[0] == 0
        assert check("--hook", "pr", event={"tool_name": "mcp__github__create_pull_request", "tool_input": {}})[0] == 2
        assert check("--hook", "pr", event={"tool_name": "mcp__github__get_pull_request", "tool_input": {}})[0] == 0
        assert check("--hook", "pr", event={"tool_name": "mcp__github__create_pull_request_review"})[0] == 0
        gql = "gh " + "api graphql -f query='mutation { createPullRequest(input: {}) { pullRequest { url } } }'"
        assert check("--hook", "pr", event={"tool_input": {"command": gql}})[0] == 2
        code, out = check("--hook", "pr", event={**cursor, "tool_name": "create_pull_request", "tool_input": {}}, cwd=tmp)
        assert code == 0 and json.loads(out)["permission"] == "deny", out
        # No run on this branch: another branch's run is never used, a PR is blocked once the branch
        # changed something, and a session that changed code since it started is nudged at stop.
        sh("git", "checkout", "-q", "-b", "elsewhere")
        assert "no receipt run for this branch" in rc("claim", "x", ok=False)
        assert check("--hook", "stop", event={}) == (0, "check: no receipt run for this branch")
        code, out = check("--hook", "pr", event={"tool_input": {"command": "gh pr create"}})
        assert code == 2 and "this branch has changes but no receipts run" in out, out
        sh("git", "stash", "-q")
        assert check("--hook", "pr", event={"tool_input": {"command": "gh pr create"}})[0] == 0, "clean branch blocked"
        sh("git", "stash", "pop", "-q")
        session = {"session_id": "s1", "cwd": str(repo)}
        assert check("--hook", "start", event=session) == (0, "")
        assert check("--hook", "stop", event=session)[0] == 0, "an unchanged session was nudged"
        (repo / "new.txt").write_text("new\n")  # a new, untracked file is a change too
        code, out = check("--hook", "stop", event=session)
        assert code == 2 and "this session changed code" in out, out
        assert check("--hook", "stop", event={**session, "stop_hook_active": True})[0] == 0
        check("--hook", "start", event=session)  # a resumed session keeps its first baseline
        assert check("--hook", "stop", event=session)[0] == 2
        (repo / "new.txt").unlink()
        sh("git", "checkout", "-q", "-")

        rc("run", "c1", "--", "false")
        rc("unverified", "c2", "needs the device")
        assert check("--hook", "stop", event={})[0] == 0, "refuted claims are reported, not blocked, at stop"
        code, out = check("--hook", "pr", event={"tool_input": {"command": "gh pr create"}})
        assert code == 2 and "c1 ❌ refuted" in out and "  c2 " not in out, out
        assert "tidy: no claim" in out and "review: no claim" in out, "workflow stages aren't required"
        code, out = check("--hook", "pr", event={**cursor, "cwd": str(repo), "command": "gh pr create --fill"}, cwd=tmp)
        assert code == 0 and json.loads(out)["permission"] == "deny", out
        assert "c1 ❌ refuted" in json.loads(out)["agent_message"], out
        assert check("--hook", "pr", event={**cursor, "command": "git status"}) == (0, '{"permission": "allow"}')

        # Hooks log to the event log: gate decisions with where they happened, and every tool call (no git,
        # never blocking, redacted and cut short). Claude Code's PR hook on a plain shell call leaves that
        # to its tool hook; Cursor's is its only view of the call.
        events = Path(tmp, "store", "events")
        today = datetime.datetime.now(datetime.timezone.utc).date()
        lines = lambda: [x for p in sorted(events.glob("*.jsonl")) for x in p.read_text().splitlines()]
        last = lambda: json.loads(lines()[-1])
        dash = {"session_id": "d1", "cwd": str(repo)}
        for hook, event in (("start", dash), ("stop", dash)):
            n = len(lines())
            assert check("--hook", hook, event=event)[0] == 0 and len(lines()) == n + 1, f"{hook} wasn't logged"
        assert {k: last()[k] for k in ("session", "host", "repo", "branch", "hook", "blocked")} == \
            {"session": "d1", "host": "claude", "repo": "demo", "branch": sh("git", "branch", "--show-current"),
             "hook": "stop", "blocked": False} and last()["run"] and last()["at"], last()
        assert last().get("github") is None, "a checkout with no GitHub remote got a GitHub link"
        for remote in ("git@github.com:me/demo.git", "https://github.com/me/demo"):
            sh("git", "remote", "add", "origin", remote)
            check("--hook", "stop", event=dash)
            assert last()["github"] == "https://github.com/me/demo", (remote, last())
            sh("git", "remote", "remove", "origin")
        tools = [("Skill", {"skill": "kip-decompose"}), ("Edit", {"file_path": "app.py"}),
                 ("Skill", {"skill": "kip-marie-kondo"}), ("Bash", {"command": "python3 receipt.py run c1 -- true"})]
        for name, tool_input in tools:
            assert check("--hook", "tool", event={**dash, "tool_name": name, "tool_input": tool_input},
                         extra_env={"PATH": ""}) == (0, ""), "the tool hook spoke, blocked, or needed git"
        assert {k: last()[k] for k in ("session", "hook", "tool", "input")} == \
            {"session": "d1", "hook": "tool", "tool": "Bash", "input": "python3 receipt.py run c1 -- true"}, last()
        check("--hook", "tool", event={**dash, "tool_name": "Bash", "tool_input": {"command": "echo supersecret123 " + "x" * 300}})
        assert last()["input"].startswith("echo [FAKE_API_TOKEN] x") and len(last()["input"]) == 200, last()
        n = len(lines())
        check("--hook", "pr", event={**dash, "tool_input": {"command": "ls"}})
        check("--hook", "pr", event={**cursor, "conversation_id": "k1", "command": "git status"})
        assert len(lines()) == n, "a shell call was logged by the PR hook as well as the tool hook"
        blocked = [json.loads(x) for x in lines() if '"s1"' in x and '"stop"' in x][-1]
        assert blocked["blocked"] and "this session changed code" in blocked["message"], blocked
        rc("claim", "handles the no claim case ❌")  # its text never decides its kind
        with open(events / f"{today}.jsonl", "a") as f:  # lines it can't use are skipped, not fatal
            f.write('not json\n{"session": "old"}\n[1]\n{"session": 7, "at": "2026-01-01T00:00:00+00:00"}\n')
            for blocked in (True, False):  # only a PR the gate let through counts as opened
                f.write(json.dumps({"session": f"pr-{blocked}", "at": f"{today}T00:00:00+00:00", "hook": "pr",
                                    "blocked": blocked}) + "\n")
        assert not Path(tmp, "dashboard.html").exists(), "a hook wrote a static page"

        # The dashboard server: the page, sessions with their stage, one session's events, and a stream
        # that pushes each new event to open pages; it exits once idle with no page open.
        def free_port():
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]

        def get(port, path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                return r.read().decode()

        def wait_until(ok, seconds):
            end = time.time() + seconds
            while time.time() < end:
                if ok():
                    return True
                time.sleep(0.1)
            return False

        def listening(port):
            with socket.socket() as s:
                return s.connect_ex(("127.0.0.1", port)) == 0

        day = lambda n: events / f"{today - datetime.timedelta(days=n)}.jsonl"
        day(20).write_text(json.dumps({"session": "ancient", "at": "2000-01-01T00:00:00+00:00"}) + "\n")
        day(1).write_text(json.dumps({"session": "yesterday", "at": f"{today}T00:00:00+00:00", "repo": "old"}) + "\n")
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "2"],
                                  env=env, stdout=subprocess.PIPE, text=True)
        assert server.stdout.readline().strip() == f"kip dashboard: http://127.0.0.1:{port}"
        assert "EventSource" in get(port, "/") and "/stream" in get(port, "/")
        rows = {r["session"]: r for r in json.loads(get(port, "/api/sessions"))}
        d1 = rows["d1"]
        assert d1["stage"] == "receipts" and d1["reached"] == ["decompose", "implement", "tidy", "receipts"], d1
        assert d1["skills"] == ["kip-decompose", "kip-marie-kondo"] and d1["tool"].startswith("Bash: echo"), d1
        assert {k: len(v) for k, v in d1["open"].items()} == \
            {"failing check": 1, "missing stage": 2, "unaddressed": 1} and d1["active"], d1["open"]
        assert rows["s1"]["blocked"] == 2 and rows["s1"]["hook"] == "stop" and "7" in rows and "old" not in rows, rows
        assert rows["pr-False"]["pr"] and not rows["pr-True"]["pr"] and not d1["pr"], rows
        assert all("asking" in x for x in d1["runs"]), d1["runs"]
        assert get(port, "/kip-working.svg").startswith("<svg") and get(port, "/kip-asking.svg").startswith("<svg")
        try:
            get(port, "/kip-build.svg")
            raise AssertionError("served a mascot state that isn't one")
        except urllib.error.HTTPError as err:
            assert err.code == 404, err
        assert "yesterday" in rows and "ancient" not in rows and not day(20).exists() and day(1).exists(), \
            "kept the wrong days"
        assert [e["hook"] for e in json.loads(get(port, "/api/sessions/d1"))][:2] == ["start", "stop"]
        assert json.loads(get(port, "/api/sessions/nobody")) == []
        answers = []  # bursts of requests: they share the server's kip.db connection and its listen backlog
        for _ in range(5):
            pages = [threading.Thread(target=lambda: answers.append(get(port, "/api/sessions"))) for _ in range(50)]
            for t in pages:
                t.start()
            for t in pages:
                t.join()
        assert len(answers) == 250 and len(set(answers)) == 1, f"{250 - len(answers)} of 250 requests failed or disagreed"
        rebound = urllib.request.Request(f"http://127.0.0.1:{port}/api/sessions", headers={"Host": "evil.test"})
        try:
            urllib.request.urlopen(rebound, timeout=5)
            raise AssertionError("served sessions to another host name")
        except urllib.error.HTTPError as err:
            assert err.code == 403, err
        stream, got = urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=5), queue.Queue()
        def read_stream():
            try:
                for x in stream:
                    got.put((time.time(), x))
            except OSError:  # closed below
                pass
        threading.Thread(target=read_stream, daemon=True).start()
        time.sleep(0.5)
        sent = time.time()
        check("--hook", "tool", event={**dash, "tool_name": "Read", "tool_input": {"file_path": "streamed.py"}})
        at, line = got.get(timeout=5)
        assert line.startswith(b"data: ") and json.loads(line[6:])["input"] == "streamed.py", line
        assert at - sent < 1, f"streamed after {at - sent:.2f}s"
        time.sleep(3)
        assert server.poll() is None, "exited while a page was open"
        stream.close()
        assert wait_until(lambda: server.poll() is not None, 15), "an idle server kept running"

        # A start hook launches the server when nothing listens, once, and not at all with KIP_DASHBOARD=0.
        port = free_port()
        auto = {"KIP_DASHBOARD": "1", "KIP_DASHBOARD_PORT": str(port), "KIP_DASHBOARD_IDLE": "2",
                "CLAUDE_CODE_SESSION_ID": "s0"}  # the shared server must not take the session's id
        check("--hook", "start", event=dash, extra_env={**auto, "KIP_DASHBOARD": "0"})
        assert not wait_until(lambda: listening(port), 2), "started with KIP_DASHBOARD=0"
        check("--hook", "start", event=dash, extra_env=auto)
        assert wait_until(lambda: listening(port), 10), "the start hook didn't launch the server"
        records = Path(tmp, "store", "servers")
        assert not list(records.glob("*.json")), "the start hook's shared server took the session's id"
        check("--hook", "start", event=dash, extra_env=auto)
        time.sleep(1)
        servers = subprocess.run(["pgrep", "-f", f"serve --port {port}"], capture_output=True, text=True).stdout.split()
        assert len(servers) == 1, servers
        assert wait_until(lambda: not listening(port), 20), "the launched server never went idle"

        # A session's end stops the dashboards it started, and only those; the start hook's shared one is no one's.
        owned = {}
        for who in ("s1", "s2", None):
            p = free_port()
            owned[who] = (p, subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(p), "--idle", "0"],
                                              env={**{k: v for k, v in env.items() if k != "CLAUDE_CODE_SESSION_ID"},
                                                   **({"CLAUDE_CODE_SESSION_ID": who} if who else {})},
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            assert wait_until(lambda: listening(p), 10), who
        assert sorted(json.loads(f.read_text())["session"] for f in records.iterdir()) == ["s1", "s2"]
        decoy = subprocess.Popen(["sleep", "30"])  # a record whose pid is no longer a dashboard
        (records / f"{decoy.pid}.json").write_text(json.dumps({"session": "s1"}))
        assert check("--hook", "end", event={"session_id": "s1"}) == (0, "")
        assert wait_until(lambda: owned["s1"][1].poll() is not None, 10), "the ending session's server kept running"
        assert listening(owned["s2"][0]) and listening(owned[None][0]), "stopped another session's server"
        assert decoy.poll() is None, "killed a process that isn't a dashboard"
        assert [json.loads(f.read_text())["session"] for f in records.iterdir()] == ["s2"]
        decoy.kill()
        for _, server_ in owned.values():
            server_.terminate()
            server_.wait()
        idle = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(free_port()), "--idle", "1"],
                                env={**env, "CLAUDE_CODE_SESSION_ID": "s3"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: any("s3" in f.read_text() for f in records.iterdir()), 10)
        assert idle.wait(20) == 0 and not any("s3" in f.read_text() for f in records.iterdir()), "an idle exit left its record"
        assert not list(records.iterdir()), "a stopped server left its record"
        # A log that can't be written changes nothing about what the gate says.
        gate_pr = {"tool_input": {"command": "gh pr create"}}
        before = check("--hook", "pr", event=gate_pr)
        events.rename(events.with_suffix(".bak"))
        events.write_text("a file where the log's folder should be\n")
        assert check("--hook", "pr", event=gate_pr) == before and before[0] == 2, before
        events.unlink()
        events.with_suffix(".bak").rename(events)

        # Skills with no skill hook: a prompt's `/name` (when a skill folder has it, or it's a stage's skill) or
        # attached SKILL.md, and a tool reading a SKILL.md, count as using that skill. Editing one doesn't.
        # Cursor gets JSON answers and one session id across its events. Prompts and replies are kept, redacted.
        home = {"HOME": str(Path(tmp, "nohome"))}
        (repo / ".claude" / "skills" / "my-skill").mkdir(parents=True)
        (repo / ".claude" / "skills" / "my-skill" / "SKILL.md").write_text("---\nname: my-skill\n---\n")
        prompt = {"session_id": "p1", "cwd": str(repo),
                  "prompt": "/kip-decompose this, see /help, then /my-skill; not /code-review/notes.md"}
        assert check("--hook", "prompt", event=prompt, extra_env=home) == (0, "")
        assert [(e["session"], e["tool"], e["input"]) for e in map(json.loads, lines()[-2:])] == \
            [("p1", "Skill", "kip-decompose"), ("p1", "Skill", "my-skill")], lines()[-2:]
        said = json.loads(lines()[-3])
        assert (said["hook"], said["text"]) == ("prompt", prompt["prompt"]), said
        # A repo's config can turn prompts and replies off; skills still count.
        cfg_path = repo / ".kip" / "config.json"
        kept = cfg_path.read_text()
        cfg_path.write_text(json.dumps({**json.loads(kept), "dashboard": {"conversation": False}}))
        check("--hook", "prompt", event={**prompt, "prompt": "quiet /kip-decompose"}, extra_env=home)
        check("--hook", "stop", event={"session_id": "p1", "cwd": str(repo), "last_assistant_message": "quiet reply"})
        assert "quiet" not in "".join(lines()) and json.loads(lines()[-2])["input"] == "kip-decompose", lines()[-2:]
        cfg_path.write_text(kept)
        assert "code-review" not in "".join(lines()[-2:]), "a path in the prompt counted as a skill"
        check("--hook", "tool", event={"session_id": "p1", "tool_name": "Edit",
                                       "tool_input": {"file_path": str(repo / ".claude/skills/my-skill/SKILL.md")}})
        k2 = {**cursor, "conversation_id": "k2"}
        assert check("--hook", "start", event={**k2, "session_id": "cursor-session"})[0] == 0
        check("--hook", "start", event={**cursor, "conversation_id": "k0", "workspace_roots": []})  # an empty composer
        assert check("--hook", "prompt", event={**k2, "prompt": "/nothing-here", "attachments": [
            {"type": "file", "file_path": "/u/.cursor/skills/kip-marie-kondo/SKILL.md"}]}, extra_env=home) == \
            (0, '{"continue": true}')
        assert check("--hook", "tool", event={**k2, "hook_event_name": "preToolUse", "tool_name": "Read",
                                              "tool_input": {"file_path": "/u/.cursor/skills/kip-receipts/SKILL.md"}}) == (0, "{}")
        assert check("--hook", "response", event={**k2, "text": "Done, key sk-test-12345678."},
                     extra_env={"MY_API_KEY": "sk-test-12345678"}) == (0, "{}")
        assert json.loads(lines()[-1])["text"] == "Done, key [MY_API_KEY].", lines()[-1]
        check("--hook", "stop", event={**k2, "status": "completed"})
        assert json.loads(lines()[-1])["hook"] == "stop", "Cursor's stop logged a reply it doesn't carry"
        check("--hook", "response", event={**k2, "text": "A reply that lands after the stop."})
        check("--hook", "stop", event={"session_id": "p1", "cwd": str(repo), "last_assistant_message": "All set."})
        assert [(e["hook"], e.get("text")) for e in map(json.loads, lines()[-2:])] == \
            [("response", "All set."), ("stop", None)], lines()[-2:]
        # A tool call waiting on approval: Claude Code's hook logs it; Cursor's renderer log notes it, read live.
        assert check("--hook", "ask", event={"session_id": "p1", "cwd": str(repo), "tool_name": "Bash",
                                             "tool_input": {"command": "docker start redis"}}) == (0, "")
        assert [json.loads(lines()[-1])[k] for k in ("hook", "tool", "input")] == ["ask", "Bash", "docker start redis"]
        renderer = Path(tmp, "cursor-logs", "20261026T090000", "window1", "renderer.log")
        renderer.parent.mkdir(parents=True)
        wake = 'x [ComposerWakelockManager] {} wakelock id=1 reason="{}" composerId={}\n'.format
        renderer.write_text(wake("Released", "user-approval-requested", "k2"))  # before the server: history
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port)], env=env,
                                  stdout=subprocess.PIPE, text=True)
        server.stdout.readline()
        try:  # a server left running holds the test's output open
            rows = {r["session"]: r for r in json.loads(get(port, "/api/sessions"))}
            assert rows["p1"]["hook"] == "ask", rows["p1"]
            with open(renderer, "a") as f:
                f.write(wake("Released", "user-approval-requested", "k2") + wake("Released", "user-approval-requested", "zz"))
            hook = lambda sid: {r["session"]: r for r in json.loads(get(port, "/api/sessions"))}.get(sid, {}).get("hook")
            assert wait_until(lambda: hook("k2") == "ask", 5), "Cursor's approval request didn't reach its session"
            with open(renderer, "a") as f:
                f.write(wake("Acquired", "agent-loop-resumed", "k2"))
            assert wait_until(lambda: hook("k2") == "resume", 5), "Cursor resuming after approval didn't reach its session"
            assert hook("zz") is None, "an approval for a session the log doesn't have made a row"
        finally:
            server.terminate()
        assert rows["p1"]["skills"] == ["kip-decompose", "my-skill"] and rows["p1"]["stage"] == "implement", rows["p1"]
        assert rows["k2"]["host"] == "cursor" and rows["k2"]["repo"] == "demo", rows
        assert rows["k2"]["hook"] == "stop", "a reply landing after Cursor's stop put it back mid-turn"
        assert "cursor-session" not in rows and "k0" not in rows, rows
        assert rows["k2"]["skills"] == ["kip-marie-kondo", "kip-receipts"] and rows["k2"]["stage"] == "receipts", rows["k2"]
        assert rows["k2"]["tool"] == "Read: /u/.cursor/skills/kip-receipts/SKILL.md", rows["k2"]

        # A PR passes once every stage has an addressed claim; a review finding is a claim, so a refuted one blocks.
        gh_create = {"tool_input": {"command": "gh pr create"}}
        rc("init")
        rc("claim", "tidy pass over the branch diff", "--stage", "tidy")
        rc("claim", "review: no open correctness findings", "--stage", "review")
        (repo / "app.py").write_text('print("hi!!!")\n')
        rc("run", "c1", "--", "true")
        rc("run", "c2", "--", "true")
        assert check("--hook", "pr", event=gh_create)[0] == 0
        rc("claim", "Fixed: app.py crashes on empty input", "--stage", "review")
        rc("run", "c3", "--", "false")
        code, out = check("--hook", "pr", event=gh_create)
        assert code == 2 and "c3 ❌ refuted" in out and "no claim" not in out, out

        # The github sink puts the ledger in a marked block of the PR body, keeping the author's text,
        # with a hidden gate line that CI reads with `check --pr`.
        bin_ = Path(tmp, "bin")
        bin_.mkdir()
        (bin_ / "gh").write_text(FAKE_GH)
        (bin_ / "gh").chmod(0o755)
        body = Path(tmp, "pr-body.md")
        body.write_text("My description")
        ghlog = Path(tmp, "gh.log")
        ghenv = {"PATH": f"{bin_}{os.pathsep}{env['PATH']}", "FAKE_PR_BODY": str(body), "FAKE_GH_LOG": str(ghlog),
                 "FAKE_PR_HEAD": sh("git", "rev-parse", "HEAD")}
        out = rc("check", "--pr", "7", ok=False, extra_env=ghenv)
        assert "PR 7 has no receipts ledger" in out, out
        assert "<!-- kip-gate {" in rc("render", "--sink", "github")
        assert "github: posted https://github.test/pull/7" in rc("publish", "--sink", "github", "--issue", "7", extra_env=ghenv)
        rc("publish", extra_env=ghenv)
        pr_body = body.read_text()
        assert pr_body.startswith("My description\n\n<!-- kip-receipt -->\n### Receipts") and pr_body.count("kip-gate") == 1, pr_body
        out = rc("check", "--pr", "7", ok=False, extra_env=ghenv)
        assert "c3 ❌ refuted: Fixed: app.py crashes on empty input" in out, out

        # GitHub has no upload API: publish names the files the agent uploads by browser and records
        # (kip-receipts-github); then the ledger embeds the image and puts the video alone on a line.
        for name in ("shot.png", "demo.mp4"):
            Path(tmp, name).write_bytes(b"x")
            rc("attach", "c3", str(Path(tmp, name)))
        out = rc("publish", extra_env=ghenv)
        assert "github: not uploaded: files/c3f1-shot.png, files/c3f2-demo.mp4" in out, out
        assert "no file nope.png" in rc("record", "--sink", "github", "--file", "nope.png", "--url", "u", ok=False)
        img, vid = (f"https://github.com/user-attachments/assets/{n}" for n in ("img1", "vid1"))
        rc("record", "--sink", "github", "--file", "c3f1-shot.png", "--url", img)
        rc("record", "--sink", "github", "--file", "files/c3f2-demo.mp4", "--url", vid)
        assert "not uploaded" not in rc("publish", extra_env=ghenv)
        pr_body = body.read_text()
        assert f"![c3f1-shot.png]({img})" in pr_body and f"\n\n{vid}\n" in pr_body and "[video:" not in pr_body, pr_body

        # The github-issue sink posts the ledger as one issue comment, like Linear, then updates that comment.
        assert "github-issue: posted https://github.test/issues/7#issuecomment-99" in \
            rc("publish", "--sink", "github-issue", extra_env=ghenv)
        assert "github-issue: updated" in rc("publish", extra_env=ghenv)
        calls = ghlog.read_text().splitlines()
        assert calls[0] == "api -X POST repos/{owner}/{repo}/issues/7/comments --input -", calls
        assert calls[2] == "api -X PATCH repos/{owner}/{repo}/issues/comments/99 --input -", calls
        assert "### Receipts" in json.loads(calls[3])["body"], calls[3]

        # Stages come from config, fixed into the run at init: a project that drops them doesn't need them.
        (repo / ".kip" / "config.json").write_text(json.dumps({**PROJECT_CONFIG, "workflow": {"stages": []}}))
        rc("init")
        rc("claim", "app prints hi!!!!")
        (repo / "app.py").write_text('print("hi!!!!")\n')
        rc("run", "c1", "--", "true")
        assert check("--hook", "pr", event=gh_create)[0] == 0
        rc("publish", "--sink", "github", "--issue", "7", extra_env=ghenv)
        assert rc("check", "--pr", "7", extra_env=ghenv).startswith("check: every claim in"), body.read_text()
        out = rc("check", "--pr", "7", ok=False, extra_env={**ghenv, "FAKE_PR_HEAD": "f" * 40})
        assert "ledger is for" in out and "but its head is fffffff" in out, out

        def verdicts(run_args=()):
            return {line.split(" | ")[0][2:]: line.split(" | ")[-1].rstrip(" |")
                    for line in rc("render", *run_args).splitlines() if line.startswith("| c")}

        # Stale evidence: work after a check reopens its claim at stop and before a PR; committing the
        # same code doesn't. An observation alone is self-reported until something is attached.
        home_branch = sh("git", "branch", "--show-current")
        sh("git", "checkout", "-q", "-b", "stale")
        rc("init")
        rc("claim", "app prints 5")
        rc("claim", "looks right in the browser")
        (repo / "app.py").write_text('print("5")\n')
        rc("run", "c1", "--", "grep", "-q", "5", "app.py")
        rc("observe", "c2", "--passed", "renders 5")
        assert verdicts()["c2"] == "⚠️ self-reported: an observation with nothing attached", verdicts()
        rc("attach", "c2", str(shot))
        assert verdicts()["c2"] == "✅ verified", verdicts()
        sh("git", "commit", "-qam", "5")
        assert verdicts()["c1"] == "✅ verified" and check("--hook", "stop", event={})[0] == 0, "a commit made it stale"
        (repo / "app.py").write_text('print("6")\n')
        code, out = check("--hook", "stop", event={})
        assert code == 2 and "c1 ⚠️ stale" in out and "c2 ⚠️ stale" in out, out
        code, out = check("--hook", "pr", event=gh_create)
        assert code == 2 and "c1 ⚠️ stale" in out, out

        # Required checks: workflow.checks become claims at init that only their own command settles,
        # and they hold stop and PR until they pass on the current code.
        sh("git", "checkout", "-q", "--", "app.py")
        sh("git", "checkout", "-q", "-b", "checks")
        (repo / ".kip" / "config.json").write_text(json.dumps(
            {**PROJECT_CONFIG, "workflow": {"stages": [], "checks": {"tests": "grep -q ok app.py"}}}))
        run = Path(rc("init"))
        assert run_json(run)["claims"][0]["command"] == ["sh", "-c", "grep -q ok app.py"]
        assert rc("claim", "app says ok") == "c2"
        assert "required `tests` check" in rc("run", "c1", "--", "true", ok=False)
        assert "required `tests` check" in rc("observe", "c1", "--passed", "ran it", ok=False)
        assert "required `tests` check" in rc("unverified", "c1", "no time", ok=False)
        (repo / "app.py").write_text('print("nope")\n')
        rc("run", "c2", "--", "true")
        code, out = check("--hook", "stop", event={})
        assert code == 2 and "c1 ⚠️ unverified: `tests` passes" in out and "  c2 " not in out, out
        assert "FAIL" in rc("run", "c1", cwd=repo / ".kip")  # from a subdirectory, still the same check at the root
        code, out = check("--hook", "stop", event={})
        assert code == 2 and "c1 ❌ refuted" in out, out
        (repo / "app.py").write_text('print("ok")\n')
        rc("run", "c1")
        rc("run", "c2", "--", "true")
        assert check("--hook", "stop", event={})[0] == 0 and check("--hook", "pr", event=gh_create)[0] == 0
        assert len({e["cwd"] for e in run_json(run)["claims"][0]["evidence"]}) == 1
        # Another checkout's code says nothing about this run's evidence.
        (repo / "app.py").write_text('print("changed")\n')
        assert "stale" in rc("render") and "stale" not in rc("render", "--run", str(run), cwd=tmp)
        sh("git", "checkout", "-q", "--", "app.py")
        (repo / "app.py").write_text('print("ok")\n')
        # A remote desk test doesn't inherit this checkout's required checks.
        remote_run = Path(rc("init", "--target", "https://staging.example.test"))
        assert run_json(remote_run)["claims"] == []
        shutil.rmtree(remote_run)
        for table in ("runs", "claims"):
            kipdb(f"DELETE FROM {table} WHERE {'id' if table == 'runs' else 'run'} = ?", remote_run.name)

        # A gate that crashes blocks (once per turn) instead of silently letting everything through.
        good = kipdb("SELECT data FROM runs WHERE id = ?", run.name)[0][0]
        kipdb("UPDATE runs SET data = '{' WHERE id = ?", run.name)
        code, out = check("--hook", "stop", event={})
        assert code == 2 and "the gate crashed (JSONDecodeError" in out, out
        assert check("--hook", "pr", event=gh_create)[0] == 2
        assert check("--hook", "stop", event={"stop_hook_active": True})[0] == 0, "a crashing gate could loop"
        assert check("--hook", "pr", event={"tool_input": {"command": "ls"}})[0] == 0, "a crash blocked a non-PR command"
        code, out = check("--hook", "start", event={})
        assert code == 0 and "the gate crashed" in out, out
        code, out = check("--hook", "stop", event=cursor, cwd=tmp)
        assert code == 0 and "the gate crashed" in json.loads(out)["followup_message"], out
        kipdb("UPDATE runs SET data = ? WHERE id = ?", good, run.name)
        sh("git", "checkout", "-q", "--", ".")
        sh("git", "checkout", "-q", home_branch)
        (repo / ".kip" / "config.json").write_text(json.dumps({**PROJECT_CONFIG, "workflow": {"stages": []}}))

        # Re-runs: the latest run of each check decides it. A failure clears only when the same, unchanged
        # check passes on changed code, and that red-then-green also shows the check can fail.
        flag, check_sh = Path(tmp, "flag"), Path(tmp, "check.sh")
        rerun = Path(rc("init", "--level", "ultra"))
        for text in ("fixed", "flaky", "changed check", "gamed", "regressed"):
            rc("claim", text)
        rc("claim", "can't verify yet", "--level", "full")
        rc("run", "c1", "--", "grep", "-q", "fixed", "app.py")
        (repo / "app.py").write_text('print("fixed")\n')
        rc("run", "c1", "--", "grep", "-q", "fixed", "app.py")
        assert verdicts()["c1"] == "✅ verified", verdicts()
        has_flag = ["sh", "-c", f"test -f {flag}"]
        rc("run", "c2", "--", *has_flag)
        flag.write_text("")
        rc("run", "c2", "--", *has_flag)  # flag is outside the repo: same code, different result
        (repo / "app.py").write_text('print("fixed again")\n')
        rc("run", "c2", "--", *has_flag)
        check_sh.write_text("grep -q nope app.py\n")
        rc("run", "c3", "--", "sh", str(check_sh))
        check_sh.write_text("grep -q again app.py\n")
        rc("run", "c3", "--", "sh", str(check_sh))
        rc("run", "c4", "--", "false")
        rc("run", "c4", "--", "true")
        rc("run", "c5", "--", *has_flag)
        flag.unlink()
        rc("run", "c5", "--", *has_flag)
        rc("unverified", "c6", "needs CI")
        rc("run", "c6", "--", "true")
        rc("claim", "red re-run", "--level", "full")
        flag.write_text("")
        rc("run", "c7", "--red", "--", *has_flag)  # flag exists, so this "red" run passes: can't fail
        assert verdicts()["c7"] == "❌ check can't fail", verdicts()
        flag.unlink()
        rc("run", "c7", "--red", "--", *has_flag)
        rc("run", "c7", "--", "true")
        assert verdicts()["c7"] == "✅ verified", verdicts()
        rc("claim", "fix that adds a file", "--level", "full")
        rc("run", "c8", "--", "test", "-f", "added.txt")
        (repo / "added.txt").write_text("new\n")  # untracked, so only the worktree diff sees it
        rc("run", "c8", "--", "test", "-f", "added.txt")
        assert verdicts()["c8"] == "✅ verified", verdicts()
        (repo / "added.txt").unlink()
        v = verdicts()
        assert v["c1"] == v["c8"] == "⚠️ stale: the code changed since it was checked", v
        assert v["c2"] == "⚠️ flaky: failed and passed on the same code", v
        assert v["c3"].startswith("⚠️ check changed after failing"), v
        assert v["c4"] == "❌ refuted" and v["c5"] == "❌ refuted" and v["c6"] == "✅ verified", v
        (repo / "app.py").write_text('print("fixed")\n')  # the changed check fails without the fix
        rc("run", "c3", "--red", "--", "sh", str(check_sh))
        (repo / "app.py").write_text('print("fixed again")\n')
        assert verdicts()["c3"] == "✅ verified", verdicts()
        # A desk test runs its spec as a copy named by content: a revised spec is the same check, changed.
        rc("claim", "revised spec", "--level", "full")
        old_spec, new_spec = (rerun / "specs" / f"c9-{h * 12}.spec.ts" for h in "ab")
        old_spec.write_text("grep -q nope app.py\n")
        new_spec.write_text("grep -q again app.py\n")
        rc("run", "c9", "--", "sh", str(old_spec))
        rc("run", "c9", "--", "sh", str(new_spec))
        assert verdicts()["c9"].startswith("⚠️ check changed after failing"), verdicts()
        (repo / "app.py").write_text('print("fixed")\n')
        rc("run", "c9", "--red", "--", "sh", str(new_spec))
        (repo / "app.py").write_text('print("fixed again")\n')
        assert verdicts()["c9"] == "✅ verified", verdicts()
        # Its --red runs against a baseline server at another URL and still pairs with the greens on the change's.
        rc("claim", "red on a baseline URL", "--level", "full")
        old_spec, new_spec = (rerun / "specs" / f"c10-{h * 12}.spec.ts" for h in "cd")
        old_spec.write_text("grep -q nope app.py\n")
        new_spec.write_text('case "$BASE_URL" in *baseline*) exit 1;; esac\n')
        rc("run", "c10", "--", "env", "BASE_URL=https://change.test", "sh", str(old_spec))
        rc("run", "c10", "--", "env", "BASE_URL=https://change.test", "sh", str(new_spec))
        assert verdicts()["c10"].startswith("⚠️ check changed after failing"), verdicts()
        rc("run", "c10", "--red", "--", "env", "BASE_URL=https://baseline.test", "sh", str(new_spec))
        assert verdicts()["c10"] == "✅ verified", verdicts()
        remote = rc("init", "--target", "https://staging.example.test")
        rc("claim", "staging fixed")
        rc("run", "c1", "--", *has_flag)
        flag.write_text("")
        rc("run", "c1", "--", *has_flag)
        assert verdicts(("--run", remote))["c1"] == "⚠️ passed after failing, code not pinned"

        # Finished runs: once a run's commits are on the base branch and nothing changed since, the
        # session stops hearing about it and its old claims stop covering new work.
        fin = Path(tmp, "fin")
        fin.mkdir()

        def fsh(*args, **kw):
            return sh(*args, cwd=fin, **kw)

        def frc(*args, **kw):
            return fsh(sys.executable, str(SCRIPT), *args, **kw)

        fsh("git", "init", "-q")
        (fin / "a.txt").write_text("1\n")
        fsh("git", "add", ".")
        fsh("git", "commit", "-qm", "one")
        base = fsh("git", "branch", "--show-current")
        session, pr_event = {"session_id": "f1", "cwd": str(fin)}, {"tool_input": {"command": "gh pr create"}}
        check("--hook", "start", event=session, cwd=fin)
        frc("init")
        frc("claim", "a is 2")
        (fin / "a.txt").write_text("2\n")
        frc("run", "c1", "--", "grep", "-q", "2", "a.txt")
        assert "has run" in check("--hook", "start", event={"session_id": "f0", "cwd": str(fin)}, cwd=fin)[1]
        fsh("git", "commit", "-qam", "two")  # committed on the base branch: landed
        code, out = check("--hook", "stop", event=session, cwd=fin)
        assert code == 0 and "is finished" in out, out
        assert check("--hook", "start", event={"session_id": "f2", "cwd": str(fin)}, cwd=fin) == (0, "")
        assert "is finished" in frc("claim", "more", ok=False)
        assert "pass" in frc("run", "c1", "--", "true"), "late evidence (CI after a push) can't be recorded"
        (fin / "a.txt").write_text("3\n")
        code, out = check("--hook", "stop", event=session, cwd=fin)
        assert code == 2 and "this session changed code" in out, out
        fsh("git", "checkout", "-q", "--", "a.txt")
        # A feature branch's run isn't finished by committing; its PR gate holds until it's merged.
        fsh("git", "checkout", "-q", "-b", "feature")
        frc("init")
        frc("claim", "a is 4")
        (fin / "a.txt").write_text("4\n")
        frc("run", "c1", "--", "false")
        fsh("git", "commit", "-qam", "four")
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert code == 2 and "c1 ❌ refuted" in out, out
        fsh("git", "checkout", "-q", base)
        fsh("git", "merge", "-q", "--ff-only", "feature")
        fsh("git", "checkout", "-q", "feature")
        assert "is finished" in check("--hook", "pr", event=pr_event, cwd=fin)[1]
        # A run started only after the work was done still judges it: the PR by the branch's changes,
        # the stop by what the session changed; a session that changed nothing isn't nudged.
        late = {"session_id": "f3", "cwd": str(fin)}
        check("--hook", "start", event=late, cwd=fin)
        fsh("git", "checkout", "-q", "-b", "late")
        (fin / "a.txt").write_text("5\n")
        fsh("git", "commit", "-qam", "five")
        frc("init")
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)  # no stages configured, and no claims yet
        assert code == 2 and "no claims: record what the branch does" in out, out
        frc("claim", "a is 5")
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert code == 2 and "c1 ⚠️ unverified: a is 5" in out, out
        code, out = check("--hook", "stop", event=late, cwd=fin)
        assert code == 2 and "c1 ⚠️ unverified: a is 5" in out, out
        reader = {"session_id": "f4", "cwd": str(fin)}
        check("--hook", "start", event=reader, cwd=fin)
        assert check("--hook", "stop", event=reader, cwd=fin) == (0, "check: no code changed since the run started")
        assert check("--hook", "stop", event={}, cwd=fin)[0] == 0, "a stop without a session was judged"
        # Switching or pulling to another commit isn't the session changing code; committing there is.
        fsh("git", "switch", "-q", base)
        fsh("git", "commit", "-q", "--allow-empty", "-m", "pulled", extra_env={"GIT_COMMITTER_EMAIL": "o@o"})
        fsh("git", "switch", "-qc", "moved")
        frc("init")
        frc("claim", "nothing yet")
        assert check("--hook", "stop", event=reader, cwd=fin) == (0, "check: no code changed since the run started")
        (fin / "a.txt").write_text("6\n")
        fsh("git", "commit", "-qam", "six")
        code, out = check("--hook", "stop", event=reader, cwd=fin)
        assert code == 2 and "c1 ⚠️ unverified: nothing yet" in out, out
        # Orphaned runs: set aside, a run stops speaking for its branch, takes nothing more and leaves the
        # start message, so the branch's next PR or changed stop needs a fresh run.
        fsh("git", "checkout", "-q", "-b", "abandoned")
        gone = {"session_id": "f5", "cwd": str(fin)}
        check("--hook", "start", event=gone, cwd=fin)
        frc("init")
        frc("claim", "a is 7")
        (fin / "a.txt").write_text("7\n")
        frc("run", "c1", "--", "grep", "-q", "7", "a.txt")
        fsh("git", "commit", "-qam", "seven")
        assert "has run" in check("--hook", "start", event={"session_id": "f6", "cwd": str(fin)}, cwd=fin)[1]
        assert frc("orphan", "--reason", "went another way").startswith("orphaned ")
        assert {**json.loads(frc("json"))["orphaned"], "at": None} == {"at": None, "reason": "went another way", "via": "manual",
                                                                       "session": None}
        assert check("--hook", "start", event={"session_id": "f7", "cwd": str(fin)}, cwd=fin) == (0, "")
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert code == 2 and "no receipts run" in out, out
        code, out = check("--hook", "stop", event=gone, cwd=fin)
        assert code == 2 and "this session changed code" in out, out
        for args in (("claim", "more"), ("run", "c1", "--", "true"), ("observe", "c1", "--passed", "x"),
                     ("unverified", "c1", "x"), ("attach", "c1", str(fin / "a.txt"))):
            assert "is orphaned" in frc(*args, ok=False), args
        check("--hook", "stop", event={"session_id": "f9", "cwd": str(fin)}, cwd=fin)  # saw only the orphaned run
        fresh = Path(frc("init")).name
        frc("claim", "a is 7, again")
        check("--hook", "stop", event=gone, cwd=fin)
        # The dashboard leaves orphaned runs out, and a session whose runs all are.
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "0"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: listening(port), 10)
        rows = {r["session"]: r for r in json.loads(get(port, "/api/sessions"))}
        server.terminate()
        server.wait()
        assert "f9" not in rows and [x["id"] for x in rows["f5"]["runs"]] == [fresh], rows.get("f5")
        assert "c1 ⚠️ unverified: a is 7, again" in rows["f5"]["open"]["unaddressed"], rows["f5"]["open"]
        # Sweep settles runs no checkout will: merged branches (kept, or deleted after a GitHub merge) finish,
        # runs started after their branch's last commit included (from before init kept the fork point too),
        # deleted unmerged ones are orphaned, open and commitless ones stay; a second sweep changes nothing.
        swept = {}
        for name in ("kept-merged", "gh-merged", "dropped", "still-open", "no-commits", "in-a-worktree", "wt-dirty", "wt-ahead",
                     "kip-merged", "kip-dirty", "late-merged", "legacy-late"):
            fsh("git", "checkout", "-q", "-b", name, base)
            if "late" not in name:
                swept[name] = Path(frc("init")).name
            if name != "no-commits":
                (fin / f"{name}.txt").write_text(name)
                fsh("git", "add", ".")
                fsh("git", "commit", "-qm", name)
            if "late" in name:
                swept[name] = Path(frc("init")).name
        assert run_json(swept["late-merged"])["fork"] == fsh("git", "rev-parse", base)
        kipdb("UPDATE runs SET data = json_remove(data, '$.fork') WHERE id = ?", swept["legacy-late"])
        fsh("git", "checkout", "-q", base)
        for name in ("late-merged", "legacy-late"):
            fsh("git", "merge", "-q", "--no-ff", name, "-m", f"merge {name}")
        fsh("git", "merge", "-q", "--no-ff", "kept-merged", "-m", "merge kept-merged")
        fsh("git", "merge", "-q", "--no-ff", "gh-merged", "-m", "Merge pull request #2 from me/gh-merged")
        # A merged branch still checked out in another worktree finishes once that worktree is clean: no
        # uncommitted or untracked work, and no commits past the merge. One kip.py worktree new made is then
        # removed, with its branch; a host's (anywhere else) and a dirty one are left alone.
        folders = {n: fin / ".kip" / "worktrees" / n if n.startswith("kip-") else Path(tmp, n)
                   for n in ("in-a-worktree", "wt-dirty", "wt-ahead", "kip-merged", "kip-dirty")}
        for name, folder in folders.items():
            fsh("git", "merge", "-q", "--no-ff", name, "-m", f"merge {name}")
            fsh("git", "worktree", "add", "-q", str(folder), name)
        for name in ("wt-dirty", "kip-dirty"):
            (folders[name] / "new.txt").write_text("untracked\n")
        sh("git", "commit", "-q", "--allow-empty", "-m", "after the merge", cwd=Path(tmp, "wt-ahead"))
        fsh("git", "branch", "-q", "-D", "gh-merged", "dropped")
        out = frc("sweep")
        state = {n: next((k for k in ("finished", "orphaned") if run_json(rid).get(k)), "open") for n, rid in swept.items()}
        assert state == {"kept-merged": "finished", "gh-merged": "finished", "dropped": "orphaned",
                         "still-open": "open", "no-commits": "open", "in-a-worktree": "finished", "wt-dirty": "open",
                         "wt-ahead": "open", "kip-merged": "finished", "kip-dirty": "open", "late-merged": "finished",
                         "legacy-late": "finished"}, (state, out)
        assert f"orphaned {swept['dropped']} (dropped: branch gone)" in out, out
        assert [n for n, f in folders.items() if not f.is_dir()] == ["kip-merged"], out
        assert not fsh("git", "branch", "--list", "kip-merged") and fsh("git", "branch", "--list", "in-a-worktree"), out
        assert [run_json(swept[n])[k]["via"] for n, k in (("kept-merged", "finished"), ("dropped", "orphaned"))] == \
            ["merged", "branch gone"]
        assert frc("sweep") == "", "a second sweep changed something"
        # The dashboard sweeps every repo with an open run on a timer, so one no session starts in still settles.
        for name in ("wt-dirty", "kip-dirty"):
            (folders[name] / "new.txt").unlink()
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "0", "--settle", "1"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: run_json(swept["wt-dirty"]).get("finished") and not folders["kip-dirty"].is_dir(), 20), \
            "the dashboard didn't settle wt-dirty and kip-dirty"
        server.terminate()
        server.wait()
        # The gate on its own branch finishes a run started after the branch's last commit, once that's merged.
        fsh("git", "checkout", "-q", "-b", "late-gate", base)
        (fin / "late-gate.txt").write_text("1\n")
        fsh("git", "add", ".")
        fsh("git", "commit", "-qm", "late-gate")
        late_gate = Path(frc("init")).name
        fsh("git", "checkout", "-q", base)
        fsh("git", "merge", "-q", "--no-ff", "late-gate", "-m", "merge late-gate")
        fsh("git", "checkout", "-q", "late-gate")
        assert "is finished" in check("--hook", "stop", event={}, cwd=fin)[1] and run_json(late_gate)["finished"]["via"] == "checkout"
        fsh("git", "checkout", "-q", base)
        # Squash merges leave no ancestry, so on GitHub sweep asks gh which PRs merged: a kept branch whose tip
        # is a merged PR's head finishes, and so does a deleted one with a PR merged after its run started; a
        # branch that moved on after its PR merged stays open. Until gh answers, a deleted branch isn't orphaned.
        squashed, tips = {}, {}
        for name in ("squash-kept", "squash-gone", "squash-moved", "name-reused"):
            fsh("git", "checkout", "-q", "-b", name, base)
            squashed[name] = Path(frc("init")).name
            (fin / f"{name}.txt").write_text(name)
            fsh("git", "add", ".")
            fsh("git", "commit", "-qm", name)
            tips[name] = fsh("git", "rev-parse", "HEAD")
            fsh("git", "checkout", "-q", base)
            if name == "name-reused":  # abandoned after evidence on its commit; a later branch of that name merged
                frc("claim", "abandoned", "--run", squashed[name])
                fsh("git", "checkout", "-q", name)
                frc("run", "c1", "--", "true")
                fsh("git", "checkout", "-q", base)
                tips[name] = fsh("git", "rev-parse", "HEAD")
                continue
            fsh("git", "merge", "-q", "--squash", name)
            fsh("git", "commit", "-qm", f"{name} (#9)")
        fsh("git", "checkout", "-q", "squash-moved")
        fsh("git", "commit", "-q", "--allow-empty", "-m", "after the merge")
        fsh("git", "checkout", "-q", base)
        fsh("git", "branch", "-q", "-D", "squash-gone", "name-reused")
        fsh("git", "remote", "add", "origin", "https://github.com/me/fin.git")
        merged_prs = Path(tmp, "merged.json")
        merged_prs.write_text(json.dumps([{"headRefName": n, "headRefOid": tips[n], "mergedAt": "2999-01-01T00:00:00Z", "number": 9}
                                          for n in squashed]))
        no_gh = {"PATH": f"{bin_}{os.pathsep}{env['PATH']}"}  # FAKE_MERGED_PRS unset: gh fails
        assert frc("sweep", extra_env=no_gh) == "", "settled squash merges without gh"
        out = frc("sweep", extra_env={**no_gh, "FAKE_MERGED_PRS": str(merged_prs)})
        state = {n: next((k for k in ("finished", "orphaned") if run_json(r).get(k)), "open") for n, r in squashed.items()}
        assert state == {"squash-kept": "finished", "squash-gone": "finished", "squash-moved": "open",
                         "name-reused": "orphaned"}, (state, out)
        assert run_json(squashed["squash-kept"])["finished"]["via"] == "pr #9"
        fsh("git", "remote", "remove", "origin")
        # A branch recreated under an old name doesn't inherit the old branch's run (one started before it was
        # created, on commits it lacks); sweep orphans that run. A run started on the new branch is its own.
        fsh("git", "checkout", "-q", "-b", "reused", base)
        old = Path(frc("init")).name
        frc("claim", "old promise")
        kipdb("UPDATE runs SET created_at = '2000-01-01T00:00:00+00:00', "
              "data = json_set(data, '$.created_at', '2000-01-01T00:00:00+00:00') WHERE id = ?", old)
        fsh("git", "checkout", "-q", base)
        fsh("git", "branch", "-q", "-D", "reused")
        fsh("git", "checkout", "-q", "-b", "reused", base)
        assert "no receipt run for this branch" in frc("path", ok=False)
        out = frc("sweep")
        assert f"orphaned {old} (reused: branch recreated)" in out, out
        assert run_json(old)["orphaned"]["via"] == "branch recreated"
        assert Path(frc("init")).name == Path(frc("path")).name
        fsh("git", "checkout", "-q", base)
        # Checked out again from its own remote branch, it's the same branch: the run (started before any commit,
        # so on a commit the base branch has) still speaks for it. A run with no start time is never passed over.
        fsh("git", "checkout", "-q", "-b", "again", base)
        again = Path(frc("init")).name
        kipdb("UPDATE runs SET created_at = '2000-01-01T00:00:00+00:00', "
              "data = json_set(data, '$.created_at', '2000-01-01T00:00:00+00:00') WHERE id = ?", again)
        fsh("git", "commit", "-q", "--allow-empty", "-m", "again")
        fsh("git", "update-ref", "refs/remotes/origin/again", "HEAD")
        fsh("git", "checkout", "-q", base)
        fsh("git", "branch", "-q", "-D", "again")
        fsh("git", "checkout", "-q", "-b", "again", "refs/remotes/origin/again")
        assert Path(frc("path")).name == again and again not in frc("sweep")
        kipdb("UPDATE runs SET data = json_remove(data, '$.created_at') WHERE id = ?", old)
        fsh("git", "checkout", "-q", "reused")
        assert Path(frc("path")).name != old, "a run without a start time crashed or took the branch"
        fsh("git", "checkout", "-q", base)
        fsh("git", "update-ref", "-d", "refs/remotes/origin/again")
        # A worktree's runs belong to its repository, not its folder: sweeping the main checkout settles them
        # once the worktree is gone. Runs stored under a worktree's folder name before that still load and serve.
        wt2 = Path(tmp, "wt2-folder")
        fsh("git", "worktree", "add", "-q", "-b", "in-wt2", str(wt2), base)
        wt_run = Path(sh(sys.executable, str(SCRIPT), "init", cwd=wt2))
        assert wt_run.parent.name == "fin" and run_json(wt_run.name)["repo"] == "fin", wt_run

        def wrc(*args, **kw):
            return sh(sys.executable, str(SCRIPT), *args, cwd=wt2, **kw)

        # A run knows the worktree it started in, and only the checkout holding its code judges its evidence stale:
        # a sibling checkout's own changes don't, the run's worktree's do.
        assert run_json(wt_run.name)["worktree"] == str(wt2.resolve()), run_json(wt_run.name)
        wrc("claim", "wt2 works")
        wrc("run", "c1", "--", "true")
        (fin / "dirty.txt").write_text("the main checkout's own change\n")
        assert "stale" not in frc("render", "--run", wt_run.name)
        (fin / "dirty.txt").unlink()
        (wt2 / "wt2.txt").write_text("1\n")
        assert "stale" in wrc("render")
        sh("git", "add", ".", cwd=wt2)
        sh("git", "commit", "-qm", "wt2", cwd=wt2)
        # On a detached HEAD, a worktree's branchless run is its own, never a sibling worktree's.
        sh("git", "checkout", "-q", "--detach", cwd=wt2)
        detached = Path(wrc("init")).name
        assert Path(wrc("path")).name == detached and not run_json(detached)["branch"]
        fsh("git", "checkout", "-q", "--detach")
        assert "no receipt run" in frc("path", ok=False)
        fsh("git", "checkout", "-q", base)
        # A session's start is kept per worktree. One that started in a main checkout with uncommitted edits and
        # moved into a worktree made after it started is judged there from the worktree as it was created:
        # nothing done there yet passes, an edit or a commit there needs a run, the main checkout's edits don't count.
        mover, wt3 = {"session_id": "mover"}, Path(tmp, "wt3-folder")
        (fin / "left-behind.txt").write_text("main checkout's edit\n")
        check("--hook", "start", event={**mover, "cwd": str(fin)}, cwd=fin)
        fsh("git", "worktree", "add", "-q", "-b", "in-wt3", str(wt3), base)
        assert check("--hook", "stop", event={**mover, "cwd": str(wt3)}, cwd=wt3)[0] == 0
        (wt3 / "wt3.txt").write_text("1\n")
        code, out = check("--hook", "stop", event={**mover, "cwd": str(wt3)}, cwd=wt3)
        assert code == 2 and "this session changed code" in out, out
        sh("git", "add", ".", cwd=wt3)
        sh("git", "commit", "-qm", "wt3", cwd=wt3)
        code, out = check("--hook", "stop", event={**mover, "cwd": str(wt3)}, cwd=wt3)
        assert code == 2 and "this session changed code" in out, out
        assert check("--hook", "start", event={**mover, "cwd": str(wt3)}, cwd=wt3)[0] == 0
        assert {w for (w,) in kipdb("SELECT worktree FROM session_starts WHERE id = 'mover'")} == \
            {str(fin.resolve()), str(wt3.resolve())}
        (fin / "left-behind.txt").unlink()
        fsh("git", "worktree", "remove", "--force", str(wt3))
        fsh("git", "branch", "-q", "-D", "in-wt3")
        fsh("git", "merge", "-q", "--no-ff", "in-wt2", "-m", "merge in-wt2")
        fsh("git", "worktree", "remove", str(wt2))
        assert f"finished {wt_run.name} (in-wt2: merged)" in frc("sweep")
        # A run whose linked worktree is gone (removed, or its folder deleted) is orphaned once its branch never got
        # a commit: its uncommitted work went with the folder. One whose branch has commits waits to merge or go.
        gone = {}
        for name in ("wt-empty", "wt-deleted", "wt-committed"):
            folder = Path(tmp, f"{name}-folder")
            fsh("git", "worktree", "add", "-q", "-b", name, str(folder), base)
            gone[name] = Path(sh(sys.executable, str(SCRIPT), "init", cwd=folder)).name
            (folder / "work.txt").write_text(name)
            if name == "wt-committed":
                sh("git", "add", ".", cwd=folder)
                sh("git", "commit", "-qm", name, cwd=folder)
            if name == "wt-deleted":
                shutil.rmtree(folder)
            else:
                fsh("git", "worktree", "remove", "--force", str(folder))
        out = frc("sweep")
        assert [run_json(gone[n]).get("orphaned", {}).get("via") for n in gone] == \
            ["worktree removed", "worktree removed", None], (out, gone)
        assert f"orphaned {gone['wt-empty']} (wt-empty: worktree removed)" in out, out
        fsh("git", "worktree", "prune")
        for name in gone:
            fsh("git", "branch", "-q", "-D", name)
        # Only a linked worktree can be removed: a repo whose folder moved keeps its main checkout's runs.
        moved = Path(tmp, "mv-before")
        moved.mkdir()
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "mv"], ["checkout", "-q", "-b", "mv-work"]):
            sh("git", *cmd, cwd=moved)
        mv_run = Path(sh(sys.executable, str(SCRIPT), "init", cwd=moved)).name
        Path(tmp, "elsewhere").mkdir()
        moved = moved.rename(Path(tmp, "elsewhere", "mv-before"))  # same name, so the same repo, in a new place
        sh("git", "checkout", "-q", "-b", "mv-other", cwd=moved)  # sweep leaves the checked-out branch to the gate
        sh(sys.executable, str(SCRIPT), "sweep", cwd=moved)
        assert not run_json(mv_run).get("orphaned"), run_json(mv_run)
        Path(tmp, "store", "old-wt").mkdir()
        wt_run.rename(Path(tmp, "store", "old-wt", wt_run.name))
        kipdb("UPDATE runs SET repo = 'old-wt', data = json_set(data, '$.repo', 'old-wt') WHERE id = ?", wt_run.name)
        assert "old-wt @" in frc("render", "--run", wt_run.name)
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "0"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: listening(port), 10)
        served = json.loads(get(port, f"/api/runs/{wt_run.name}"))
        server.terminate()
        server.wait()
        assert served["id"] == wt_run.name and served["finished"], served
        # `db --migrate` moves such runs to their repository: the one main checkout with the run's branch and commit,
        # or with a live worktree of that folder name, looking in recorded checkouts and their siblings named like a
        # stored repo. Its files move too and still verify; one no checkout claims stays where it is.
        sib, sib_wt = Path(tmp, "sib"), Path(tmp, "sib-wt")
        sib.mkdir()
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "sib"], ["worktree", "add", "-q", "--detach", str(sib_wt)]):
            sh("git", *cmd, cwd=sib)
        sib_runs = [Path(sh(sys.executable, str(SCRIPT), "init", cwd=c)).name for c in (sib, sib_wt)]
        kipdb("UPDATE runs SET data = json_remove(data, '$.checkout') WHERE repo = 'sib'")
        Path(tmp, "store", "sib-wt").mkdir()
        Path(tmp, "store", "sib", sib_runs[1]).rename(Path(tmp, "store", "sib-wt", sib_runs[1]))
        kipdb("UPDATE runs SET repo = 'sib-wt', data = json_set(data, '$.repo', 'sib-wt') WHERE id = ?", sib_runs[1])
        stray = Path(frc("init")).name
        Path(tmp, "store", "nowhere").mkdir()
        Path(tmp, "store", "fin", stray).rename(Path(tmp, "store", "nowhere", stray))
        kipdb("UPDATE runs SET repo = 'nowhere', branch = 'no-such-branch', data = json_set(data, '$.repo', 'nowhere') "
              "WHERE id = ?", stray)
        base_run = Path(sh(sys.executable, str(SCRIPT), "init", cwd=fin)).name
        Path(tmp, "store", "a-clone").mkdir()
        Path(tmp, "store", "fin", base_run).rename(Path(tmp, "store", "a-clone", base_run))
        kipdb("UPDATE runs SET repo = 'a-clone', data = json_set(data, '$.repo', 'a-clone') WHERE id = ?", base_run)
        out = frc("db", "--migrate")
        assert run_json(base_run)["repo"] == "a-clone" and run_json(base_run)["branch"] == base, (out, run_json(base_run))
        assert f"re-homed {wt_run.name}: old-wt -> fin" in out and f"re-homed {sib_runs[1]}: sib-wt -> sib" in out, out
        assert "nowhere" not in out and run_json(stray)["repo"] == "nowhere", out
        assert run_json(wt_run.name)["checkout"] == str(fin.resolve()) and "fin @" in frc("render", "--run", wt_run.name)
        assert Path(frc("path", "--run", sib_runs[1])) == Path(tmp, "store", "sib", sib_runs[1]) and \
            run_json(sib_runs[1])["repo"] == "sib"
        assert "re-homed" not in frc("db", "--migrate")
        sh("git", "worktree", "remove", str(sib_wt), cwd=sib)
        # A plan run hands a unit's open claims to a run in that unit's worktree (`init --parent --unit`). The plan's
        # verdicts for them are that run's, labeled with where they were checked, and it takes no evidence for them
        # itself. Merging the unit back makes the plan's own required checks stale until they pass on the merged
        # code, while the handed-over claims stay verified. An orphaned unit run hands its claims back.
        fsh("git", "checkout", "-q", "-b", "plan", base)
        (fin / ".kip").mkdir(exist_ok=True)
        (fin / ".kip" / "config.json").write_text(json.dumps({"workflow": {"stages": [], "checks": {"tests": "test -f a.txt"}}}))
        fsh("git", "add", ".")
        fsh("git", "commit", "-qm", "plan config")
        plan_run = Path(frc("init")).name
        for text, unit in (("u1 a", "1"), ("u1 b", "1"), ("u2 c", "2")):
            frc("claim", text, "--unit", unit)
        u1, u2 = Path(tmp, "u1-folder"), Path(tmp, "u2-folder")
        fsh("git", "worktree", "add", "-q", "-b", "unit1", str(u1), "plan")
        fsh("git", "worktree", "add", "-q", "-b", "unit2", str(u2), "plan")

        def urc(folder, *args, **kw):
            return sh(sys.executable, str(SCRIPT), *args, cwd=folder, **kw)

        runs_before = len(kipdb("SELECT id FROM runs"))
        assert "--parent needs --unit" in urc(u1, "init", "--parent", plan_run, ok=False)
        assert "no open claims for unit 9" in urc(u1, "init", "--parent", plan_run, "--unit", "9", ok=False)
        assert len(kipdb("SELECT id FROM runs")) == runs_before, "a refused init left a run"
        assert "is this checkout's own run" in frc("init", "--parent", plan_run, "--unit", "1", ok=False)
        child = Path(urc(u1, "init", "--parent", plan_run, "--unit", "1")).name
        crec, prec = run_json(child), run_json(plan_run)
        assert (crec["parent"], crec["unit"], crec["level"]) == (plan_run, "1", "full"), crec
        assert [c["text"] for c in crec["claims"]] == ["`tests` passes", "u1 a", "u1 b"], crec["claims"]
        assert [c.get("delegated") for c in prec["claims"]] == \
            [None, {"run": child, "claim": "c2"}, {"run": child, "claim": "c3"}, None], prec["claims"]
        assert crec["claims"][1]["delegated_from"] == {"run": plan_run, "claim": "c2"}
        assert "no open claims for unit 1" in urc(u1, "init", "--parent", plan_run, "--unit", "1", ok=False)
        assert f"was handed to run {child} as c2" in frc("run", "c2", "--", "true", ok=False)
        assert f"was handed to run {child}" in frc("unverified", "c3", "later", ok=False)
        plan_v = lambda: verdicts(("--run", plan_run))  # noqa: E731
        assert plan_v()["c2"] == "⚠️ unverified", plan_v()
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert code == 2 and "c2 ⚠️ unverified: u1 a" in out, out
        (u1 / "u1.txt").write_text("1\n")
        sh("git", "add", ".", cwd=u1)
        sh("git", "commit", "-qm", "unit 1", cwd=u1)
        urc(u1, "run", "c1")
        urc(u1, "run", "c2", "c3", "--", "true")
        unit_sha = sh("git", "rev-parse", "--short=7", "HEAD", cwd=u1)
        frc("run", "c1")
        assert plan_v()["c2"] == plan_v()["c3"] == "✅ verified", plan_v()
        assert f"delegated to `{child}` c2 (unit1 @ `{unit_sha}`)" in frc("render", "--run", plan_run)
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert code == 2 and "c4 ⚠️ unverified: u2 c" in out and not re.search(r"  c[123] ", out), out
        fsh("git", "merge", "-q", "--no-ff", "unit1", "-m", "merge unit1")
        code, out = check("--hook", "pr", event=pr_event, cwd=fin)
        assert "c1 ⚠️ stale" in out and not re.search(r"  c[23] ", out), out
        frc("run", "c1")
        assert "c1 " not in check("--hook", "pr", event=pr_event, cwd=fin)[1]
        child2 = Path(urc(u2, "init", "--parent", plan_run, "--unit", "2")).name
        assert run_json(plan_run)["claims"][3]["delegated"]["run"] == child2
        urc(u2, "orphan", "--reason", "redo unit 2")
        assert plan_v()["c4"] == "⚠️ unverified" and "c4 ⚠️ unverified" in check("--hook", "pr", event=pr_event, cwd=fin)[1]
        child3 = Path(urc(u2, "init", "--parent", plan_run, "--unit", "2")).name
        assert run_json(plan_run)["claims"][3]["delegated"]["run"] == child3 and child3 != child2
        for folder, branch in ((u1, "unit1"), (u2, "unit2")):
            fsh("git", "worktree", "remove", "--force", str(folder))
            fsh("git", "branch", "-q", "-D", branch)
        fsh("git", "checkout", "-q", base)
        # workflow.worktrees: editing a main checkout's file is stopped once a session by default (nudge), every time
        # when required, never when off. A linked worktree's files and files outside a repo always pass. Under
        # required, a stop after this session changed the main checkout is blocked too.
        pol = Path(tmp, "pol")
        pol.mkdir()
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "pol"]):
            sh("git", *cmd, cwd=pol)
        pol_wt = Path(tmp, "pol-wt")
        sh("git", "worktree", "add", "-q", "-b", "pol-wt", str(pol_wt), cwd=pol)

        def edit(path, sid="e1"):
            return check("--hook", "edit", event={"session_id": sid, "cwd": str(pol), "hook_event_name": "PreToolUse",
                                                  "tool_name": "Write", "tool_input": {"file_path": str(path)}}, cwd=pol)

        code, out = edit(pol / "src" / "new.py")  # a file, and folder, not made yet
        assert code == 2 and "is in the main checkout" in out and "nudge" in out, out
        assert edit(pol / "src" / "new.py") == (0, "") and edit(pol / "x.py", "e2")[0] == 2
        assert edit(pol_wt / "x.py")[0] == 0 and edit(Path(tmp, "outside.txt"))[0] == 0
        assert check("--hook", "edit", event={"tool_name": "Write", "tool_input": {"file_path": str(pol / "y.py")}},
                     cwd=pol)[0] == 0, "nudged an edit with no session to nudge once"
        check("--hook", "end", event={"session_id": "e1"})
        assert edit(pol / "x.py")[0] == 2, "a session's end didn't forget its nudge"
        (pol / ".kip").mkdir()
        (pol / ".kip" / "config.json").write_text('{"workflow": {"worktrees": "off"}}')
        assert edit(pol / "x.py", "e3") == (0, "")
        (pol / ".kip" / "config.json").write_text('{"workflow": {"worktrees": "required", "stages": []}}')
        assert edit(pol / "x.py", "e4")[0] == edit(pol / "x.py", "e4")[0] == 2
        assert edit(pol_wt / "x.py", "e4")[0] == 0
        def used(sid, tool, what):  # what the tool hook logs for a skill call, or for reading or editing a file
            check("--hook", "tool", event={"session_id": sid, "tool_name": tool,
                                           "tool_input": {"skill" if tool == "Skill" else "file_path": what}})

        # Every edit pays for this hook: a linked worktree's file, or one outside a repo, costs one git call
        # once the session has used the skills workflow.skills puts before edits.
        (pol / ".kip" / "config.json").write_text('{"workflow": {"worktrees": "required", "stages": [], '
                                                  '"skills": {"decompose": "required", "implement": "nudge"}}}')
        used("e5", "Skill", "kip-decompose")
        used("e5", "Skill", "kip-messy-bun")
        counted, calls = Path(tmp, "counted-git"), Path(tmp, "git-calls")
        counted.mkdir()
        real_git = shutil.which("git")
        (counted / "git").write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexec "{real_git}" "$@"\n')
        (counted / "git").chmod(0o755)
        for path in (pol_wt / "x.py", Path(tmp, "outside.txt")):
            calls.unlink(missing_ok=True)
            assert check("--hook", "edit", event={"session_id": "e5", "tool_name": "Write", "tool_input": {
                "file_path": str(path)}}, cwd=pol, extra_env={"PATH": f"{counted}:{os.environ['PATH']}"})[0] == 0
            assert len(calls.read_text().splitlines()) == 1, (path, calls.read_text())
        mover_pol = {"session_id": "pol-s", "cwd": str(pol)}
        check("--hook", "start", event=mover_pol, cwd=pol)
        assert check("--hook", "stop", event=mover_pol, cwd=pol)[0] == 0
        (pol / "made-here.txt").write_text("1\n")
        code, out = check("--hook", "stop", event=mover_pol, cwd=pol)
        assert code == 2 and "workflow.worktrees is required" in out, out
        check("--hook", "start", event={**mover_pol, "cwd": str(pol_wt)}, cwd=pol_wt)
        assert "workflow.worktrees" not in check("--hook", "stop", event={**mover_pol, "cwd": str(pol_wt)}, cwd=pol_wt)[1]
        sh("git", "worktree", "remove", "--force", str(pol_wt), cwd=pol)
        # workflow.skills: an edit in a repo waits for kip-decompose (required) and is nudged once toward kip-messy-bun;
        # a stop after the session changed code waits for kip-marie-kondo, in one message with the run's own blocks.
        # A Skill call or a read of its SKILL.md counts as using a skill; editing its SKILL.md doesn't.
        (pol / ".kip" / "config.json").write_text(json.dumps({"workflow": {"stages": [], "worktrees": "off", "skills": {
            "decompose": "required", "implement": "nudge", "tidy": "required"}}}))
        code, out = edit(pol / "a.py", "sk")
        assert code == 2 and "/kip-decompose" in out and "/kip-messy-bun" in out, out
        code, out = edit(pol / "a.py", "sk")
        assert code == 2 and "/kip-decompose" in out and "kip-messy-bun" not in out, "nudged messy-bun twice: " + out
        used("sk", "Skill", "kip-decompose")
        assert edit(pol / "a.py", "sk") == (0, "")
        for name in ("kip-decompose", "kip-messy-bun"):
            used("sk2", "Read", f"/x/skills/analysis/{name}/SKILL.md")
        assert edit(pol / "a.py", "sk2") == (0, "")
        used("sk3", "Edit", "/x/skills/analysis/kip-decompose/SKILL.md")
        assert "/kip-decompose" in edit(pol / "a.py", "sk3")[1], "editing a skill counted as using it"
        assert edit(Path(tmp, "outside.txt"), "sk3") == (0, ""), "gated a file outside a repo"
        sk = {"session_id": "sk", "cwd": str(pol)}
        check("--hook", "start", event=sk, cwd=pol)
        assert check("--hook", "stop", event=sk, cwd=pol)[0] == 0, "gated a stop with no code changed"
        (pol / "tidy-me.txt").write_text("1\n")
        code, out = check("--hook", "stop", event=sk, cwd=pol)
        assert code == 2 and "/kip-marie-kondo" in out and "no receipts run" in out and "/kip-decompose" not in out, out
        assert check("--hook", "stop", event={**sk, "stop_hook_active": True}, cwd=pol)[0] == 0, "stop hook could loop"
        used("sk", "Skill", "kip-marie-kondo")
        assert "kip-marie-kondo" not in check("--hook", "stop", event=sk, cwd=pol)[1]
        check("--hook", "end", event=sk)
        assert "/kip-messy-bun" in edit(pol / "a.py", "sk")[1], "a session's end didn't forget its skill nudges"
        assert "/kip-messy-bun" not in edit(pol / "a.py", "sk3")[1], "a session's end forgot another session's nudges"
        # By default (no home config nulling the gates) kip-messy-bun is required, not a nudge: every edit waits for
        # it until the session uses it.
        (pol / ".kip" / "config.json").write_text('{"workflow": {"stages": [], "worktrees": "off"}}')
        def edit_default(sid):
            return check("--hook", "edit", event={"session_id": sid, "cwd": str(pol), "tool_name": "Write",
                                                  "tool_input": {"file_path": str(pol / "a.py")}},
                         cwd=pol, extra_env={"KIP_HOME": str(Path(tmp, "home-defaults"))})
        used("sk5", "Skill", "kip-decompose")
        assert "/kip-messy-bun" in edit_default("sk5")[1]
        assert "/kip-messy-bun" in edit_default("sk5")[1], "kip-messy-bun was only a nudge by default"
        used("sk5", "Skill", "kip-messy-bun")
        assert edit_default("sk5") == (0, "")
        (pol / ".kip" / "config.json").write_text('{"workflow": {"skills": {"tidy": "sometimes"}}}')
        assert "workflow.skills must map" in edit(pol / "a.py", "sk4")[1]
        (pol / "tidy-me.txt").unlink()
        # Evidence against a local URL records the folder its server runs in. Served by another checkout of this
        # repo (worktrees share ports), it doesn't speak for the code it pinned: flagged, and it holds the gate.
        # Served from this checkout, or from outside any checkout of the repo, it counts.
        srv_wt = Path(tmp, "srv-wt")
        sh("git", "worktree", "add", "-q", "--detach", str(srv_wt), cwd=pol)
        (pol / ".kip" / "config.json").write_text('{"workflow": {"stages": []}}')
        sh("git", "checkout", "-q", "-b", "srv", cwd=pol)
        sh("git", "commit", "-q", "--allow-empty", "-m", "srv", cwd=pol)
        srv_run = Path(sh(sys.executable, str(SCRIPT), "init", cwd=pol)).name
        sh(sys.executable, str(SCRIPT), "claim", "the page loads", cwd=pol)

        port = free_port()  # one URL throughout, as a dev server's would be: one check

        def served_by(folder):
            server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=folder,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert wait_until(lambda: listening(port), 10)
            try:
                sh(sys.executable, str(SCRIPT), "run", "c1", "--", sys.executable, "-c",
                   f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/')", cwd=pol)
            finally:
                server.terminate()
                server.wait()
            return run_json(srv_run)["claims"][0]["evidence"][-1]["served"], \
                verdicts(("--run", srv_run))["c1"]

        info, v = served_by(srv_wt)
        assert info["checkout"] == str(srv_wt.resolve()) and info["here"] == str(pol.resolve()), info
        assert v == f"⚠️ served from another checkout: {srv_wt.resolve()}", v
        code, out = check("--hook", "pr", event={"tool_input": {"command": "gh pr create"}}, cwd=pol)
        assert code == 2 and "c1 ⚠️ served from another checkout" in out, out
        assert served_by(pol)[1] == "✅ verified"
        info, v = served_by(Path(tmp))
        assert info["cwd"] and info["checkout"] is None and v == "✅ verified", (info, v)
        served_by(srv_wt)
        # The dashboard: a session's row says which worktree its last gate fired in; /api/runs lists a plan's unit
        # runs, each unit run its plan, unit and worktree (struck once removed), and flags a removed worktree with
        # unmerged work and evidence served from another checkout; a delegated claim says where it went.
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "0"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: listening(port), 10)
        try:
            listed = {x["id"]: x for x in json.loads(get(port, "/api/runs"))}
            sessions = {r["session"]: r for r in json.loads(get(port, "/api/sessions"))}
            detail = json.loads(get(port, f"/api/runs/{plan_run}"))
        finally:
            server.terminate()
            server.wait()
        assert set(listed[plan_run]["children"]) == {child, child2, child3}, listed[plan_run]
        unit = listed[child]
        assert (unit["parent"], unit["unit"]) == (plan_run, "1") and unit["worktree"] == \
            {"path": str(u1.resolve()), "linked": True, "live": False}, unit
        assert "its worktree was removed with work not merged" in unit["flags"] and unit["attention"], unit
        assert listed[plan_run]["worktree"]["linked"] is False, listed[plan_run]
        assert "evidence served from another checkout" in listed[srv_run]["flags"], listed[srv_run]
        assert detail["claims"][1]["delegated"] == {"run": child, "claim": "c2"} and detail["claims"][0]["delegated"] is None
        assert (sessions["pol-s"]["worktree"], sessions["pol-s"]["linked"]) == (str(pol_wt.resolve()), True), sessions["pol-s"]
        assert sessions["pol-s"]["worktrees"] == [{"path": str(pol.resolve()), "linked": False, "live": True},
                                                  {"path": str(pol_wt.resolve()), "linked": True, "live": False}], sessions["pol-s"]
        sh("git", "worktree", "remove", "--force", str(srv_wt), cwd=pol)
        # SubagentStop: a subagent in a worktree of its own (Claude Code's agent-<agent_id>) is gated like a session
        # there: changed code needs a run on its branch, then its claims addressed. One sharing its parent's checkout,
        # main or the parent's worktree, is never blocked, even with the parent's run open; nor is a re-stop.
        sub = Path(tmp, "sub")
        sub.mkdir()
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "sub"], ["checkout", "-q", "-b", "parent-work"]):
            sh("git", *cmd, cwd=sub)
        (sub / ".kip").mkdir()
        (sub / ".kip" / "config.json").write_text('{"workflow": {"stages": [], "worktrees": "off"}}')
        parent = {"session_id": "sub-s"}
        check("--hook", "start", event={**parent, "cwd": str(sub)}, cwd=sub)
        sh(sys.executable, str(SCRIPT), "init", cwd=sub)
        sh(sys.executable, str(SCRIPT), "claim", "parent's open claim", cwd=sub)
        (sub / "parent.txt").write_text("mid-task\n")
        agent_wt, parent_wt = Path(tmp, "agent-abc123"), Path(tmp, "parent-wt")
        sh("git", "worktree", "add", "-q", "-b", "worktree-agent-abc123", str(agent_wt), cwd=sub)
        sh("git", "worktree", "add", "-q", "-b", "parent-wt", str(parent_wt), cwd=sub)

        def subagent_stop(cwd, agent="abc123", **extra):
            return check("--hook", "subagent", event={**parent, "agent_id": agent, "agent_type": "iso", "cwd": str(cwd),
                                                      "hook_event_name": "SubagentStop", **extra}, cwd=cwd)

        assert subagent_stop(sub) == (0, "check: subagent shares its parent's checkout")
        assert subagent_stop(agent_wt)[0] == 0, "an isolated subagent that changed nothing was blocked"
        (agent_wt / "made.txt").write_text("x\n")
        (parent_wt / "edit.txt").write_text("x\n")
        assert subagent_stop(parent_wt, agent="other")[0] == 0, "a subagent in its parent's worktree was blocked"
        code, out = subagent_stop(agent_wt)
        assert code == 2 and "this session changed code, but there's no receipts run" in out, out
        assert subagent_stop(agent_wt, stop_hook_active=True)[0] == 0
        sh(sys.executable, str(SCRIPT), "init", cwd=agent_wt)
        sh(sys.executable, str(SCRIPT), "claim", "made.txt exists", cwd=agent_wt)
        code, out = subagent_stop(agent_wt)
        assert code == 2 and "c1 ⚠️ unverified: made.txt exists" in out, out
        sh(sys.executable, str(SCRIPT), "run", "c1", "--", "test", "-f", "made.txt", cwd=agent_wt)
        assert subagent_stop(agent_wt)[0] == 0
        for folder in (agent_wt, parent_wt):
            sh("git", "worktree", "remove", "--force", str(folder), cwd=sub)
        # A host's worktree made inside the checkout, unignored (`claude --worktree` and isolated subagents put
        # theirs in .claude/worktrees/), is another checkout: it isn't this checkout's change.
        nest = Path(tmp, "nest")
        nest.mkdir()
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "nest"]):
            sh("git", *cmd, cwd=nest)
        (nest / "untracked-before.txt").write_text("already here\n")
        nester = {"session_id": "nest-s", "cwd": str(nest)}
        check("--hook", "start", event=nester, cwd=nest)
        sh("git", "worktree", "add", "-q", "-b", "worktree-agent-n1", str(nest / ".claude" / "worktrees" / "agent-n1"), cwd=nest)
        (nest / ".claude" / "worktrees" / "agent-n1" / "inside.txt").write_text("the subagent's\n")
        assert check("--hook", "stop", event=nester, cwd=nest) == (0, "check: no receipt run for this branch"), \
            "a nested worktree counted as the main checkout's change"
        (nest / "real-change.txt").write_text("mine\n")
        assert check("--hook", "stop", event=nester, cwd=nest)[0] == 2
        # A session's start settles what a sweep would, so merged and deleted branches need no manual sweep.
        started = {}
        for name in ("start-merged", "start-dropped"):
            fsh("git", "checkout", "-q", "-b", name, base)
            started[name] = Path(frc("init")).name
            (fin / f"{name}.txt").write_text(name)
            fsh("git", "add", ".")
            fsh("git", "commit", "-qm", name)
        fsh("git", "checkout", "-q", base)
        fsh("git", "merge", "-q", "--no-ff", "start-merged", "-m", "merge start-merged")
        fsh("git", "branch", "-q", "-D", "start-dropped")
        check("--hook", "start", event={"session_id": "f12", "cwd": str(fin)}, cwd=fin)
        assert run_json(started["start-merged"])["finished"]["via"] == "merged"
        assert run_json(started["start-dropped"])["orphaned"]["via"] == "branch gone"
        # Each write records the session that made it, from the shell the agent runs it in; none recorded is null.
        fsh("git", "checkout", "-q", "-b", "provenance", base)
        w1, w2 = {"CLAUDE_CODE_SESSION_ID": "w1"}, {"CLAUDE_CODE_SESSION_ID": "w2"}
        prov = Path(frc("init", extra_env=w1)).name
        frc("claim", "one", extra_env=w1)
        frc("claim", "two", extra_env=w2)
        frc("claim", "three")
        frc("run", "c1", "--", "true", extra_env=w2)
        frc("observe", "c1", "--passed", "looked", extra_env=w1)
        frc("unverified", "c2", "can't", extra_env=w1)
        frc("accept", "c2", extra_env=w2)
        frc("unverified", "c3", "nobody")
        rec = run_json(prov)
        c1, c2, c3 = rec["claims"]
        assert (rec["session"], c1["session"], c2["session"], c3["session"]) == ("w1", "w1", "w2", None), rec
        assert [e["session"] for e in c1["evidence"]] == ["w2", "w1"], c1["evidence"]
        assert (c2["unverified_by"], c2["accepted_by"], c3["unverified_by"]) == ("w1", "w2", None), (c2, c3)
        # One command can be evidence for several claims: it runs once, and each claim gets its own evidence
        # sharing that log. An unknown claim stops it before anything runs.
        count = Path(tmp, "ran-once")
        once = ["sh", "-c", f"echo ran >> '{count}'"]
        assert "unknown claim c9" in frc("run", "c1", "c9", "--", *once, ok=False) and not count.exists()
        out = frc("run", "c1", "c3", "--", *once, extra_env=w1)
        assert count.read_text() == "ran\n" and out.splitlines() == [
            "c1e3: exit 0 (expected success) -> pass", "c3e1: exit 0 (expected success) -> pass"], out
        c1, _, c3 = run_json(prov)["claims"]
        shared = [c1["evidence"][-1], c3["evidence"][-1]]
        assert {(e["command"][0], e["log"]["path"], e["log"]["sha256"], e["session"]) for e in shared} == \
            {("sh", "files/c1e3.log", shared[0]["log"]["sha256"], "w1")} and c3["unverified"] is None, shared
        assert "✅ verified" in frc("render", "--run", prov).split("| c3 |")[1].split("\n")[0]
        assert frc("orphan", "--reason", "done here", extra_env=w2) and run_json(prov)["orphaned"]["session"] == "w2"
        fsh("git", "checkout", "-q", base)
        # /api/runs, for triage: every run with its state and how it settled, its commits, the sessions that wrote
        # it and those only on its branch when a hook fired, and flags for what looks wrong.
        fsh("git", "checkout", "-q", "-b", "triage", base)
        tw = {"CLAUDE_CODE_SESSION_ID": "tw"}
        tri = Path(frc("init", extra_env=tw)).name
        frc("claim", "t is 1", "--unit", "2", extra_env=tw)
        check("--hook", "stop", event={"session_id": "tb", "cwd": str(fin)}, cwd=fin)  # on the branch, wrote nothing
        (fin / "t.txt").write_text("1\n")
        fsh("git", "add", ".")
        fsh("git", "commit", "-qm", "t")
        fsh("git", "checkout", "-q", base)
        fsh("git", "merge", "-q", "--no-ff", "triage", "-m", "merge triage")  # no hook since: nothing settled it
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port), "--idle", "0"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_until(lambda: listening(port), 10)
        listed = {x["id"]: x for x in json.loads(get(port, "/api/runs"))}
        server.terminate()
        server.wait()
        t = listed[tri]
        assert (t["repo"], t["branch"], t["state"], t["writers"], t["unknown_writes"], t["branch_only"], t["units"]) == \
            ("fin", "triage", "open", {"tw": 2}, 0, ["tb"], ["2"]), t
        assert t["commits"]["start"] == t["commits"]["fork"] and t["commits"]["finished"] is None, t["commits"]
        assert {"open on a merged branch", "sessions linked only by branch"} <= set(t["flags"]) and t["attention"], t
        km = listed[swept["kept-merged"]]
        assert km["flags"] == ["no writer recorded"] and not km["attention"], km  # settled: history, not a to-do
        p = listed[prov]
        assert (p["state"], p["via"], p["reason"], p["writers"], p["unknown_writes"]) == \
            ("orphaned", "manual", "done here", {"w1": 6, "w2": 4}, 2), p
        assert "no writer recorded" in listed[swept["still-open"]]["flags"], listed[swept["still-open"]]
        assert listed[swept["kept-merged"]]["state"] == "finished" and listed[swept["kept-merged"]]["via"] == "merged"
        assert list(listed) == sorted(listed, key=lambda i: listed[i]["created_at"] or "", reverse=True)
        # A session that merges its own verified branch into the base branch didn't change code there: the
        # merged branch's run (finished by the gate itself) covers those commits. A commit of its own still counts.
        merger = {"session_id": "f10", "cwd": str(fin)}
        check("--hook", "start", event=merger, cwd=fin)
        fsh("git", "checkout", "-q", "-b", "merged-here")
        merged_run = Path(frc("init")).name
        frc("claim", "b is 1")
        (fin / "b.txt").write_text("1\n")
        frc("run", "c1", "--", "grep", "-q", "1", "b.txt")
        fsh("git", "add", ".")
        fsh("git", "commit", "-qm", "b")
        fsh("git", "checkout", "-q", base)
        fsh("git", "merge", "-q", "--no-ff", "merged-here", "-m", "merge merged-here")
        code, out = check("--hook", "stop", event=merger, cwd=fin)
        assert code == 0 and run_json(merged_run).get("finished"), (out, run_json(merged_run).get("finished"))
        (fin / "b.txt").write_text("2\n")
        fsh("git", "commit", "-qam", "b on base")
        code, out = check("--hook", "stop", event=merger, cwd=fin)
        assert code == 2 and "this session changed code" in out, out

        with open(run / "files" / "c1e2.log", "a") as f:
            f.write("tampered\n")
        assert "hash mismatch" in rc("render", "--run", str(run), ok=False)
        assert "hash mismatch" in rc("publish", "--run", str(run), ok=False)

        # kip.db: made at the current schema on first use, an older one upgraded in place with its rows kept,
        # and concurrent writers queue instead of overwriting each other.
        fresh = {"KIP_RECEIPTS_DIR": str(Path(tmp, "fresh dir #1?"))}  # a path a hand-built file: URI would break on
        assert rc("db", extra_env=fresh) == f"{Path(tmp, 'fresh dir #1?', 'kip.db')} · not created yet"
        assert rc("db", "--migrate", extra_env=fresh).endswith("kip.db · schema v2 of 2 · 0 runs")
        upgrade = f"""import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
import receipt
receipt.db().execute("INSERT INTO runs VALUES ('r1', 'demo', 'main', '2026-01-01T00:00:00+00:00', '{{}}')")
receipt._db = None
receipt.SCHEMA.append("ALTER TABLE runs ADD COLUMN note TEXT")
con = receipt.db()
print(con.execute("PRAGMA user_version").fetchone()[0], con.execute("SELECT id, note FROM runs").fetchall())"""
        assert sh(sys.executable, "-c", upgrade, extra_env=fresh) == "3 [('r1', None)]"
        # Upgrading a v1 kip.db leaves `sessions` as an older Kip (another checkout's hooks) writes it: that still
        # works, and its rows read as starts in an unknown worktree.
        v1 = f"""import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
import receipt
schema = list(receipt.SCHEMA)
del receipt.SCHEMA[1:]
receipt.db()
receipt._db = None
receipt.SCHEMA[:] = schema
receipt.db().execute("INSERT INTO sessions VALUES ('demo', 's1', 'an older Kip, after the upgrade')")
print(receipt.session_states({{"session_id": "s1"}}))"""
        assert sh(sys.executable, "-c", v1, extra_env={"KIP_RECEIPTS_DIR": str(Path(tmp, "v1"))}) == \
            "{'': 'an older Kip, after the upgrade'}"
        conc = Path(rc("init"))
        rc("claim", "written by 20 processes at once", "--run", conc.name)
        writers = [subprocess.Popen([sys.executable, str(SCRIPT), "observe", "c1", "--passed", f"w{i}", "--run", str(conc)],
                                    cwd=repo, env=env, stderr=subprocess.PIPE, text=True) for i in range(20)]
        assert all(w.wait() == 0 for w in writers), [w.stderr.read() for w in writers if w.returncode]
        ev = run_json(conc)["claims"][0]["evidence"]
        assert sorted(e["observation"] for e in ev) == sorted(f"w{i}" for i in range(20)), "lost a concurrent write"
        assert sorted(e["id"] for e in ev) == sorted(f"c1e{i}" for i in range(1, 21)), ev
        assert rc("path", "--run", conc.name) == rc("path", "--run", str(conc)) == str(conc)
        assert "no receipt run nope" in rc("path", "--run", "nope", ok=False)
        store = Path(tmp, "store")
        assert not [p for p in store.rglob("*") if p.name in ("receipt.json", "LATEST") or p.name == "sessions"], \
            "wrote the old JSON store"

        # The JSON store before kip.db is imported once, the first time kip.db opens or on `db --migrate`:
        # runs render as they did, session starts carry over, and the gate finds the branch's run.
        legacy = Path(tmp, "legacy")
        for r in (plan, conc):
            shutil.copytree(r, legacy / "demo" / r.name)
            (legacy / "demo" / r.name / "receipt.json").write_text(json.dumps(run_json(r), indent=2))
        (legacy / "demo" / "sessions").mkdir()
        (legacy / "demo" / "sessions" / "m1").write_text(kipdb("SELECT state FROM session_starts LIMIT 1")[0][0] + "\n")
        old = {"KIP_RECEIPTS_DIR": str(legacy)}
        assert rc("db", "--migrate", extra_env=old).startswith("imported 2 runs and 1 session starts"), "import"
        assert rc("db", "--migrate", extra_env=old).startswith("imported 0 runs and 0 session starts"), "re-import"
        for r in (plan, conc):
            assert rc("render", "--run", r.name, extra_env=old) == rc("render", "--run", r.name), f"{r.name} renders differently"
        # Opening an up-to-date kip.db takes no write lock, so hooks don't queue behind a writer.
        with sqlite3.connect(Path(tmp, "store", "kip.db"), isolation_level=None) as held:
            held.execute("BEGIN IMMEDIATE")
            began = time.time()
            check("--hook", "stop", event={})  # reads this branch's run from kip.db
            assert time.time() - began < 5, "a hook waited on another process's write lock"
            held.execute("ROLLBACK")
        # Sessions opening a new kip.db at once all see the import, which happens once; and a run from an
        # older Kip without created_at or branch still comes across.
        racy = Path(tmp, "racy")
        shutil.copytree(legacy, racy, ignore=shutil.ignore_patterns("kip.db*"))
        bare = racy / "demo" / "20250101-000000-beef"
        bare.mkdir()
        (bare / "receipt.json").write_text(json.dumps({"id": bare.name, "repo": "demo", "level": "full", "claims": []}))
        openers = [subprocess.Popen([sys.executable, str(SCRIPT), "path"], cwd=repo, stdout=subprocess.PIPE, text=True,
                                    env={**env, "KIP_RECEIPTS_DIR": str(racy)}) for _ in range(8)]
        assert {o.communicate()[0].strip() for o in openers} == {str(racy / "demo" / conc.name)}, "an opener missed the import"
        assert rc("db", extra_env={"KIP_RECEIPTS_DIR": str(racy)}).endswith("· 3 runs")
        lazy = Path(tmp, "lazy")
        shutil.copytree(legacy, lazy, ignore=shutil.ignore_patterns("kip.db*"))
        assert rc("path", extra_env={"KIP_RECEIPTS_DIR": str(lazy)}) == str(lazy / "demo" / conc.name), \
            "the first open didn't import, or the gate's branch lookup misses the imported run"

        # The dashboard's segments: every run a session touched (here, across two branches), the issues its runs
        # publish to and its tracker calls name, the outside sources it read; and each run's review panel, whose
        # files are served only if the run recorded them and they haven't changed since.
        seg = {"KIP_RECEIPTS_DIR": str(Path(tmp, "segments"))}
        sh("git", "checkout", "-q", "-b", "seg-a")
        first = Path(rc("init", "--sink", "linear", "--issue", "ENG-9", extra_env=seg))
        rc("claim", "the page renders", "--provider", "frontend", extra_env=seg)
        rc("run", "c1", "--label", "smoke", "--", "true", extra_env=seg)
        shot, clip = Path(tmp, "shot.png"), Path(tmp, "clip.webm")
        shot.write_bytes(b"\x89PNG fake")
        clip.write_bytes(bytes(range(256)) * 4)
        rc("attach", "c1", str(shot), extra_env=seg)
        rc("attach", "c1", str(clip), extra_env=seg)
        sid = {"session_id": "seg1", "cwd": str(repo)}
        check("--hook", "start", event=sid, extra_env=seg)
        sh("git", "checkout", "-q", "-b", "seg-b")
        second = Path(rc("init", extra_env=seg))
        check("--hook", "stop", event=sid, extra_env=seg)
        calls = [("mcp__linear__get_issue", {"id": "ENG-42"}), ("mcp__linear__get_issue", {"id": "ENG-42"}),
                 ("mcp__claude_ai_Atlassian__getJiraIssue", {"cloudId": "c-1", "issueIdOrKey": "PROJ-7"}),
                 ("mcp__github__get_issue", {"title": "FOO-1 is not a tracker key"}),
                 ("mcp__github__get_issue", {"owner": "me", "repo": "demo", "issue_number": 12}),
                 ("WebSearch", {"query": "UTF-8 decode error"}), ("WebFetch", {"url": "https://docs.test/a"}),
                 ("Bash", {"command": "curl -s https://api.test/v1 | jq ."}), ("Read", {"file_path": "app.py"}),
                 ("Bash", {"command": "curl http://127.0.0.1:7377/api/sessions"})]
        for name, tool_input in calls:
            check("--hook", "tool", event={**sid, "tool_name": name, "tool_input": tool_input}, extra_env=seg)
        check("--hook", "prompt", event={**sid, "prompt": "fix it, see https://linear.test/ENG-42."}, extra_env=seg)
        sh("git", "checkout", "-q", "-")
        sh("git", "checkout", "-q", "-")
        port = free_port()
        server = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--port", str(port)], env={**env, **seg},
                                  stdout=subprocess.PIPE, text=True)
        server.stdout.readline()
        try:
            row = next(r for r in json.loads(get(port, "/api/sessions")) if r["session"] == "seg1")
            assert [x["id"] for x in row["runs"]] == [first.name, second.name], row["runs"]
            assert row["runs"][0]["sinks"] == [{"name": "local", "url": None}, {"name": "linear", "url": None}] and \
                row["runs"][0]["issue"] == "ENG-9" and row["runs"][0]["claims"] == {"✅ verified": 1}, row["runs"][0]
            assert sorted((i["key"], i["via"]) for i in row["issues"]) == \
                [("ENG-42", "linear"), ("ENG-9", "linear"), ("PROJ-7", "claude_ai_Atlassian"),
                 ("me/demo#12", "github")], row["issues"]
            assert sorted((s["kind"], s["name"], s["count"]) for s in row["sources"]) == [
                ("link", "https://linear.test/ENG-42", 1), ("mcp", "claude_ai_Atlassian", 1), ("mcp", "github", 2),
                ("mcp", "linear", 2), ("search", "UTF-8 decode error", 1), ("web", "https://api.test/v1", 1),
                ("web", "https://docs.test/a", 1)], row["sources"]
            detail = json.loads(get(port, f"/api/runs/{first.name}"))
            c1 = detail["claims"][0]
            assert c1["verdict"] == "✅ verified" and c1["provider"] == "frontend", c1
            assert c1["evidence"][0]["label"] == "smoke" and c1["evidence"][0]["passed"] is True, c1["evidence"]
            assert c1["evidence"][0]["log"].startswith(f"/runs/{first.name}/files/"), c1["evidence"]
            assert [(f["type"], f["name"]) for f in c1["files"]] == [("image", "c1f1-shot.png"), ("video", "c1f2-clip.webm")]

            def fetch(path, **headers):
                req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, dict(r.headers), r.read()
                except urllib.error.HTTPError as err:
                    return err.code, dict(err.headers), b""

            image, video = (f["url"] for f in c1["files"])
            assert fetch(image)[0::2] == (200, shot.read_bytes()) and fetch(image)[1]["Content-Type"] == "image/png"
            status, headers, body = fetch(video, Range="bytes=10-19")
            assert (status, body, headers["Content-Range"]) == (206, clip.read_bytes()[10:20], "bytes 10-19/1024"), headers
            assert fetch(video, Range="bytes=-4")[2] == clip.read_bytes()[-4:]
            assert fetch(video, Range="bytes=5000-")[0] == 416
            log = fetch(c1["evidence"][0]["log"])
            assert log[0] == 200 and log[1]["Content-Type"].startswith("text/plain"), log[:2]
            for path in (f"/runs/{first.name}/files/nope.png", f"/runs/{first.name}/files/..%2F..%2Fkip.db",
                         f"/runs/{first.name}/files/../../../kip.db", f"/runs/nope/files/c1f1-shot.png",
                         "/api/runs/nope"):
                assert fetch(path)[0] == 404, path
            with open(first / "files" / "c1f1-shot.png", "ab") as f:
                f.write(b"tampered")
            assert fetch(image)[0] == 404, "served a file that changed after it was recorded"
        finally:
            server.terminate()
    print("receipt.py: all checks passed")


if __name__ == "__main__":
    main()
