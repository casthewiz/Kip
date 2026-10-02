#!/usr/bin/env python3
"""Deterministic receipts: record claims and evidence, publish them to sinks.

  receipt.py sinks                                          list sinks and whether each is configured
  receipt.py init [--sink NAME --issue KEY] [--level lite|full|ultra]
                                                            start a run (local only by default), print its dir
  receipt.py claim "TEXT" [--provider NAME] [--level L]     add a claim, print its id
  receipt.py run CLAIM [--red] [--static] -- CMD ...        run CMD, record the result
  receipt.py observe CLAIM (--passed|--failed) "TEXT"       record a non-command observation
  receipt.py attach CLAIM PATH [--type image|video|log]     copy a file into the run
  receipt.py unverified CLAIM "REASON"                      mark a claim as unverifiable
  receipt.py render [--sink NAME]                           print the ledger as that sink would post it
  receipt.py publish [--sink NAME --issue KEY]              upload files and upsert the comment on each sink,
                                                            optionally opting the run into another sink first
  receipt.py path                                           print the run dir

Every command takes --run DIR; default is the latest run for the current repo.
Runs live in $KIP_RECEIPTS_DIR (default ~/.kip/receipts)/<repo>/<run-id>/.
Sinks are modules in <repo>/.kip/sinks/ or this script's sinks/ (see sinks/CONTRACT.md).
Standard library only, Python 3.8+.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("KIP_RECEIPTS_DIR", Path.home() / ".kip" / "receipts"))
SECRET_ENV = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)
EXCERPT_LINES = 15
FILE_TYPES = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
              ".mp4": "video", ".webm": "video", ".mov": "video"}


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def sink_dirs():
    """Project sinks first, so a repo can add or override one."""
    top = git("rev-parse", "--show-toplevel")
    return ([Path(top) / ".kip" / "sinks"] if top else []) + [Path(__file__).resolve().parent / "sinks"]


def sink_names():
    names = {p.stem for d in sink_dirs() if d.is_dir() for p in d.glob("*.py") if not p.stem.startswith("_")}
    return ["local"] + sorted(names - {"local"})


def load_sink(name):
    for d in sink_dirs():
        p = d / f"{name}.py"
        if p.exists():
            spec = importlib.util.spec_from_file_location(f"kip_sink_{name}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    sys.exit(f"unknown sink {name}; available: {', '.join(sink_names())}")


def missing_env(mod):
    return [v for v in getattr(mod, "REQUIRES", []) if not os.environ.get(v)]


def repo_name():
    top = git("rev-parse", "--show-toplevel")
    return Path(top).name if top else Path.cwd().name


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def redact(text):
    for k, v in os.environ.items():
        if SECRET_ENV.search(k) and len(v) >= 8:
            text = text.replace(v, f"[{k}]")
    return text


def run_dir(a):
    if a.run:
        return Path(a.run)
    latest = ROOT / repo_name() / "LATEST"
    if not latest.exists():
        sys.exit("no receipt run for this repo; start one with `receipt.py init`")
    return latest.parent / latest.read_text().strip()


def load(d):
    return json.loads((d / "receipt.json").read_text())


def save(d, rec):
    (d / "receipt.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")


def find_claim(rec, cid):
    for c in rec["claims"]:
        if c["id"] == cid:
            return c
    sys.exit(f"unknown claim {cid}; claims: {', '.join(c['id'] for c in rec['claims']) or 'none'}")


def code_state(d, eid):
    """Pin the code an evidence ran against: HEAD, plus the uncommitted diff if any."""
    state = {"commit": git("rev-parse", "HEAD"), "patch": None}
    diff = git("diff", "HEAD")
    if diff:
        # ponytail: untracked files aren't in the patch; add `git add -N` capture if that bites.
        p = d / "files" / f"{eid}.patch"
        p.write_text(diff + "\n")
        state["patch"] = {"path": f"files/{p.name}", "sha256": sha256(p)}
    return state


def new_evidence(d, c, **fields):
    eid = f"{c['id']}e{len(c['evidence']) + 1}"
    e = {"id": eid, **fields, **code_state(d, eid)}
    c["evidence"].append(e)
    return e


def cmd_sinks(a):
    for name in sink_names():
        missing = [] if name == "local" else missing_env(load_sink(name))
        print(f"{name:10} {'always on' if name == 'local' else 'missing ' + ', '.join(missing) if missing else 'ready'}")


def check_sinks(sinks, issue):
    """Local is always first; cloud sinks are opt-in and need an issue to post to."""
    sinks = list(dict.fromkeys(["local", *sinks]))
    unknown = set(sinks) - set(sink_names())
    if unknown:
        sys.exit(f"unknown sink {', '.join(sorted(unknown))}; available: {', '.join(sink_names())}")
    if len(sinks) > 1 and not issue:
        sys.exit(f"--issue is required to publish to {', '.join(sinks[1:])}")
    return sinks


def cmd_init(a):
    sinks = check_sinks(a.sink, a.issue)
    now = datetime.datetime.now(datetime.timezone.utc)
    rid = now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = ROOT / repo_name() / rid
    (d / "files").mkdir(parents=True)
    save(d, {"id": rid, "created_at": now.isoformat(timespec="seconds"), "repo": repo_name(),
             "commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
             "level": a.level, "issue": a.issue, "sinks": sinks, "published": {}, "claims": []})
    (d.parent / "LATEST").write_text(rid + "\n")
    print(d)


def cmd_claim(a):
    d = run_dir(a)
    rec = load(d)
    cid = f"c{len(rec['claims']) + 1}"
    rec["claims"].append({"id": cid, "text": a.text, "provider": a.provider, "level": a.level,
                          "evidence": [], "files": [], "unverified": None})
    save(d, rec)
    print(cid)


def cmd_run(a):
    cmd = a.cmd
    if not cmd:
        sys.exit("usage: receipt.py run CLAIM [--red] [--static] -- CMD ...")
    d = run_dir(a)
    rec = load(d)
    c = find_claim(rec, a.claim)
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        code, out = r.returncode, redact(r.stdout)
    except OSError as err:
        code, out = 127, f"{cmd[0]}: {err.strerror}\n"
    passed = code != 0 if a.red else code == 0
    e = new_evidence(d, c, kind="static" if a.static else "command", phase="red" if a.red else "green",
                     command=cmd, cwd=os.getcwd(), exit_code=code, passed=passed,
                     excerpt="\n".join(out.strip().splitlines()[-EXCERPT_LINES:]))
    log = d / "files" / f"{e['id']}.log"
    log.write_text(out)
    e["log"] = {"path": f"files/{log.name}", "sha256": sha256(log)}
    save(d, rec)
    expected = "expected failure" if a.red else "expected success"
    print(f"{e['id']}: exit {code} ({expected}) -> {'pass' if passed else 'FAIL'}")


def cmd_observe(a):
    d = run_dir(a)
    rec = load(d)
    e = new_evidence(d, find_claim(rec, a.claim), kind="observed", phase="green",
                     observation=a.text, passed=a.passed)
    save(d, rec)
    print(e["id"])


def cmd_attach(a):
    src = Path(a.path)
    if not src.is_file():
        sys.exit(f"no such file: {src}")
    d = run_dir(a)
    rec = load(d)
    c = find_claim(rec, a.claim)
    dest = d / "files" / f"{c['id']}f{len(c['files']) + 1}-{src.name}"
    shutil.copy2(src, dest)
    c["files"].append({"type": a.type or FILE_TYPES.get(src.suffix.lower(), "log"),
                       "path": f"files/{dest.name}", "sha256": sha256(dest), "uploads": {}})
    save(d, rec)
    print(dest)


def cmd_unverified(a):
    d = run_dir(a)
    rec = load(d)
    find_claim(rec, a.claim)["unverified"] = a.reason
    save(d, rec)


def verdict(c, level):
    level = c.get("level") or level
    ev = c["evidence"]
    if any(not e["passed"] for e in ev if e["phase"] == "green"):
        return "❌ refuted"
    if any(not e["passed"] for e in ev if e["phase"] == "red"):
        return "❌ check can't fail"
    if c["unverified"] or not ev:
        return "⚠️ unverified"
    if level == "ultra" and not any(e["phase"] == "red" for e in ev):
        return "⚠️ falsifiability not shown"
    return "✅ verified"


def verify_hashes(d, rec):
    """Refuse to render evidence whose files changed since they were recorded."""
    entries = [f for c in rec["claims"] for f in c["files"]]
    entries += [e[k] for c in rec["claims"] for e in c["evidence"] for k in ("log", "patch") if e.get(k)]
    for f in entries:
        p = d / f["path"]
        if p.exists() and sha256(p) != f["sha256"]:
            sys.exit(f"hash mismatch for {f['path']}: file changed after it was recorded")


def summarize(e):
    if e["kind"] == "observed":
        return f"observed: {e['observation']}"
    red = ", red" if e["phase"] == "red" else ""
    return f"`{shlex.join(e['command'])}` (exit {e['exit_code']}{red})"


def render_markdown(rec, sink=None):
    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    out = [f"### Receipts · {rec['repo']} @ `{(rec['commit'] or '?')[:7]}` ({rec['branch']}) · {rec['level']}", "",
           "| # | Claim | Provider | Evidence | Verdict |", "|---|---|---|---|---|"]
    for c in rec["claims"]:
        v = verdict(c, rec["level"]) + (f": {c['unverified']}" if c["unverified"] else "")
        ev = "<br>".join(cell(summarize(e)) for e in c["evidence"]) or "—"
        out.append(f"| {c['id']} | {cell(c['text'])} | {c['provider'] or 'generic'} | {ev} | {cell(v)} |")
    for c in rec["claims"]:
        if not c["evidence"] and not c["files"]:
            continue
        out += ["", f"**{c['id']}**: {c['text']}"]
        for e in c["evidence"]:
            meta = [f"commit `{(e['commit'] or '?')[:7]}`"] + (["uncommitted changes"] if e["patch"] else [])
            if e.get("log"):
                meta.append(f"log sha256 `{e['log']['sha256'][:12]}`")
            out += ["", f"- {summarize(e)} · {' · '.join(meta)}"]
            if e.get("excerpt"):
                out += ["", "```", e["excerpt"], "```"]
        for f in c["files"]:
            name = Path(f["path"]).name
            url = f["uploads"].get(sink)
            if url and f["type"] == "image":
                out += ["", f"![{name}]({url})"]
            elif url:
                out += ["", f"[{f['type']}: {name}]({url})"]
            else:
                out += ["", f"- {f['type']}: `{name}` (sha256 `{f['sha256'][:12]}`, not uploaded)"]
    out += ["", f"`kip-receipt {rec['id']}`"]
    return "\n".join(out)


def render_jira(rec, sink=None):
    """Jira wiki markup (REST API v2). Uploaded files are referenced by attachment name."""
    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    def jsum(e):
        return re.sub(r"`([^`]+)`", r"{{\1}}", summarize(e))

    out = [f"h3. Receipts · {rec['repo']} @ {{{{{(rec['commit'] or '?')[:7]}}}}} ({rec['branch']}) · {rec['level']}",
           "", "||#||Claim||Provider||Evidence||Verdict||"]
    for c in rec["claims"]:
        v = verdict(c, rec["level"]) + (f": {c['unverified']}" if c["unverified"] else "")
        ev = " \\\\ ".join(cell(jsum(e)) for e in c["evidence"]) or "—"
        out.append(f"|{c['id']}|{cell(c['text'])}|{c['provider'] or 'generic'}|{ev}|{cell(v)}|")
    for c in rec["claims"]:
        if not c["evidence"] and not c["files"]:
            continue
        out += ["", f"*{c['id']}*: {c['text']}"]
        for e in c["evidence"]:
            meta = [f"commit {{{{{(e['commit'] or '?')[:7]}}}}}"] + (["uncommitted changes"] if e["patch"] else [])
            if e.get("log"):
                meta.append(f"log sha256 {{{{{e['log']['sha256'][:12]}}}}}")
            out += ["", f"* {jsum(e)} · {' · '.join(meta)}"]
            if e.get("excerpt"):
                out += ["{noformat}", e["excerpt"], "{noformat}"]
        for f in c["files"]:
            name = f["uploads"].get(sink)
            if not name:
                out += ["", f"* {f['type']}: {{{{{Path(f['path']).name}}}}} (sha256 {{{{{f['sha256'][:12]}}}}}, not uploaded)"]
            else:
                out += ["", f"!{name}|thumbnail!" if f["type"] == "image" else f"[^{name}]"]
    out += ["", f"{{{{kip-receipt {rec['id']}}}}}"]
    return "\n".join(out)


RENDERERS = {"markdown": render_markdown, "jira": render_jira}


def render(rec, sink=None):
    fmt = getattr(load_sink(sink), "FORMAT", "markdown") if sink and sink != "local" else "markdown"
    return RENDERERS[fmt](rec, sink)


def cmd_render(a):
    d = run_dir(a)
    rec = load(d)
    verify_hashes(d, rec)
    print(render(rec, a.sink))


def cmd_publish(a):
    """Upload each file once per sink, then create or update that sink's single comment."""
    d = run_dir(a)
    rec = load(d)
    verify_hashes(d, rec)
    if a.sink:
        if a.issue and rec["issue"] and a.issue != rec["issue"]:
            sys.exit(f"run is for {rec['issue']}, not {a.issue}; start a new run for a different issue")
        rec["issue"] = rec["issue"] or a.issue
        rec["sinks"] = check_sinks(rec["sinks"] + a.sink, rec["issue"])
        save(d, rec)
    failed = []
    for name in [s for s in rec["sinks"] if s != "local"]:
        mod = load_sink(name)
        missing = missing_env(mod)
        if missing:
            print(f"{name}: skipped, missing {', '.join(missing)}")
            failed.append(name)
            continue
        try:
            for f in (f for c in rec["claims"] for f in c["files"] if name not in f["uploads"]):
                path = d / f["path"]
                f["uploads"][name] = mod.upload(rec["issue"], path,
                                                mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                save(d, rec)
            prior = rec["published"].get(name, {}).get("comment_id")
            comment_id, url = mod.publish(rec["issue"], render(rec, name), prior)
        except Exception as err:  # one sink failing shouldn't stop the others
            print(f"{name}: failed: {redact(str(err))}")
            failed.append(name)
            continue
        rec["published"][name] = {"comment_id": comment_id, "url": url}
        save(d, rec)
        print(f"{name}: {'updated' if prior else 'posted'} {url}")
    if not [s for s in rec["sinks"] if s != "local"]:
        print(f"local only: {d}")
    if failed:
        sys.exit(1)


def cmd_path(a):
    print(run_dir(a))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run", help="run dir (default: latest run for this repo)")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sinks", parents=[common])
    s.set_defaults(fn=cmd_sinks)

    s = sub.add_parser("init", parents=[common])
    s.add_argument("--sink", action="append", default=[],
                   help="also publish to this sink (opt-in, confirmed with the user); local is always on")
    s.add_argument("--issue")
    s.add_argument("--level", choices=["lite", "full", "ultra"], default="full")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("claim", parents=[common])
    s.add_argument("text")
    s.add_argument("--provider")
    s.add_argument("--level", choices=["lite", "full", "ultra"], help="override the run's level (e.g. ultra floor)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("run", parents=[common])
    s.add_argument("claim")
    s.add_argument("--red", action="store_true", help="falsifiability run: expected to fail")
    s.add_argument("--static", action="store_true", help="typecheck/lint/grep, not behavior")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("observe", parents=[common])
    s.add_argument("claim")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--passed", dest="passed", action="store_true")
    g.add_argument("--failed", dest="passed", action="store_false")
    s.add_argument("text")
    s.set_defaults(fn=cmd_observe)

    s = sub.add_parser("attach", parents=[common])
    s.add_argument("claim")
    s.add_argument("path")
    s.add_argument("--type", choices=["image", "video", "log"])
    s.set_defaults(fn=cmd_attach)

    s = sub.add_parser("unverified", parents=[common])
    s.add_argument("claim")
    s.add_argument("reason")
    s.set_defaults(fn=cmd_unverified)

    s = sub.add_parser("render", parents=[common])
    s.add_argument("--sink", help="render exactly what this sink would post")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("publish", parents=[common])
    s.add_argument("--sink", action="append", default=[], help="opt this run into another sink first")
    s.add_argument("--issue", help="issue for newly added sinks, if the run has none")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("path", parents=[common])
    s.set_defaults(fn=cmd_path)

    # Split off the wrapped command ourselves; argparse.REMAINDER swallows run's own flags.
    argv, cmd = sys.argv[1:], []
    if argv[:1] == ["run"] and "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    a.cmd = cmd
    a.fn(a)


if __name__ == "__main__":
    main()
