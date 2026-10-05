#!/usr/bin/env python3
"""Self-check for receipt.py and the sink interface: python3 test_receipt.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("receipt.py")
DESKTEST = Path(__file__).with_name("playwright") / "desktest.py"

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
FAKE_SINK = '''
import json, os
from pathlib import Path
FORMAT = "markdown"
REQUIRES = ["FAKE_SINK_LOG"]
def _log(*event):
    p = Path(os.environ["FAKE_SINK_LOG"])
    p.write_text(json.dumps((json.loads(p.read_text()) if p.exists() else []) + [list(event)]))
def upload(issue, path, content_type):
    _log("upload", issue, path.name, content_type)
    return "https://fake.test/" + path.name
def publish(issue, body, comment_id):
    _log("update" if comment_id else "create", issue, body)
    return comment_id or "cm1", "https://fake.test/" + issue + "#cm1"
'''


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp, "demo")
        repo.mkdir()
        sink_log = Path(tmp, "sink.json")
        env = {**os.environ, "KIP_RECEIPTS_DIR": str(Path(tmp, "store")), "FAKE_API_TOKEN": "supersecret123",
               "FAKE_SINK_LOG": str(sink_log), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for v in ("LINEAR_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            env.pop(v, None)

        def sh(*args, ok=True, extra_env=None):
            r = subprocess.run(args, cwd=repo, env={**env, **(extra_env or {})}, capture_output=True, text=True)
            assert (r.returncode == 0) == ok, f"{args}: exit {r.returncode}\n{r.stdout}{r.stderr}"
            return (r.stdout + r.stderr).strip()

        def rc(*args, **kw):
            return sh(sys.executable, str(SCRIPT), *args, **kw)

        (repo / "app.py").write_text('print("hi")\n')
        (repo / ".kip" / "sinks").mkdir(parents=True)
        (repo / ".kip" / "sinks" / "fake.py").write_text(FAKE_SINK)
        sh("git", "init", "-q")
        sh("git", "add", ".")
        sh("git", "commit", "-qm", "init")

        # Local by default; cloud sinks are opt-in, need an issue; unknown sinks are refused.
        assert "--issue is required" in rc("init", "--sink", "fake", ok=False)
        assert "unknown sink nope" in rc("init", "--sink", "nope", ok=False)
        sinks = rc("sinks")
        assert "local" in sinks and "fake       ready" in sinks and "linear     missing LINEAR_API_KEY" in sinks, sinks
        opted = Path(rc("init", "--sink", "fake", "--issue", "ENG-9"))
        assert json.loads((opted / "receipt.json").read_text())["sinks"] == ["local", "fake"]

        run = Path(rc("init", "--level", "full"))
        rec = json.loads((run / "receipt.json").read_text())
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

        shot = repo / "shot.png"
        shot.write_bytes(b"\x89PNG fake")
        rc("attach", "c1", str(shot))

        md = rc("render")
        assert "| c1 | app prints hi | generic |" in md and "✅ verified" in md, md
        assert "⚠️ unverified: needs PDF service" in md, md
        assert "❌ check can't fail" in md, md
        assert "❌ refuted" in md and "exit 127" in md, md
        assert "| c6 | billing claim at ultra floor | generic | `true` (exit 0) | ⚠️ falsifiability not shown |" in md, md
        assert "`c1f1-shot.png` (sha256" in md and "not uploaded" in md, md
        assert md.endswith(f"`kip-receipt {run.name}`"), md
        assert md == rc("render"), "render is not deterministic"

        jira = rc("render", "--sink", "jira")
        assert jira.startswith("h3. Receipts") and "{{c1f1-shot.png}}" in jira and "not uploaded" in jira, jira

        log = (run / "files" / "c2e1.log").read_text()
        assert "supersecret123" not in log and "[FAKE_API_TOKEN]" in log, log
        assert (run / "files" / "c2e1.patch").exists(), "dirty tree not captured"

        # Local-only run: publish stays local until the run opts into a sink.
        assert f"local only: {run}" in rc("publish")
        assert not sink_log.exists(), "local-only publish touched a sink"
        assert "--issue is required" in rc("publish", "--sink", "fake", ok=False)
        assert json.loads((run / "receipt.json").read_text())["sinks"] == ["local"], "failed opt-in was saved"

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
        rec = json.loads((run / "receipt.json").read_text())
        assert rec["published"]["fake"] == {"comment_id": "cm1", "url": "https://fake.test/ENG-1#cm1"}, rec
        assert "linear" not in rec["published"] and rec["sinks"] == ["local", "fake", "linear"], rec
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
        rec = json.loads((remote / "receipt.json").read_text())
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
        assert "unknown viewport watch" in sh(sys.executable, str(DESKTEST), "c1", "u", str(spec), "--viewports",
                                              "watch", ok=False, extra_env={"KIP_PLAYWRIGHT": str(pw)})
        spec.write_text("// edited after the run\n")
        assert "hash mismatch for specs/c1.spec.ts" in rc("render", "--run", str(remote), ok=False)

        with open(run / "files" / "c1e2.log", "a") as f:
            f.write("tampered\n")
        assert "hash mismatch" in rc("render", "--run", str(run), ok=False)
        assert "hash mismatch" in rc("publish", "--run", str(run), ok=False)
    print("receipt.py: all checks passed")


if __name__ == "__main__":
    main()
