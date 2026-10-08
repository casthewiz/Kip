#!/usr/bin/env python3
"""Deterministic receipts: record claims and evidence, publish them to sinks.

  receipt.py sinks                                          list destinations and whether each is ready
  receipt.py init [--sink NAME --issue KEY] [--target URL] [--level lite|full|ultra]
                                                            start a run (local only by default), print its dir
  receipt.py claim "TEXT" [--provider NAME] [--level L] [--unit N]
                                                            add a claim, print its id
  receipt.py run CLAIM [--red] [--static] [--collect DIR] [--label TEXT] -- CMD ...
                                                            run CMD, record the result, attach media from DIR
  receipt.py observe CLAIM (--passed|--failed) "TEXT"       record a non-command observation
  receipt.py attach CLAIM PATH [--type image|video|log]     copy a file into the run
  receipt.py unverified CLAIM "REASON"                      mark a claim as unverifiable
  receipt.py render [--sink NAME]                           print the ledger as that sink would post it
  receipt.py publish [--sink NAME --issue KEY]              upload files and upsert the comment on each sink,
                                                            optionally opting the run into another sink first
  receipt.py record --sink NAME --url URL [--comment-id ID] record a comment the agent posted (agent-backed sinks)
  receipt.py path                                           print the run dir
  receipt.py check [--hook stop|pr]                         exit 2 if this branch's run has open claims
                                                            and the code changed since init (host hooks)

Every command takes --run DIR; default is the latest run for the current repo.
Runs live in $KIP_RECEIPTS_DIR (default ~/.kip/receipts)/<repo>/<run-id>/.
Sinks are the `destinations` in Kip's config (kip.py config); script-backed ones are modules
in <repo>/.kip/sinks/ or this script's sinks/ (see sinks/CONTRACT.md).
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

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "kip.py").is_file())))
import kip  # noqa: E402

ROOT = Path(os.environ.get("KIP_RECEIPTS_DIR", Path.home() / ".kip" / "receipts"))
EXCERPT_LINES = 15
FILE_TYPES = {".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
              ".mp4": "video", ".webm": "video", ".mov": "video", ".zip": "trace"}


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def sink_dirs():
    """Project sinks first, so a repo can add or override one."""
    top = git("rev-parse", "--show-toplevel")
    return ([Path(top) / ".kip" / "sinks"] if top else []) + [Path(__file__).resolve().parent / "sinks"]


def destinations():
    """Configured destinations, plus one per project sink module the config doesn't name."""
    cfg = kip.load()
    top = git("rev-parse", "--show-toplevel")
    found = {p.stem: {"type": p.stem} for p in (Path(top, ".kip", "sinks").glob("*.py") if top else [])
             if not p.stem.startswith("_")}
    return {"local": {"type": "local"}, **found, **cfg["destinations"]}, cfg


def sink_names():
    return list(destinations()[0])


def load_sink(name):
    module = destinations()[0].get(name, {}).get("type", name)
    for d in sink_dirs():
        p = d / f"{module}.py"
        if p.exists():
            spec = importlib.util.spec_from_file_location(f"kip_sink_{module}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    sys.exit(f"no sink module {module} for {name}; available: {', '.join(sink_names())}")


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
        if kip.SECRET_ENV.search(k) and len(v) >= 8:
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


def fingerprint():
    """Hash of HEAD, the diff, and untracked names: changes once any work lands after init."""
    state = "\n".join(git(*a) or "" for a in (["rev-parse", "HEAD"], ["diff", "HEAD"], ["status", "--porcelain"]))
    return hashlib.sha256(state.encode()).hexdigest()


def code_state(d, rec, eid):
    """Pin the code an evidence ran against: HEAD, plus the uncommitted diff if any.
    A remote target isn't this checkout, so there's nothing local to pin."""
    if rec.get("target"):
        return {"commit": None, "patch": None, "remote": True}
    state = {"commit": git("rev-parse", "HEAD"), "patch": None}
    diff = git("diff", "HEAD")
    if diff:
        # ponytail: untracked files aren't in the patch; add `git add -N` capture if that bites.
        p = d / "files" / f"{eid}.patch"
        p.write_text(diff + "\n")
        state["patch"] = {"path": f"files/{p.name}", "sha256": sha256(p)}
    return state


def new_evidence(d, rec, c, **fields):
    eid = f"{c['id']}e{len(c['evidence']) + 1}"
    e = {"id": eid, **fields, **code_state(d, rec, eid)}
    c["evidence"].append(e)
    return e


def rel_to(path, d):
    try:
        return path.resolve().relative_to(d.resolve())
    except ValueError:
        return None


def command_inputs(cmd, d):
    """Hash every file a command names (specs, configs, scripts), so a rerun can tell
    whether it's running the same inputs. Files inside the run dir are tamper-checked."""
    inputs = []
    for arg in cmd:
        p = Path(arg)
        if p.is_file():
            inside = rel_to(p, d)
            inputs.append({"path": str(inside) if inside else str(p.resolve()), "inside_run": bool(inside),
                           "sha256": sha256(p)})
    return inputs


def attach_file(d, c, src, kind=None, name=None):
    dest = d / "files" / f"{c['id']}f{len(c['files']) + 1}-{name or src.name}"
    shutil.copy2(src, dest)
    c["files"].append({"type": kind or FILE_TYPES.get(src.suffix.lower(), "log"),
                       "path": f"files/{dest.name}", "sha256": sha256(dest), "uploads": {}})
    return dest


def cmd_sinks(a):
    dests, cfg = destinations()
    for name, entry in dests.items():
        s = "always on" if name == "local" else kip.status(cfg, entry)
        print(f"{name:10} {s}" + (" (recommended)" if name == cfg.get("default_destination", "local") else ""))


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
    (d / "specs").mkdir()  # test code written for this run lives with its evidence
    save(d, {"id": rid, "created_at": now.isoformat(timespec="seconds"), "repo": repo_name(),
             "commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"), "baseline": fingerprint(),
             "level": a.level, "issue": a.issue, "target": a.target, "sinks": sinks, "published": {},
             "claims": []})
    (d.parent / "LATEST").write_text(rid + "\n")
    print(d)


def cmd_claim(a):
    d = run_dir(a)
    rec = load(d)
    cid = f"c{len(rec['claims']) + 1}"
    rec["claims"].append({"id": cid, "text": a.text, "unit": a.unit, "provider": a.provider, "level": a.level,
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
    e = new_evidence(d, rec, c, kind="static" if a.static else "command", phase="red" if a.red else "green",
                     label=a.label, command=cmd, cwd=os.getcwd(), inputs=command_inputs(cmd, d), exit_code=code, passed=passed,
                     excerpt="\n".join(out.strip().splitlines()[-EXCERPT_LINES:]))
    log = d / "files" / f"{e['id']}.log"
    log.write_text(out)
    e["log"] = {"path": f"files/{log.name}", "sha256": sha256(log)}
    collected = []
    if a.collect and Path(a.collect).is_dir():
        root = Path(a.collect)
        for p in sorted(root.rglob("*")):
            kind = FILE_TYPES.get(p.suffix.lower())
            if p.is_file() and kind:
                name = f"{e['id']}-" + "-".join(p.relative_to(root).parts)
                collected.append(attach_file(d, c, p, kind, name).name)
    save(d, rec)
    expected = "expected failure" if a.red else "expected success"
    print(f"{e['id']}: exit {code} ({expected}) -> {'pass' if passed else 'FAIL'}"
          + (f", collected {len(collected)} file(s)" if a.collect else ""))


def cmd_observe(a):
    d = run_dir(a)
    rec = load(d)
    e = new_evidence(d, rec, find_claim(rec, a.claim), kind="observed", phase="green",
                     observation=a.text, passed=a.passed)
    save(d, rec)
    print(e["id"])


def cmd_attach(a):
    src = Path(a.path)
    if not src.is_file():
        sys.exit(f"no such file: {src}")
    d = run_dir(a)
    rec = load(d)
    dest = attach_file(d, find_claim(rec, a.claim), src, a.type)
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
    entries += [i for c in rec["claims"] for e in c["evidence"] for i in e.get("inputs", []) if i["inside_run"]]
    for f in entries:
        p = d / f["path"]
        if p.exists() and sha256(p) != f["sha256"]:
            sys.exit(f"hash mismatch for {f['path']}: file changed after it was recorded")


def summarize(e, full=False):
    """Table rows use the evidence's label when it has one; details always show the full command."""
    if e["kind"] == "observed":
        return f"observed: {e['observation']}"
    red = ", red" if e["phase"] == "red" else ""
    cmd = f"`{shlex.join(e['command'])}`"
    label = e.get("label")
    head = (f"{label}: {cmd}" if label else cmd) if full else (label or cmd)
    return f"{head} (exit {e['exit_code']}{red})"


def subject(rec, code):
    if rec.get("target"):
        return f"{rec['target']} (remote: state not pinned)"
    return f"{rec['repo']} @ {code((rec['commit'] or '?')[:7])} ({rec['branch']})"


def evidence_meta(e, code):
    meta = ["remote"] if e.get("remote") else \
        [f"commit {code((e['commit'] or '?')[:7])}"] + (["uncommitted changes"] if e["patch"] else [])
    if e.get("log"):
        meta.append(f"log sha256 {code(e['log']['sha256'][:12])}")
    return " · ".join(meta)


def has_units(rec):
    """Runs planned by kip-decompose tag claims with their unit; others keep the original table."""
    return any(c.get("unit") for c in rec["claims"])


def render_markdown(rec, sink=None):
    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    def code(s):
        return f"`{s}`"

    units = has_units(rec)
    out = [f"### Receipts · {subject(rec, code)} · {rec['level']}", "",
           "| # |" + (" Unit |" if units else "") + " Claim | Provider | Evidence | Verdict |",
           "|---|" + ("---|" if units else "") + "---|---|---|---|"]
    for c in rec["claims"]:
        v = verdict(c, rec["level"]) + (f": {c['unverified']}" if c["unverified"] else "")
        ev = "<br>".join(cell(summarize(e)) for e in c["evidence"]) or "—"
        unit = f" {cell(c.get('unit') or '—')} |" if units else ""
        out.append(f"| {c['id']} |{unit} {cell(c['text'])} | {c['provider'] or 'generic'} | {ev} | {cell(v)} |")
    for c in rec["claims"]:
        if not c["evidence"] and not c["files"]:
            continue
        out += ["", f"**{c['id']}**: {c['text']}"]
        for e in c["evidence"]:
            out += ["", f"- {summarize(e, full=True)} · {evidence_meta(e, code)}"]
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

    def jsum(e, full=False):
        return re.sub(r"`([^`]+)`", r"{{\1}}", summarize(e, full))

    def code(s):
        return f"{{{{{s}}}}}"

    units = has_units(rec)
    out = [f"h3. Receipts · {subject(rec, code)} · {rec['level']}", "",
           "||#||" + ("Unit||" if units else "") + "Claim||Provider||Evidence||Verdict||"]
    for c in rec["claims"]:
        v = verdict(c, rec["level"]) + (f": {c['unverified']}" if c["unverified"] else "")
        ev = " \\\\ ".join(cell(jsum(e)) for e in c["evidence"]) or "—"
        unit = f"{cell(c.get('unit') or '—')}|" if units else ""
        out.append(f"|{c['id']}|{unit}{cell(c['text'])}|{c['provider'] or 'generic'}|{ev}|{cell(v)}|")
    for c in rec["claims"]:
        if not c["evidence"] and not c["files"]:
            continue
        out += ["", f"*{c['id']}*: {c['text']}"]
        for e in c["evidence"]:
            out += ["", f"* {jsum(e, full=True)} · {evidence_meta(e, code)}"]
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


def sink_format(name):
    entry = destinations()[0].get(name, {})
    if name == "local" or entry.get("format") or entry.get("via"):
        return entry.get("format", "markdown")
    return getattr(load_sink(name), "FORMAT", "markdown")


def render(rec, sink=None):
    fmt = sink_format(sink) if sink else "markdown"
    if fmt not in RENDERERS:
        sys.exit(f"{sink}: unknown format {fmt}; formats: {', '.join(RENDERERS)}")
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
    dests = destinations()[0]
    failed = []
    for name in [s for s in rec["sinks"] if s != "local"]:
        entry = dests.get(name)
        if entry is None:
            print(f"{name}: skipped, no longer configured")
            failed.append(name)
            continue
        prior = rec["published"].get(name, {}).get("comment_id")
        if entry.get("via"):  # the agent posts through its own connector, then records the result
            target = f"update comment {prior} on" if prior else "post a new comment on"
            print(f"{name}: agent-backed via {entry['via']}: {target} {rec['issue']} with "
                  f"`receipt.py render --sink {name}` verbatim, then "
                  f"`receipt.py record --sink {name} --url URL --comment-id ID`")
            continue
        missing = kip.missing(entry)
        if missing:
            print(f"{name}: skipped, missing {', '.join(missing)}")
            failed.append(name)
            continue
        mod, conf = load_sink(name), kip.settings(entry)
        try:
            for f in (f for c in rec["claims"] for f in c["files"] if name not in f["uploads"]):
                path = d / f["path"]
                f["uploads"][name] = mod.upload(conf, rec["issue"], path,
                                                mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                save(d, rec)
            comment_id, url = mod.publish(conf, rec["issue"], render(rec, name), prior)
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


def cmd_record(a):
    d = run_dir(a)
    rec = load(d)
    if a.sink not in rec["sinks"]:
        sys.exit(f"{a.sink} isn't one of this run's sinks: {', '.join(rec['sinks'])}")
    prior = rec["published"].get(a.sink, {}).get("comment_id")
    rec["published"][a.sink] = {"comment_id": a.comment_id or prior or a.url, "url": a.url}
    save(d, rec)
    print(f"{a.sink}: recorded {a.url}")


def cmd_path(a):
    print(run_dir(a))


def cmd_check(a):
    """The gate host hooks call. Exit 2 (blocking, reasons on stderr) only when this branch's
    run has claims nobody addressed and the code changed since init; anything else passes."""
    hook = json.loads(sys.stdin.read() or "{}") if a.hook else {}
    if hook.get("cwd"):
        os.chdir(hook["cwd"])
    if hook.get("stop_hook_active"):  # already nudged once this turn; never loop
        return
    if a.hook == "pr" and not re.search(r"\bgh\s+pr\s+create\b", hook.get("tool_input", {}).get("command", "")):
        return
    latest = ROOT / repo_name() / "LATEST"
    if not a.run and not latest.exists():
        return print("check: no receipt run for this repo")
    d = run_dir(a)
    rec = load(d)
    if rec["branch"] != git("branch", "--show-current"):
        return print(f"check: latest run is for {rec['branch']}, not this branch")
    if rec.get("baseline") in (None, fingerprint()):
        return print("check: no code changed since the run started")
    pr = a.hook == "pr"
    blocking = [f"{c['id']} {verdict(c, rec['level'])}: {c['text']}" for c in rec["claims"]
                if (not c["evidence"] and not c["unverified"]) or (pr and verdict(c, rec["level"]).startswith("❌"))]
    if not blocking:
        return print(f"check: every claim in {rec['id']} is addressed")
    todo = ("fix them, or re-run their checks, before opening a PR" if pr else
            "record evidence (`receipt.py run`) or why it can't be verified (`receipt.py unverified`), "
            "then end with `receipt.py render`")
    print(f"kip-receipts: run {d} has claims that aren't done:\n  " + "\n  ".join(blocking) + f"\nNext: {todo}.",
          file=sys.stderr)
    sys.exit(2)


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
    s.add_argument("--target", help="URL under test when it isn't this checkout (staging, prod, a preview deploy)")
    s.add_argument("--level", choices=["lite", "full", "ultra"], default="full")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("claim", parents=[common])
    s.add_argument("text")
    s.add_argument("--provider")
    s.add_argument("--level", choices=["lite", "full", "ultra"], help="override the run's level (e.g. ultra floor)")
    s.add_argument("--unit", help="the kip-decompose unit this claim belongs to")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("run", parents=[common])
    s.add_argument("claim")
    s.add_argument("--red", action="store_true", help="falsifiability run: expected to fail")
    s.add_argument("--static", action="store_true", help="typecheck/lint/grep, not behavior")
    s.add_argument("--collect", help="after the run, attach every image, video, and trace under this dir")
    s.add_argument("--label", help="short name for the ledger table; the full command stays in the details")
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
    s.add_argument("--type", choices=["image", "video", "trace", "log"])
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

    s = sub.add_parser("record", parents=[common])
    s.add_argument("--sink", required=True)
    s.add_argument("--url", required=True)
    s.add_argument("--comment-id", help="the comment's id, so the next publish updates it (default: the url)")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("path", parents=[common])
    s.set_defaults(fn=cmd_path)

    s = sub.add_parser("check", parents=[common])
    s.add_argument("--hook", choices=["stop", "pr"], help="read a host hook's JSON event from stdin")
    s.set_defaults(fn=cmd_check)

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
