#!/usr/bin/env python3
"""Self-check for receipt.py and the sink interface: python3 test_receipt.py"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
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

# Stands in for gh: one pull request whose body lives in $FAKE_PR_BODY.
FAKE_GH = f'''#!/bin/sh
case "$1 $2" in
  "pr view") {sys.executable} -c 'import json, os, sys; print(json.dumps({{"body": open(os.environ["FAKE_PR_BODY"]).read(), "url": "https://github.test/pull/" + sys.argv[1], "headRefOid": os.environ["FAKE_PR_HEAD"]}}))' "$3";;
  "pr edit") cat > "$FAKE_PR_BODY";;
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
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for v in ("LINEAR_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            env.pop(v, None)

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
        assert inputs.get("specs/c1.spec.ts") is True and inputs.get(str(pw.resolve())) is False, inputs
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
        assert "❌ refuted" in md, md
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
        spec.write_text("// edited after the run\n")
        assert "hash mismatch for specs/c1.spec.ts" in rc("render", "--run", str(remote), ok=False)

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
        ghenv = {"PATH": f"{bin_}{os.pathsep}{env['PATH']}", "FAKE_PR_BODY": str(body),
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
        assert code == 2 and "c1 ⚠️ unverified: `tests` passes" in out and "c2" not in out, out
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
        rc("init", "--level", "ultra")
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

        with open(run / "files" / "c1e2.log", "a") as f:
            f.write("tampered\n")
        assert "hash mismatch" in rc("render", "--run", str(run), ok=False)
        assert "hash mismatch" in rc("publish", "--run", str(run), ok=False)

        # kip.db: made at the current schema on first use, an older one upgraded in place with its rows kept,
        # and concurrent writers queue instead of overwriting each other.
        fresh = {"KIP_RECEIPTS_DIR": str(Path(tmp, "fresh dir #1?"))}  # a path a hand-built file: URI would break on
        assert rc("db", extra_env=fresh) == f"{Path(tmp, 'fresh dir #1?', 'kip.db')} · not created yet"
        assert rc("db", "--migrate", extra_env=fresh).endswith("kip.db · schema v1 of 1 · 0 runs")
        upgrade = f"""import sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
import receipt
receipt.db().execute("INSERT INTO runs VALUES ('r1', 'demo', 'main', '2026-01-01T00:00:00+00:00', '{{}}')")
receipt._db = None
receipt.SCHEMA.append("ALTER TABLE runs ADD COLUMN note TEXT")
con = receipt.db()
print(con.execute("PRAGMA user_version").fetchone()[0], con.execute("SELECT id, note FROM runs").fetchall())"""
        assert sh(sys.executable, "-c", upgrade, extra_env=fresh) == "2 [('r1', None)]"
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
        (legacy / "demo" / "sessions" / "m1").write_text(kipdb("SELECT state FROM sessions LIMIT 1")[0][0] + "\n")
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
    print("receipt.py: all checks passed")


if __name__ == "__main__":
    main()
