#!/usr/bin/env python3
"""Deterministic receipts: record claims and evidence, publish them to sinks.

  receipt.py sinks                                          list destinations and whether each is ready
  receipt.py init [--sink NAME --issue KEY] [--target URL] [--level lite|full|ultra]
                                                            start a run (local only by default), print its dir
  receipt.py claim "TEXT" [--provider NAME] [--level L] [--unit N] [--stage NAME]
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
  receipt.py check [--hook start|stop|pr]                   exit 2 if this branch's run has open claims
                                                            and the code changed since init, or there's
                                                            no run for work that needs one (host hooks)
  receipt.py check --pr N                                   exit 1 if PR N's published ledger is missing
                                                            or has claims that aren't done (CI)

Every command takes --run DIR; default is the latest run on the current branch.
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
import tempfile
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


def branch_run():
    """This branch's latest run: LATEST when it's this branch's, else the newest run made on it."""
    here, branch = ROOT / repo_name(), git("branch", "--show-current")
    runs = sorted((p.parent for p in here.glob("*/receipt.json")), reverse=True)
    if (here / "LATEST").exists():
        runs.insert(0, here / (here / "LATEST").read_text().strip())
    return next((d for d in runs if (d / "receipt.json").exists() and load(d)["branch"] == branch), None)


def run_dir(a):
    d = Path(a.run) if a.run else branch_run()
    if not d:
        sys.exit("no receipt run for this branch; start one with `receipt.py init`")
    return d


def load(d):
    return json.loads((d / "receipt.json").read_text())


def save(d, rec):
    (d / "receipt.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")


def find_claim(rec, cid):
    for c in rec["claims"]:
        if c["id"] == cid:
            return c
    sys.exit(f"unknown claim {cid}; claims: {', '.join(c['id'] for c in rec['claims']) or 'none'}")


def worktree_diff():
    """HEAD to the working tree, new files included, staged through a copy of the index
    (so unchanged files aren't rehashed) and never touching the real one."""
    with tempfile.TemporaryDirectory() as t:
        index, env = Path(t, "index"), {**os.environ, "GIT_INDEX_FILE": str(Path(t, "index"))}
        real = git("rev-parse", "--git-path", "index")
        if real and Path(real).exists():
            shutil.copy2(real, index)
        subprocess.run(["git", "add", "-A"], env=env, capture_output=True)
        r = subprocess.run(["git", "diff", "--cached", "--binary", "HEAD"], env=env, capture_output=True)
    return r.stdout if r.returncode == 0 else b""  # bytes: new files needn't be UTF-8


def fingerprint():
    """Hash of HEAD and the working tree: changes once any work lands after init."""
    return hashlib.sha256(f"{git('rev-parse', 'HEAD')}\n".encode() + worktree_diff()).hexdigest()


def code_state(d, rec, eid):
    """Pin the code an evidence ran against: HEAD, plus the uncommitted diff if any.
    A remote target isn't this checkout, so there's nothing local to pin."""
    if rec.get("target"):
        return {"commit": None, "patch": None, "remote": True}
    state = {"commit": git("rev-parse", "HEAD"), "patch": None}
    diff = worktree_diff()
    if diff:
        p = d / "files" / f"{eid}.patch"
        p.write_bytes(diff)
        state["patch"] = {"path": f"files/{p.name}", "sha256": sha256(p)}
    return state


def new_evidence(d, rec, c, **fields):
    eid = f"{c['id']}e{len(c['evidence']) + 1}"
    e = {"id": eid, **fields, **code_state(d, rec, eid)}
    c["evidence"].append(e)
    c["unverified"] = None  # evidence supersedes an earlier "can't verify"
    return e


def rel_to(path, d):
    try:
        return path.resolve().relative_to(d.resolve())
    except ValueError:
        return None


def command_inputs(cmd, d):
    """Hash every file a command names (specs, configs, scripts), so a rerun can tell
    whether it's running the same inputs. Files inside the run dir are tamper-checked; files in the
    repo are code under test, already pinned with the commit and patch."""
    top = git("rev-parse", "--show-toplevel")
    inputs = []
    for arg in cmd:
        p = Path(arg)
        if p.is_file():
            inside = rel_to(p, d)
            inputs.append({"path": str(inside) if inside else str(p.resolve()), "inside_run": bool(inside),
                           "in_repo": not inside and bool(top and rel_to(p, Path(top))), "sha256": sha256(p)})
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
             "stages": kip.load().get("workflow", {}).get("stages", []), "claims": []})
    (d.parent / "LATEST").write_text(rid + "\n")
    print(d)


def cmd_claim(a):
    d = run_dir(a)
    rec = load(d)
    cid = f"c{len(rec['claims']) + 1}"
    rec["claims"].append({"id": cid, "text": a.text, "unit": a.unit, "provider": a.provider, "level": a.level,
                          "stage": a.stage, "evidence": [], "files": [], "unverified": None})
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


def check_key(e):
    """Runs of the same command in the same dir are one check; observations are one check."""
    return (tuple(e["command"]), e.get("cwd")) if e.get("command") else ("observed",)


def code_key(e):
    return e.get("commit"), (e.get("patch") or {}).get("sha256")


def inputs_key(e):
    """The check's own files (specs, scripts outside the repo), not the code it tests."""
    return sorted((i["path"], i["sha256"]) for i in e.get("inputs", []) if not i.get("in_repo"))


def rerun_note(fails, passes, last, reds):
    """Why a check that failed and later passed still isn't verified, or None when the pass is a fix:
    the same check, unchanged, passing on code that changed since it failed."""
    if last.get("remote"):
        return "passed after failing, code not pinned"
    if any(code_key(f) == code_key(p) and inputs_key(f) == inputs_key(p) for f in fails for p in passes):
        return "flaky: failed and passed on the same code"
    if any(inputs_key(f) != inputs_key(last) for f in fails) and \
            not any(inputs_key(r) == inputs_key(last) and r["passed"] for r in reds):
        return "check changed after failing; show the new check fails without the fix (--red)"
    return None


def verdict(c, level):
    """The latest run of each check decides it. A failure clears only when the same check passes on
    changed code, which also shows the check can fail."""
    level = c.get("level") or level
    ev = c["evidence"]
    runs = {}
    for e in ev:
        runs.setdefault(check_key(e), {"green": [], "red": []})[e["phase"]].append(e)
    if any(r["green"] and not r["green"][-1]["passed"] for r in runs.values()):
        return "❌ refuted"
    if any(r["red"] and not r["red"][-1]["passed"] for r in runs.values()):
        return "❌ check can't fail"
    shown = any(e["phase"] == "red" for e in ev)
    for r in runs.values():
        fails = [e for e in r["green"] if not e["passed"]]
        if fails:
            note = rerun_note(fails, [e for e in r["green"] if e["passed"]], r["green"][-1], r["red"])
            if note:
                return f"⚠️ {note}"
            shown = True
    if c["unverified"] or not ev:
        return "⚠️ unverified"
    if level == "ultra" and not shown:
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


def render_github(rec, sink=None):
    """Markdown plus a hidden line CI reads (`check --pr`): the run and what blocks a PR."""
    gate = json.dumps({"run": rec["id"], "head": rec.get("head"), "blocking": blocking(rec, pr=True)},
                      ensure_ascii=False)
    return render_markdown(rec, sink) + "\n\n<!-- kip-gate " + gate.replace(">", "\\u003e") + " -->"


RENDERERS = {"markdown": render_markdown, "jira": render_jira, "github": render_github}


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
    rec["head"] = git("rev-parse", "HEAD")  # what CI compares to the PR head (`check --pr`)
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


def blocking(rec, pr=False):
    """What keeps work from being done: claims nobody addressed and, before a PR, refuted
    claims and workflow stages (tidy, review) with no claim at all."""
    out = [f"{c['id']} {verdict(c, rec['level'])}: {c['text']}" for c in rec["claims"]
           if (not c["evidence"] and not c["unverified"]) or (pr and verdict(c, rec["level"]).startswith("❌"))]
    if pr:
        have = {c.get("stage") for c in rec["claims"]}
        out += [f"{s}: no claim (`receipt.py claim \"…\" --stage {s}`)" for s in rec.get("stages", []) if s not in have]
    return out


def gate(a, hook):
    """(blocked, message) for this branch's run; anything that isn't this branch's changed work passes."""
    if hook.get("stop_hook_active") or hook.get("loop_count"):  # already nudged once this turn; never loop
        return False, "check: already nudged this turn"
    if hook.get("status") in ("aborted", "error"):  # Cursor: the user stopped it, or it crashed; don't re-prompt
        return False, f"check: turn {hook['status']}"
    command = hook.get("tool_input", {}).get("command") or hook.get("command", "")
    if a.hook == "pr" and not re.search(r"(^|[;&|(\n])\s*gh\s+pr\s+create\b", command):  # as a command, not in text
        return False, "check: not opening a PR"
    d = Path(a.run) if a.run else branch_run()
    if not d:
        return unplanned(a.hook, hook)
    rec = load(d)
    if rec.get("baseline") in (None, fingerprint()):
        return False, "check: no code changed since the run started"
    pr = a.hook == "pr"
    open_ = blocking(rec, pr)
    if not open_:
        return False, f"check: every claim in {rec['id']} is addressed"
    todo = ("fix them, or re-run their checks, before opening a PR" if pr else
            "record evidence (`receipt.py run`) or why it can't be verified (`receipt.py unverified`), "
            "then end with `receipt.py render`")
    return True, f"kip-receipts: run {d} has claims that aren't done:\n  " + "\n  ".join(open_) + f"\nNext: {todo}."


def base_branch():
    ref = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return ref or next((b for b in ("main", "master") if git("rev-parse", "--verify", "-q", b)), None)


def session_file(hook):
    sid = hook.get("session_id") or hook.get("conversation_id")
    return ROOT / repo_name() / "sessions" / re.sub(r"[^\w.-]", "_", sid) if sid else None


def unplanned(event, hook):
    """No run on this branch. Block only work that needed one: a PR from a branch that changed
    something, or a session that changed code since it started."""
    base = base_branch()
    ahead = base and git("rev-list", "--count", f"{base}..HEAD") not in (None, "0")
    if event == "pr" and (ahead or git("diff", "HEAD")):
        return True, ("kip-receipts: this branch has changes but no receipts run. Start one (`receipt.py init`), "
                      "record a claim per promise, and back each with evidence before opening a PR.")
    start = session_file(hook)
    if event == "stop" and start and start.exists() and start.read_text().strip() != fingerprint():
        return True, ("kip-receipts: this session changed code, but there's no receipts run for this branch. "
                      "Start one (`receipt.py init`), record what you claim to have done, and back each claim "
                      "with evidence, or say plainly that it's unverified.")
    return False, "check: no receipt run for this branch"


def session_start(hook):
    """Remember the code as the session found it (once per session), and say where this branch's run stands."""
    if not git("rev-parse", "--show-toplevel"):
        return ""
    start = session_file(hook)
    if start and not start.exists():
        start.parent.mkdir(parents=True, exist_ok=True)
        start.write_text(fingerprint() + "\n")
    d = branch_run()
    if not d:
        return ""
    rec = load(d)
    open_ = blocking(rec, pr=True)
    return (f"kip-receipts: {git('branch', '--show-current')} has run {rec['id']} ({rec['level']}, {d}). "
            + ("Not done yet:\n  " + "\n  ".join(open_) if open_ else "Every claim is addressed.")
            + "\nAdd evidence and claims to this run instead of starting a new one.")


def check_pr(pr):
    r = subprocess.run(["gh", "pr", "view", pr, "--json", "body,headRefOid"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"gh pr view {pr}: {r.stderr.strip()}")
    view = json.loads(r.stdout)
    found = re.findall(r"<!-- kip-gate (\{.*?\}) -->", view.get("body") or "")
    if not found:
        sys.exit(f"kip-receipts: PR {pr} has no receipts ledger; "
                 f"publish one with `receipt.py publish --sink github --issue {pr}`")
    g = json.loads(found[-1])
    if g.get("head") != view.get("headRefOid"):
        sys.exit(f"kip-receipts: PR {pr}'s ledger is for {(g.get('head') or 'an unknown commit')[:7]}, but its head "
                 f"is {(view.get('headRefOid') or '?')[:7]}; verify the new commits and republish "
                 f"(`receipt.py publish`)")
    if g["blocking"]:
        sys.exit(f"kip-receipts: run {g['run']} has claims that aren't done:\n  " + "\n  ".join(g["blocking"]))
    print(f"check: every claim in {g['run']} is addressed")


def cmd_check(a):
    """The gate host hooks and CI call. Claude Code: exit 2 blocks, reasons on stderr. Cursor (its
    events carry workspace_roots): JSON on stdout, a follow-up message at stop, a deny before a PR."""
    if a.pr:
        return check_pr(a.pr)
    hook = json.loads(sys.stdin.read() or "{}") if a.hook else {}
    cwd = hook.get("cwd") or (hook.get("workspace_roots") or [None])[0]
    if cwd:
        os.chdir(cwd)
    if a.hook == "start":
        msg = session_start(hook)
        return print(json.dumps({"additional_context": msg} if msg else {}) if "workspace_roots" in hook else msg)
    blocked, msg = gate(a, hook)
    if "workspace_roots" in hook:
        if a.hook == "pr":
            out = {"permission": "deny", "user_message": msg, "agent_message": msg} if blocked else {"permission": "allow"}
        else:
            out = {"followup_message": msg} if blocked else {}
        return print(json.dumps(out))
    print(msg, file=sys.stderr if blocked else sys.stdout)
    if blocked:
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
    s.add_argument("--stage", help="the workflow stage this claim covers (tidy, review); a PR needs one per stage")
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
    g = s.add_mutually_exclusive_group()
    g.add_argument("--hook", choices=["start", "stop", "pr"], help="read a host hook's JSON event from stdin")
    g.add_argument("--pr", help="check the ledger published on this pull request (CI; needs gh)")
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
