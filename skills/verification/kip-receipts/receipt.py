#!/usr/bin/env python3
"""Deterministic receipts: record claims and evidence, publish them to sinks.

  receipt.py sinks                                          list destinations and whether each is ready
  receipt.py init [--sink NAME --issue KEY] [--target URL] [--level lite|full|ultra]
                                                            start a run (local only by default), print its dir
  receipt.py claim "TEXT" [--provider NAME] [--level L] [--unit N] [--stage NAME]
                                                            add a claim, print its id
  receipt.py run CLAIM [--red] [--static] [--collect DIR] [--label TEXT] [-- CMD ...]
                                                            run CMD (a required check's own command if omitted),
                                                            record the result, attach media from DIR
  receipt.py observe CLAIM (--passed|--failed) "TEXT"       record a non-command observation
  receipt.py attach CLAIM PATH [--type image|video|log]     copy a file into the run
  receipt.py unverified CLAIM "REASON"                      mark a claim as unverifiable
  receipt.py accept CLAIM [--reason TEXT]                   the user takes an unverified claim as it stands
  receipt.py orphan [--reason TEXT]                         set aside a run whose work was abandoned: it leaves
                                                            the dashboard and its branch needs a new run
  receipt.py sweep                                          finish this repo's runs whose branch merged, orphan
                                                            those whose branch is gone
  receipt.py render [--sink NAME]                           print the ledger as that sink would post it
  receipt.py publish [--sink NAME --issue KEY]              upload files and upsert the comment on each sink,
                                                            optionally opting the run into another sink first
  receipt.py record --sink NAME --url URL [--comment-id ID] record a comment the agent posted (agent-backed sinks)
  receipt.py record --sink NAME --file PATH --url URL       record a file the agent uploaded itself (e.g. github, by browser)
  receipt.py path                                           print the run dir (its logs, patches, specs)
  receipt.py json                                           print the run, its claims and evidence, as JSON
  receipt.py db [--migrate]                                 kip.db's path, schema version, and run count;
                                                            --migrate creates or upgrades it and imports JSON runs
  receipt.py check [--hook start|stop|pr|tool|prompt|response|end] exit 2 if this branch's run has open claims
                                                            and the code changed since init, or there's
                                                            no run for work that needs one (host hooks)
  receipt.py serve [--port N] [--idle SECONDS]              live dashboard on 127.0.0.1 (install's service or a start hook launches it)
  receipt.py check --pr N                                   exit 1 if PR N's published ledger is missing
                                                            or has claims that aren't done (CI)

Every command takes --run ID (or the run's folder path); default is the latest run on the current branch.
Runs, claims, and evidence live in $KIP_RECEIPTS_DIR (default ~/.kip/receipts)/kip.db, a SQLite database;
each run's logs, patches, specs, and media live in <repo>/<run-id>/ beside it.
Sinks are the `destinations` in Kip's config (kip.py config); script-backed ones are modules
in <repo>/.kip/sinks/ or this script's sinks/ (see sinks/CONTRACT.md).
Standard library only, Python 3.8+.
"""
import argparse
import contextlib
import copy
import datetime
import functools
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import shlex
import select
import shutil
import signal
import socket
import sqlite3
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


@functools.lru_cache(maxsize=None)
def repo_name():
    top = git("rev-parse", "--show-toplevel")
    return Path(top).name if top else Path.cwd().name


def github_url():
    """This checkout's GitHub page, from its origin remote; None when it isn't on GitHub."""
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$", git("remote", "get-url", "origin") or "")
    return m and f"https://github.com/{m[1]}"


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


DB = ROOT / "kip.db"
SCHEMA = [  # one entry per version: append a new one, never edit a shipped one
    """CREATE TABLE runs (id TEXT PRIMARY KEY, repo TEXT NOT NULL, branch TEXT, created_at TEXT NOT NULL, data TEXT NOT NULL);
    CREATE INDEX runs_by_branch ON runs (repo, branch);
    CREATE TABLE claims (run TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (run, id));
    CREATE TABLE evidence (run TEXT NOT NULL, claim TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                           PRIMARY KEY (run, claim, seq));
    CREATE TABLE files (run TEXT NOT NULL, claim TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                        PRIMARY KEY (run, claim, seq));
    CREATE TABLE sessions (repo TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY (repo, id));""",
]
_db, _imported = None, (0, 0)


def db():
    """kip.db, upgraded to the current schema on first open; creating it imports the JSON store that came
    before it in the same transaction, so that happens exactly once. Write-ahead logging lets hooks in many
    sessions read while one writes, and an up-to-date database is opened without taking the write lock."""
    global _db, _imported
    if _db is None:
        DB.parent.mkdir(parents=True, exist_ok=True)
        if not DB.exists():  # a new kip.db appears already in WAL mode: when openers switch one at once,
            new = DB.with_name(f"{DB.name}.{os.getpid()}.new")  # SQLite fails one rather than wait (deadlock)
            sqlite3.connect(new).execute("PRAGMA journal_mode=WAL").connection.close()
            try:
                os.link(new, DB)
            except FileExistsError:
                pass
            finally:
                new.unlink()
        _db = sqlite3.connect(DB, timeout=30, isolation_level=None, check_same_thread=False)
        try:
            _db.execute("PRAGMA journal_mode=WAL")
            if _db.execute("PRAGMA user_version").fetchone()[0] < len(SCHEMA):
                with tx():  # re-read under the lock: another process may have just upgraded it
                    version = _db.execute("PRAGMA user_version").fetchone()[0]
                    for v, script in enumerate(SCHEMA[version:], version + 1):
                        for statement in script.split(";"):
                            _db.execute(statement)
                        _db.execute(f"PRAGMA user_version = {v}")
                    if version == 0:
                        _imported = import_json()
        except BaseException:
            _db = None
            raise
    return _db


@contextlib.contextmanager
def tx(mode="IMMEDIATE"):
    """A transaction (IMMEDIATE takes the write lock up front, so writers queue instead of failing midway),
    or the one already open."""
    con = db()
    if con.in_transaction:
        yield con
        return
    con.execute(f"BEGIN {mode}")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def dumps(x):
    return json.dumps(x, ensure_ascii=False)


def branch_run():
    """This branch's newest run: the last one started on it (rowid order; saving a run never moves it)."""
    row = db().execute("SELECT repo, id FROM runs WHERE repo = ? AND branch IS ? ORDER BY rowid DESC",
                       (repo_name(), git("branch", "--show-current"))).fetchone()
    return ROOT / row[0] / row[1] if row else None


def run_path(run):
    """A run's folder (its logs, patches, specs), from its id or that folder's path."""
    rid = Path(run).name
    row = db().execute("SELECT repo FROM runs WHERE id = ?", (rid,)).fetchone()
    if not row:
        sys.exit(f"no receipt run {rid}")
    return ROOT / row[0] / rid


def run_dir(a):
    d = run_path(a.run) if a.run else branch_run()
    if not d:
        sys.exit("no receipt run for this branch; start one with `receipt.py init`")
    return d


def adding_to(a):
    """run_dir for commands that add to a run: an orphaned one takes nothing more."""
    d = run_dir(a)
    if json.loads(db().execute("SELECT data FROM runs WHERE id = ?", (Path(d).name,)).fetchone()[0]).get("orphaned"):
        sys.exit(f"run {Path(d).name} is orphaned; start a new one with `receipt.py init`")
    return d


def load(d):
    """A run as one dict, its claims with their evidence and files, read from one snapshot."""
    rid = Path(d).name
    with tx("DEFERRED") as con:
        row = con.execute("SELECT data FROM runs WHERE id = ?", (rid,)).fetchone()
        if not row:
            sys.exit(f"no receipt run {rid}")
        claims = {cid: {**json.loads(data), "evidence": [], "files": []} for cid, data in
                  con.execute("SELECT id, data FROM claims WHERE run = ? ORDER BY seq", (rid,))}
        for table in ("evidence", "files"):
            for cid, data in con.execute(f"SELECT claim, data FROM {table} WHERE run = ? ORDER BY seq", (rid,)):
                claims[cid][table].append(json.loads(data))
    return {**json.loads(row[0]), "claims": list(claims.values())}


def save(d, rec):
    rid = Path(d).name
    with tx() as con:
        con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET data = excluded.data",
                    (rid, rec["repo"], rec.get("branch"), rec.get("created_at") or rid,  # runs from older Kips may lack them
                     dumps({k: v for k, v in rec.items() if k != "claims"})))
        for table in ("claims", "evidence", "files"):
            con.execute(f"DELETE FROM {table} WHERE run = ?", (rid,))
        for i, c in enumerate(rec["claims"]):
            con.execute("INSERT INTO claims VALUES (?, ?, ?, ?)",
                        (rid, c["id"], i, dumps({k: v for k, v in c.items() if k not in ("evidence", "files")})))
            for table in ("evidence", "files"):
                con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
                                [(rid, c["id"], j, dumps(x)) for j, x in enumerate(c[table])])


@contextlib.contextmanager
def mutate(d):
    """Load, change and save a run in one write transaction, so concurrent writers queue instead of
    overwriting each other's claims and evidence."""
    with tx():
        rec = load(d)
        yield rec
        save(d, rec)


def import_json():
    """Bring in runs and session starts from the JSON store kip.db replaced; their files stay where they are.
    A run already in kip.db is left alone, so this is safe to repeat. Returns (runs, sessions) imported."""
    runs = sessions = 0
    for p in sorted(ROOT.glob("*/*/receipt.json")):
        with tx() as con:
            if con.execute("SELECT 1 FROM runs WHERE id = ?", (p.parent.name,)).fetchone():
                continue
            try:
                save(p.parent, json.loads(p.read_text()))
            except (ValueError, KeyError) as err:
                print(f"kip-receipts: skipped {p}: {err}", file=sys.stderr)
                continue
            runs += 1
    for p in ROOT.glob("*/sessions/*"):
        with tx() as con:
            sessions += con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)",
                                    (p.parent.parent.name, p.name, p.read_text().strip())).rowcount
    return runs, sessions


def find_claim(rec, cid):
    for c in rec["claims"]:
        if c["id"] == cid:
            return c
    sys.exit(f"unknown claim {cid}; claims: {', '.join(c['id'] for c in rec['claims']) or 'none'}")


def staged(*commands):
    """Run git commands on the working tree, new files included, staged through a copy of the index
    (so unchanged files aren't rehashed) and never touching the real one."""
    with tempfile.TemporaryDirectory() as t:
        index, env = Path(t, "index"), {**os.environ, "GIT_INDEX_FILE": str(Path(t, "index"))}
        real = git("rev-parse", "--git-path", "index")
        if real and Path(real).exists():
            shutil.copy2(real, index)
        subprocess.run(["git", "add", "-A"], env=env, capture_output=True)
        runs = [subprocess.run(["git", *args], env=env, capture_output=True) for args in commands]
    return [r.stdout if r.returncode == 0 else b"" for r in runs]  # bytes: new files needn't be UTF-8


DIFF, TREE = ["diff", "--cached", "--binary", "HEAD"], ["write-tree"]  # TREE: same code, same id, committed or not


def worktree_diff():
    """HEAD to the working tree."""
    return staged(DIFF)[0]


@functools.lru_cache(maxsize=None)
def current_tree():
    """The code verdicts judge evidence against, once per invocation."""
    return staged(TREE)[0].decode().strip() or None


def fingerprint():
    """Hash of HEAD and the working tree: changes once any work lands after init."""
    return hashlib.sha256(f"{git('rev-parse', 'HEAD')}\n".encode() + worktree_diff()).hexdigest()


def code_state(d, rec, eid):
    """Pin the code an evidence ran against: HEAD, plus the uncommitted diff if any.
    A remote target isn't this checkout, so there's nothing local to pin."""
    if rec.get("target"):
        return {"commit": None, "patch": None, "remote": True}
    tree, diff = staged(TREE, DIFF)
    state = {"commit": git("rev-parse", "HEAD"), "patch": None, "tree": tree.decode().strip() or None}
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
    workflow = kip.load().get("workflow", {})
    checks = [] if a.target else [  # required checks are this checkout's, not a remote site's
        new_claim(f"c{i}", f"`{name}` passes", check=name, command=cmd if isinstance(cmd, list) else ["sh", "-c", cmd])
        for i, (name, cmd) in enumerate(workflow.get("checks", {}).items(), 1)]
    save(d, {"id": rid, "created_at": now.isoformat(timespec="seconds"), "repo": repo_name(),
             "commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"), "baseline": fingerprint(),
             "level": a.level, "issue": a.issue, "target": a.target, "sinks": sinks, "published": {},
             "stages": workflow.get("stages", []), "claims": checks})
    print(d)


def cmd_claim(a):
    with mutate(adding_to(a)) as rec:
        if rec.get("finished"):
            sys.exit(f"run {rec['id']} is finished (its work is on the base branch); start a new one with `receipt.py init`")
        cid = f"c{len(rec['claims']) + 1}"
        rec["claims"].append(new_claim(cid, a.text, unit=a.unit, provider=a.provider, level=a.level, stage=a.stage))
    print(cid)


def new_claim(cid, text, **fields):
    return {"id": cid, "text": text, "unit": None, "provider": None, "level": None, "stage": None,
            **fields, "evidence": [], "files": [], "unverified": None}


def not_a_check(c, how):
    """A required check (`workflow.checks`) is settled only by running its configured command."""
    if c.get("check"):
        sys.exit(f"{c['id']} is the required `{c['check']}` check; it can't be {how}. "
                 f"Run it: `receipt.py run {c['id']}`")


def cmd_run(a):
    d = adding_to(a)
    c = find_claim(load(d), a.claim)
    cmd = a.cmd or c.get("command")
    if not cmd:
        sys.exit("usage: receipt.py run CLAIM [--red] [--static] -- CMD ...")
    if c.get("command") and cmd != c["command"]:
        not_a_check(c, f"settled by `{shlex.join(cmd)}`")
    cwd = (c.get("command") and git("rev-parse", "--show-toplevel")) or os.getcwd()  # a required check runs at the root
    try:
        r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        code, out = r.returncode, redact(r.stdout)
    except OSError as err:
        code, out = 127, f"{cmd[0]}: {err.strerror}\n"
    passed = code != 0 if a.red else code == 0
    collected = []
    with mutate(d) as rec:  # after the command, so a long check never holds the write lock
        c = find_claim(rec, a.claim)
        e = new_evidence(d, rec, c, kind="static" if a.static else "command", phase="red" if a.red else "green",
                         label=a.label, command=cmd, cwd=cwd, inputs=command_inputs(cmd, d), exit_code=code,
                         passed=passed, excerpt="\n".join(out.strip().splitlines()[-EXCERPT_LINES:]))
        log = d / "files" / f"{e['id']}.log"
        log.write_text(out)
        e["log"] = {"path": f"files/{log.name}", "sha256": sha256(log)}
        if a.collect and Path(a.collect).is_dir():
            root = Path(a.collect)
            for p in sorted(root.rglob("*")):
                kind = FILE_TYPES.get(p.suffix.lower())
                if p.is_file() and kind:
                    name = f"{e['id']}-" + "-".join(p.relative_to(root).parts)
                    collected.append(attach_file(d, c, p, kind, name).name)
    expected = "expected failure" if a.red else "expected success"
    print(f"{e['id']}: exit {code} ({expected}) -> {'pass' if passed else 'FAIL'}"
          + (f", collected {len(collected)} file(s)" if a.collect else ""))


def cmd_observe(a):
    d = adding_to(a)
    with mutate(d) as rec:
        c = find_claim(rec, a.claim)
        not_a_check(c, "observed")
        e = new_evidence(d, rec, c, kind="observed", phase="green", observation=a.text, passed=a.passed)
    print(e["id"])


def cmd_attach(a):
    src = Path(a.path)
    if not src.is_file():
        sys.exit(f"no such file: {src}")
    d = adding_to(a)
    with mutate(d) as rec:
        dest = attach_file(d, find_claim(rec, a.claim), src, a.type)
    print(dest)


def cmd_unverified(a):
    with mutate(adding_to(a)) as rec:
        c = find_claim(rec, a.claim)
        not_a_check(c, "marked unverified")
        c["unverified"] = a.reason


def cmd_accept(a):
    """The user takes an unverified claim as it stands, so the dashboard stops asking them about it."""
    with mutate(adding_to(a)) as rec:
        c = find_claim(rec, a.claim)
        if not c["unverified"]:
            sys.exit(f"{c['id']} isn't marked unverified; only an unverified claim can be accepted")
        c["accepted"] = a.reason or True


def cmd_orphan(a):
    """Set a run aside whose work was abandoned: it leaves the dashboard and stops speaking for its branch."""
    with mutate(run_dir(a)) as rec:
        rec["orphaned"] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                           "reason": a.reason}
    print(f"orphaned {rec['id']}")


def cmd_sweep(a):
    for line in settle():
        print(line)


def settle(orphan=True):
    """Settle this repo's runs that no checkout will finish: finished once their branch merged into the base
    branch, orphaned (if orphan) once it's gone without merging. The current branch finishes as the gate would.
    Yields a line per run settled."""
    base, here = base_branch(), git("branch", "--show-current")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    live = set(re.findall(r"^branch refs/heads/(.+)$", git("worktree", "list", "--porcelain") or "", re.M))
    for (rid,) in db().execute("SELECT id FROM runs WHERE repo = ? ORDER BY rowid", (repo_name(),)).fetchall():
        d = ROOT / repo_name() / rid
        rec = load(d)
        branch = rec.get("branch")
        if rec.get("finished") or rec.get("orphaned") or not branch:
            continue
        if branch == here:
            if finish_if_done(d, rec):
                yield f"finished {rid} ({branch}: merged)"
            continue
        if branch in live:  # checked out elsewhere: that checkout's hooks finish it, uncommitted work and all
            continue
        tip = git("rev-parse", "-q", "--verify", f"refs/heads/{branch}") or \
            git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}")
        # messy-bun: a deleted branch counts as merged only by GitHub's merge message; squash merges get orphaned.
        # "Gone" means gone from this clone, and runs are keyed by folder name, so sweep in the clone that made them.
        merged = bool(base) and git("merge-base", "--is-ancestor", tip, base) is not None and tip != rec["commit"] if tip else \
            bool(base and git("log", base, "--merges", "-1", "--format=%h", f"--grep=from [^ ]*/{re.escape(branch)}$"))
        if merged:
            with mutate(d) as fresh:
                fresh["finished"] = {"at": now, "head": tip, "fingerprint": None}
            yield f"finished {rid} ({branch}: merged)"
        elif orphan and not tip:
            with mutate(d) as fresh:
                fresh["orphaned"] = {"at": now, "reason": "its branch is gone"}
            yield f"orphaned {rid} ({branch}: branch gone)"


def check_key(e):
    """Runs of the same command in the same dir are one check; observations are one check."""
    return (tuple(e["command"]), e.get("cwd")) if e.get("command") else ("observed",)


def code_key(e):
    return e.get("tree") or (e.get("commit"), (e.get("patch") or {}).get("sha256"))


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


def verdict(c, rec):
    """The latest run of each check decides it. A failure clears only when the same check passes on
    changed code, which also shows the check can fail."""
    level = c.get("level") or rec["level"]
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
    now = current_tree() if repo_name() == rec["repo"] else None  # another checkout's code says nothing
    if now and any(r["green"] and r["green"][-1].get("tree") not in (None, now) for r in runs.values()):
        return "⚠️ stale: the code changed since it was checked"
    if c["unverified"] or not ev:
        return "⚠️ unverified"
    if all(e["kind"] == "observed" for e in ev) and not c["files"]:
        return "⚠️ self-reported: an observation with nothing attached"
    if level == "ultra" and not shown:
        return "⚠️ falsifiability not shown"
    return "✅ verified"


def recorded(rec):
    """Every file the run recorded inside its folder, with the hash it had then."""
    entries = [f for c in rec["claims"] for f in c["files"]]
    entries += [e[k] for c in rec["claims"] for e in c["evidence"] for k in ("log", "patch") if e.get(k)]
    return entries + [i for c in rec["claims"] for e in c["evidence"] for i in e.get("inputs", []) if i["inside_run"]]


def verify_hashes(d, rec):
    """Refuse to render evidence whose files changed since they were recorded."""
    for f in recorded(rec):
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


def render_markdown(rec, sink=None, bare_video=False):
    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    def code(s):
        return f"`{s}`"

    units = has_units(rec)
    out = [f"### Receipts · {subject(rec, code)} · {rec['level']}", "",
           "| # |" + (" Unit |" if units else "") + " Claim | Provider | Evidence | Verdict |",
           "|---|" + ("---|" if units else "") + "---|---|---|---|"]
    for c in rec["claims"]:
        v = verdict(c, rec) + (f": {c['unverified']}" if c["unverified"] else "")
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
            elif url and f["type"] == "video" and bare_video:  # alone on a line, GitHub renders a player
                out += ["", url]
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
        v = verdict(c, rec) + (f": {c['unverified']}" if c["unverified"] else "")
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
    return render_markdown(rec, sink, bare_video=True) + "\n\n<!-- kip-gate " + gate.replace(">", "\\u003e") + " -->"


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
    rec["head"] = git("rev-parse", "HEAD")  # what CI compares to the PR head (`check --pr`)
    with mutate(d) as fresh:  # only what publish owns; claims and evidence recorded meanwhile stay
        fresh.update(issue=rec["issue"], sinks=rec["sinks"], head=rec["head"])
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
                with mutate(d) as fresh:
                    next(x for c in fresh["claims"] for x in c["files"] if x["path"] == f["path"])["uploads"][name] = \
                        f["uploads"][name]
            comment_id, url = mod.publish(conf, rec["issue"], render(rec, name), prior)
        except Exception as err:  # one sink failing shouldn't stop the others
            print(f"{name}: failed: {redact(str(err))}")
            failed.append(name)
            continue
        with mutate(d) as fresh:
            fresh["published"][name] = rec["published"][name] = {"comment_id": comment_id, "url": url}
        print(f"{name}: {'updated' if prior else 'posted'} {url}")
        pending = [f["path"] for c in rec["claims"] for f in c["files"] if not f["uploads"].get(name)]
        if pending:
            print(f"{name}: not uploaded: {', '.join(pending)}; upload each yourself, "
                  f"`receipt.py record --sink {name} --file PATH --url URL`, then publish again")
    if not [s for s in rec["sinks"] if s != "local"]:
        print(f"local only: {d}")
    if failed:
        sys.exit(1)


def cmd_record(a):
    with mutate(run_dir(a)) as rec:
        if a.sink not in rec["sinks"]:
            sys.exit(f"{a.sink} isn't one of this run's sinks: {', '.join(rec['sinks'])}")
        if a.file:
            f = next((f for c in rec["claims"] for f in c["files"] if a.file in (f["path"], Path(f["path"]).name)), None)
            if f is None:
                sys.exit(f"no file {a.file} in this run")
            f["uploads"][a.sink] = a.url
        else:
            prior = rec["published"].get(a.sink, {}).get("comment_id")
            rec["published"][a.sink] = {"comment_id": a.comment_id or prior or a.url, "url": a.url}
    print(f"{a.sink}: recorded {a.url}")


def cmd_path(a):
    print(run_dir(a))


def cmd_json(a):
    print(json.dumps(load(run_dir(a)), indent=2, ensure_ascii=False))


def cmd_db(a):
    """Where kip.db is, its schema version and how many runs it holds. --migrate creates or upgrades it and
    imports the JSON store first; without it, this only reads."""
    if a.migrate:
        db()  # creates or upgrades it; a new one imports as it opens
        runs, sessions = map(sum, zip(import_json(), _imported))
        print(f"imported {runs} runs and {sessions} session starts from the JSON store")
    if not DB.exists():
        return print(f"{DB} · not created yet")
    con = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    version = con.execute("PRAGMA user_version").fetchone()[0]
    runs = con.execute("SELECT count(*) FROM runs").fetchone()[0] if version else 0
    print(f"{DB} · schema v{version} of {len(SCHEMA)} · {runs} runs")


def blocking(rec, pr=False):
    """What keeps work from being done: claims nobody addressed or whose evidence is stale, required
    checks that haven't passed and, before a PR, refuted claims, workflow stages (tidy, review)
    with no claim, or no claims at all."""
    def open_(c, v):
        return (not c["evidence"] and not c["unverified"]) or v.startswith("⚠️ stale") or \
            (c.get("check") and not v.startswith("✅")) or (pr and v.startswith("❌"))
    out = [f"{c['id']} {v}: {c['text']}" for c in rec["claims"] for v in [verdict(c, rec)] if open_(c, v)]
    if pr:
        have = {c.get("stage") for c in rec["claims"]}
        out += [f"{s}: no claim (`receipt.py claim \"…\" --stage {s}`)" for s in rec.get("stages", []) if s not in have]
        if not rec["claims"]:
            out.append("no claims: record what the branch does (`receipt.py claim \"…\"`) and back each with evidence")
    return out


def opens_pr(hook):
    """A PR-opening MCP tool, or a shell command (not text in one) that opens a PR through gh."""
    if re.search(r"create_pull_request\b|create_pr\b", str(hook.get("tool_name") or "")):
        return True
    tool_input = hook.get("tool_input")
    command = str((tool_input.get("command") if isinstance(tool_input, dict) else None) or hook.get("command") or "")
    for part in re.split(r"[;&|(\n]", command):
        part = part.strip()
        if re.match(r"gh\s+pr\s+create\b", part):
            return True
        if re.match(r"gh\s+api\b", part) and ("createPullRequest" in part or re.search(r"/pulls(?![\w/])", part) and
                re.search(r"\s(-X\s*POST|--method[=\s]+POST|-[fF]|--field|--raw-field|--input)\b", part)):
            return True
    return False


NUDGED = "check: already nudged this turn"


def gate(a, hook):
    """(blocked, message) for this branch's run; anything that isn't this branch's changed work passes."""
    if hook.get("stop_hook_active") or hook.get("loop_count"):  # already nudged once this turn; never loop
        return False, NUDGED
    if hook.get("status") in ("aborted", "error"):  # Cursor: the user stopped it, or it crashed; don't re-prompt
        return False, f"check: turn {hook['status']}"
    if a.hook == "pr" and not opens_pr(hook):
        return False, "check: not opening a PR"
    d = run_path(a.run) if a.run else branch_run()
    rec = load(d) if d else None
    if rec and rec.get("orphaned"):  # its claims no longer speak for this branch
        rec = None
    if rec and (rec.get("finished") or finish_if_done(d, rec)):
        if rec["finished"]["fingerprint"] == fingerprint():
            return False, f"check: run {rec['id']} is finished: its work is on the base branch, nothing changed since"
        rec = None  # new work after a finished run is unplanned work
    if not rec:
        return unplanned(a.hook, hook)
    pr = a.hook == "pr"
    # A run started after the work was done still judges it: a PR by the branch's changes,
    # a stop by what this session changed.
    if rec.get("baseline") in (None, fingerprint()) and not (branch_changed() if pr else session_changed(hook)):
        return False, "check: no code changed since the run started"
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


def session_id(hook):
    sid = hook.get("session_id") or hook.get("conversation_id")
    return str(sid) if sid else None


def session_start_state(hook):
    """The code as this session found it (session_state), or None for a session Kip didn't see start."""
    sid = session_id(hook)
    row = sid and db().execute("SELECT state FROM sessions WHERE repo = ? AND id = ?", (repo_name(), sid)).fetchone()
    return row[0] if row else None


def finish_if_done(d, rec):
    """Mark a run finished once commits made since its init are on the base branch and nothing
    changed since, so its old claims stop speaking for new work. True if it just finished."""
    head, base = git("rev-parse", "HEAD"), base_branch()
    if head == rec["commit"] or not base or git("merge-base", "--is-ancestor", "HEAD", base) is None \
            or worktree_diff():
        return False
    rec["finished"] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                       "head": head, "fingerprint": fingerprint()}
    with mutate(d) as fresh:
        fresh["finished"] = rec["finished"]
    return True


def branch_changed():
    """The branch differs from the base branch: commits ahead of it, or uncommitted changes to tracked files."""
    base = base_branch()
    return bool(base and git("rev-list", "--count", f"{base}..HEAD") not in (None, "0") or git("diff", "HEAD"))


def session_state():
    """HEAD, the uncommitted diff, and now: switching or pulling moves HEAD, but only the session's
    own diff and commits are its changes."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return f"{git('rev-parse', 'HEAD')} {hashlib.sha256(worktree_diff()).hexdigest()} {now}"


def session_changed(hook):
    """This session changed code: its uncommitted diff differs from how it found it, or HEAD has
    commits it made (not merges) that its starting HEAD lacks and no finished run covers."""
    start = session_start_state(hook)
    if not start:
        return False
    parts = start.split()
    if len(parts) == 1:  # a session started before HEAD and diff were kept apart
        return parts[0] != fingerprint()
    head, diff, since = parts
    if diff != session_state().split()[1]:
        return True
    me = re.search(r"<(.*)>", git("var", "GIT_COMMITTER_IDENT") or "")
    mine = ["rev-list", "--no-merges", "--ignore-missing", f"--since={since}", "-F", f"--committer=<{me[1]}>",
            "HEAD", f"^{head}"] if me else None
    if not (mine and git(*mine)):
        return False
    list(settle(orphan=False))  # they may be a merged branch's, whose run finishes now
    covered = db().execute("SELECT json_extract(data, '$.finished.head') FROM runs WHERE repo = ?", (repo_name(),))
    return bool(git(*mine, *(f"^{h}" for (h,) in covered if h)))


def unplanned(event, hook):
    """No run on this branch. Block only work that needed one: a PR from a branch that changed
    something, or a session that changed code since it started."""
    if event == "pr" and branch_changed():
        return True, ("kip-receipts: this branch has changes but no receipts run. Start one (`receipt.py init`), "
                      "record a claim per promise, and back each with evidence before opening a PR.")
    if event == "stop" and session_changed(hook):
        return True, ("kip-receipts: this session changed code, but there's no receipts run for this branch. "
                      "Start one (`receipt.py init`), record what you claim to have done, and back each claim "
                      "with evidence, or say plainly that it's unverified.")
    return False, "check: no receipt run for this branch"


def setup_reminder():
    """One-time setup a person still has to do, for the agent to offer."""
    return "\n".join(f"kip setup: `{n}` isn't done ({e['about']}). Offer it to the user once; they run `{e['run']}` "
                   f"themselves. If they don't want it, `python3 ~/.kip/kip/kip.py setup --skip {n}` "
                   "stops this reminder." for n, e in kip.pending_setup())


def session_start(hook):
    """Remember the code as the session found it (once per session), say where this branch's run stands,
    and what setup is left."""
    return "\n".join(filter(None, [run_status(hook), setup_reminder()]))


def run_status(hook):
    if not git("rev-parse", "--show-toplevel"):
        return ""
    if session_id(hook) and not session_start_state(hook):
        state = session_state()  # git runs outside the write lock
        with tx() as con:  # a resumed session keeps the state it first started from
            con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)", (repo_name(), session_id(hook), state))
    d = branch_run()
    rec = load(d) if d else None
    if not rec or rec.get("finished") or rec.get("orphaned") or finish_if_done(d, rec):
        return ""
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


EVENTS = ROOT / "events"  # one <UTC date>.jsonl per day, so old days can be dropped without rewriting a file hooks append to
KEEP_DAYS = 14
ACTIVE = datetime.timedelta(minutes=30)
PORT = int(os.environ.get("KIP_DASHBOARD_PORT", 7377))
SERVERS = ROOT / "servers"  # <pid>.json for each dashboard a Claude Code session started, for its end hook to stop
OWNER = "CLAUDE_CODE_SESSION_ID"
STAGES = ["decompose", "implement", "tidy", "review", "receipts"]
SKILL_STAGES = {"kip-decompose": "decompose", "kip-messy-bun": "implement", "kip-marie-kondo": "tidy",
                "code-review": "review", "kip-receipts": "receipts"}
EDITS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "afterFileEdit"}
SAID = 20000  # a prompt or reply is kept whole up to here, for the dashboard to read back
SKILL_FILE = re.compile(r"/skills/(?:\S*/)?([\w.-]+)/SKILL\.md$")  # reading one is using that skill (Cursor has no skill hook)
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
TRACKER = re.compile(r"linear|jira|atlassian|github-issue", re.I)  # an MCP server or sink whose issue keys are issues
GITHUB_ISSUE = [re.compile(rf'"{k}": "?([\w.-]+)') for k in ("owner", "repo", "issue_number")]  # in a GitHub MCP call
URL = re.compile(r"https?://(?!(?:localhost|127\.0\.0\.1|\[::1\])[:/])[^\s<>()\"'`]*[^\s<>()\"'`.,;:!?]")  # not this machine; not a sentence's period


def append_event(payload, **fields):
    """One line in the event log for a host hook's payload; a failure here never touches the gate or the tool call."""
    try:
        e = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
             "session": payload.get("conversation_id") or payload.get("session_id"),  # Cursor: one id across its events
             "host": "cursor" if "workspace_roots" in payload else "claude", **fields}
        EVENTS.mkdir(parents=True, exist_ok=True)
        with open(EVENTS / f"{e['at'][:10]}.jsonl", "a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_tool(hook):
    """A tool call: its name and a short, redacted line of what it was asked to do. No git: this runs on every call."""
    ti = hook.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    what = next((ti[k] for k in ("skill", "command", "file_path", "pattern", "url", "query", "description")
                 if isinstance(ti.get(k), str)), None) or hook.get("command") or hook.get("file_path") or \
        (json.dumps(ti, ensure_ascii=False) if ti else "")
    append_event(hook, hook="tool", tool=hook.get("tool_name") or hook.get("hook_event_name") or "tool",
                 input=redact(str(what))[:200])


def log_said(hook, kind, text):
    """A prompt or the agent's reply, redacted, unless config's dashboard.conversation is false."""
    if not (isinstance(text, str) and text.strip()):
        return
    try:
        cwd = hook.get("cwd") or (hook.get("workspace_roots") or [None])[0]
        if cwd:
            os.chdir(cwd)  # the repo's own .kip/config.json can turn it off
        if not kip.load().get("dashboard", {}).get("conversation", True):
            return
    except (Exception, SystemExit):  # a config that won't load keeps nothing, and never fails the hook
        return
    append_event(hook, hook=kind, text=redact(text)[:SAID])


def log_prompt(hook):
    """The prompt, then the skills it invokes, by `/name` or by attaching its SKILL.md, logged as Skill calls;
    neither host has a hook for that. A `/name` counts when a skill folder has it, or it's a stage's skill
    (built-ins like code-review have no folder)."""
    log_said(hook, "prompt", hook.get("prompt"))
    roots = [Path.home() / d for d in (".kip/skills", ".claude/skills", ".cursor/skills")] + \
        [Path(w) / d for w in (hook.get("workspace_roots") or [hook.get("cwd")]) if w
         for d in (".claude/skills", ".cursor/skills")]
    attached = [m[1] for a in (hook.get("attachments") or []) if isinstance(a, dict)
                for m in [SKILL_FILE.search(str(a.get("file_path") or ""))] if m]
    named = [n for n in re.findall(r"(?:^|\s)/([\w.-]+)(?![\w./-])", str(hook.get("prompt") or ""))
             if n in SKILL_STAGES or any((r / n / "SKILL.md").exists() for r in roots)]
    for name in dict.fromkeys(named + attached):
        append_event(hook, hook="tool", tool="Skill", input=name)


def log_gate(event, hook, blocked, msg):
    try:
        d = branch_run()
        append_event(hook, hook=event, repo=repo_name(), github=github_url(), branch=git("branch", "--show-current"),
                     run=str(d) if d else None, blocked=blocked, message=msg)
    except Exception:
        pass


def ensure_server():
    """Start the dashboard server unless one is listening (or KIP_DASHBOARD=0); it exits on its own when idle."""
    if os.environ.get("KIP_DASHBOARD") == "0":
        return
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(PORT)], cwd=ROOT,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env={k: v for k, v in os.environ.items() if k != OWNER})  # shared: no session's to stop


def stop_servers(hook):
    """A session's end: stop the dashboards it started and left running."""
    sid = session_id(hook)
    for p in SERVERS.glob("*.json") if sid else ():
        try:
            if json.loads(p.read_text()).get("session") != sid:
                continue
        except (OSError, ValueError):  # still being written, or another end hook took it
            continue
        cmd = subprocess.run(["ps", "-o", "command=", "-p", p.stem], capture_output=True, text=True).stdout
        # messy-bun: a crashed server's pid reused by another dashboard would be stopped; record start times if that bites
        if "receipt.py" in cmd and " serve" in cmd:  # a dead server's pid may belong to something else by now
            os.kill(int(p.stem), signal.SIGTERM)
        p.unlink(missing_ok=True)


def reason_kind(line):
    """A blocking() line's kind, read from its verdict (claim lines start with their id), never its text."""
    v = re.match(r"c\d+ (.*)", line)
    return ("missing stage" if not v else "stale" if v[1].startswith("⚠️ stale") else
            "failing check" if v[1].startswith("❌") else "unaddressed")


def sources(tool, what):
    """What outside the machine a tool call reads from: an MCP server, a web search, or the URLs it names."""
    if tool.startswith("mcp__"):
        return [("mcp", tool.split("__")[1])]
    if tool == "WebSearch":
        return [("search", what)]
    return [("web", url) for url in URL.findall(what)]


def fold(rows, e):
    """Add one event to its session's row; the session's id, or None for a line that isn't an event."""
    if not (isinstance(e, dict) and e.get("session") and e.get("at")):
        return None
    sid = str(e["session"])
    r = rows.setdefault(sid, {"session": sid, "host": None, "repo": None, "github": None, "branch": None, "run": None,
                              "runs": [], "skills": [], "blocked": 0, "pr": False, "stage": None, "reached": [],
                              "tool": None, "sources": {}, "issues": {}, "started_only": True})
    r.update({k: e[k] for k in ("host", "repo", "github", "branch", "run") if e.get(k)}, last=e["at"], hook=e.get("hook"),
             started_only=r["started_only"] and e.get("hook") == "start")
    if e.get("run") and e["run"] not in r["runs"]:
        r["runs"].append(e["run"])
    found = [("link", url) for url in URL.findall(str(e.get("text") or ""))] if e.get("hook") == "prompt" else []
    if e.get("hook") == "tool":
        tool, what = str(e.get("tool")), str(e.get("input") or "")
        r["tool"] = f"{tool}: {what}" if what else tool
        found = sources(tool, what)
        server = found[0][1] if found and found[0][0] == "mcp" else ""
        gh = [m.search(what) for m in GITHUB_ISSUE] if re.search("github", server, re.I) else []
        keys = ISSUE_KEY.findall(what) if TRACKER.search(server) else \
            ["{}/{}#{}".format(*(m[1] for m in gh))] if gh and all(gh) else []
        for key in keys:
            r["issues"].setdefault(key, {"key": key, "via": server})
        skill_file = tool not in EDITS and SKILL_FILE.search(what)  # editing a skill isn't using it
        if skill_file:
            tool, what = "Skill", skill_file[1]
        stage = SKILL_STAGES.get(what) if tool == "Skill" else "implement" if tool in EDITS else \
            "receipts" if re.search(r"receipt\.py (run|render|publish)\b", what) else None
        if tool == "Skill" and what not in r["skills"]:
            r["skills"].append(what)
        if stage:
            r["stage"] = stage
            r["reached"] = sorted(set(r["reached"]) | {stage}, key=STAGES.index)
    for kind, name in found:
        s = r["sources"].setdefault(f"{kind} {name}", {"kind": kind, "name": name, "count": 0})
        s.update(count=s["count"] + 1, last=e["at"])
    if e.get("hook") == "stop" and e.get("message") != NUDGED:  # the stop it let through after a block isn't a pass
        r["blocked"] = r["blocked"] + 1 if e.get("blocked") else 0
    if e.get("hook") == "pr" and not e.get("blocked"):
        r["pr"] = True
    return sid


def stored(run):
    """A run by its id or folder, or None when kip.db doesn't have it."""
    rid = Path(run).name
    return load(rid) if db().execute("SELECT 1 FROM runs WHERE id = ?", (rid,)).fetchone() else None


def run_summary(rec):
    """What a session's segments show of a run: where it publishes and how its claims stand."""
    counts, asking = {}, 0
    for c in rec["claims"]:
        v = verdict(c, rec).split(":")[0]
        counts[v] = counts.get(v, 0) + 1
        asking += bool(c["unverified"]) and not c.get("accepted")
    return {"id": rec["id"], "branch": rec.get("branch"), "level": rec["level"], "issue": rec.get("issue"),
            "target": rec.get("target"), "finished": bool(rec.get("finished")), "claims": counts, "asking": asking,
            "sinks": [{"name": s, "url": rec.get("published", {}).get(s, {}).get("url")} for s in rec["sinks"]]}


def run_detail(rec):
    """A run as its review panel shows it: each claim's verdict, its evidence, and links to what it recorded."""
    def url(f):
        return f and f"/runs/{rec['id']}/{f['path']}"

    return {**run_summary(rec), "commit": rec.get("commit"), "claims": [
        {"id": c["id"], "text": c["text"], "unit": c.get("unit"), "stage": c.get("stage"),
         "provider": c.get("provider") or "generic", "verdict": verdict(c, rec), "unverified": c.get("unverified"),
         "accepted": c.get("accepted"),
         "evidence": [{"id": e["id"], "label": e.get("label"), "summary": summarize(e, full=True), "passed": e.get("passed"),
                       "phase": e.get("phase"), "excerpt": e.get("excerpt"), "commit": e.get("commit"),
                       "remote": bool(e.get("remote")), "log": url(e.get("log")), "patch": url(e.get("patch"))}
                      for e in c["evidence"]],
         "files": [{"type": f["type"], "name": Path(f["path"]).name, "url": url(f)} for f in c["files"]]}
        for c in rec["claims"]]}


@functools.lru_cache(maxsize=256)
def hashed(path, mtime_ns, ctime_ns, size):
    """A file's sha256, worked out again only when it's touched (ctime too: a copy can keep mtime): a video
    is fetched in many ranges."""
    return sha256(path)


def finish(rows):
    """The rows as the dashboard shows them, newest first, leaving out sessions that only started: whether each
    is active, its runs, the issues they and its tracker calls name, its run's open reasons by kind and the
    stages it has claims for."""
    now, out = datetime.datetime.now(datetime.timezone.utc), []
    for r in rows.values():
        if r.pop("started_only"):  # Cursor starts a session for each empty composer; the prompt gets another
            continue
        recs = [rec for rec in map(stored, r["runs"]) if rec]
        if recs and all(x.get("orphaned") for x in recs):  # its work was set aside
            continue
        recs = [x for x in recs if not x.get("orphaned")]
        rec = next((x for x in recs if x["id"] == Path(r["run"]).name), None) if r["run"] else None
        open_ = {}
        for line in (blocking(rec, pr=True) if rec and not rec.get("finished") else []):
            open_.setdefault(reason_kind(line), []).append(line)
        claimed = {c.get("stage") for c in rec["claims"]} & set(STAGES) if rec else set()
        issues = dict(r["issues"])
        for x in recs:
            for s in x["sinks"]:
                if x.get("issue") and TRACKER.search(s):
                    issues.setdefault(x["issue"], {"key": x["issue"], "via": s})
        out.append({**r, "active": now - datetime.datetime.fromisoformat(r["last"]) < ACTIVE, "open": open_,
                    "reached": sorted(set(r["reached"]) | claimed, key=STAGES.index),
                    "runs": [run_summary(x) for x in recs], "issues": list(issues.values()),
                    "sources": sorted(r["sources"].values(), key=lambda s: s["last"], reverse=True)})
    return sorted(out, key=lambda r: r["last"], reverse=True)


def cmd_serve(a):
    """The live dashboard: the page, its JSON, and /stream. One thread follows the event log, folding each new
    line into memory and pushing it to open pages, so a request costs the same however long the log gets."""
    from collections import deque
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import queue
    import threading
    import time
    page = Path(__file__).resolve().with_name("dashboard.html").read_text()
    MASCOT = Path(__file__).resolve().parents[3] / "assets" / "mascot"
    EVENTS.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)  # outside any repo: verdicts in a long-lived server mustn't judge runs against one checkout's tree
    rows, timelines, streams, lock = {}, {}, set(), threading.Lock()
    reading = threading.Lock()  # request threads share one kip.db connection; one read transaction at a time
    offsets, partial, live = {}, {}, {"busy": time.time()}

    def sweep():
        cutoff = str(datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=KEEP_DAYS))
        for p in EVENTS.glob("*.jsonl"):
            if p.stem < cutoff:
                p.unlink()
                offsets.pop(p, None)
                partial.pop(p, None)

    def poll():
        for p in sorted(EVENTS.glob("*.jsonl")):
            size = p.stat().st_size
            if size <= offsets.get(p, 0):
                continue
            with open(p, "rb") as f:
                f.seek(offsets.get(p, 0))
                *lines, partial[p] = (partial.get(p, b"") + f.read(size - offsets.get(p, 0))).split(b"\n")
            offsets[p] = size
            for line in lines:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                with lock:
                    sid = fold(rows, e)
                    if sid:
                        timelines.setdefault(sid, deque(maxlen=500)).append(e)
                if sid:
                    for q in list(streams):
                        q.put(line)
                    live["busy"] = time.time()

    def follow():
        swept = time.time()
        while True:
            time.sleep(0.25)
            if time.time() - swept > 3600:
                sweep()
                swept = time.time()
            poll()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, ctype="application/json", code=200):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if (self.headers.get("Host") or "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
                return self.send('{"error": "wrong host"}', code=403)  # a rebound DNS name; not this machine's page
            path = self.path.split("?")[0]
            one = re.fullmatch(r"/api/sessions/([\w.-]+)", path)
            run = re.fullmatch(r"/api/runs/([\w-]+)", path)
            file = re.fullmatch(r"/runs/([\w-]+)/(files/[\w.-]+)", path)
            mascot = re.fullmatch(r"/(kip-(?:waiting|working|asking|success|failure)\.svg)", path)
            if path == "/":
                self.send(page, "text/html")
            elif mascot:
                self.send((MASCOT / mascot[1]).read_text(), "image/svg+xml")
            elif path == "/api/sessions":
                with lock:
                    snapshot = copy.deepcopy(rows)
                with reading:
                    body = json.dumps(finish(snapshot), ensure_ascii=False)
                self.send(body)
            elif one:
                with lock:
                    events = list(timelines.get(one[1], ()))
                self.send(json.dumps(events, ensure_ascii=False))
            elif run:
                with reading:
                    rec = stored(run[1])
                    body = rec and json.dumps(run_detail(rec), ensure_ascii=False)
                self.send(body) if body else self.send('{"error": "no such run"}', code=404)
            elif file:
                self.send_file(*file.groups())
            elif path == "/stream":
                self.stream()
            else:
                self.send('{"error": "not found"}', code=404)

        def send_file(self, rid, rel):
            """A file the run recorded, unchanged since; a byte range of it for video seeking."""
            with reading:
                rec = stored(rid)
            entry = next((f for f in recorded(rec) if f["path"] == rel), None) if rec else None
            p = ROOT / rec["repo"] / rid / rel if entry else None
            st = p.stat() if p and p.is_file() else None
            if not (st and hashed(str(p), st.st_mtime_ns, st.st_ctime_ns, st.st_size) == entry["sha256"]):
                return self.send('{"error": "no such file, or it changed after it was recorded"}', code=404)
            size, start, end, code = st.st_size, 0, st.st_size - 1, 200
            want = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if want and (want[1] or want[2]):
                start, end = (int(want[1]), min(int(want[2] or end), end)) if want[1] else (max(0, size - int(want[2])), end)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                code = 206
            media = entry.get("type") in ("image", "video", "trace")  # logs, patches and inputs are only ever text
            self.send_response(code)
            self.send_header("Content-Type", (mimetypes.guess_type(p.name)[0] if media else None) or
                             ("application/octet-stream" if media else "text/plain; charset=utf-8"))
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(end - start + 1))

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q, beat = queue.Queue(), time.time()
            streams.add(q)
            try:
                while True:
                    try:
                        self.wfile.write(b"data: " + q.get(timeout=0.25) + b"\n\n")
                    except queue.Empty:
                        if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1):
                            break  # the page closed
                        if time.time() - beat < 15:
                            continue
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    beat = time.time()
            except OSError:
                pass
            finally:
                streams.discard(q)
                live["busy"] = time.time()

    try:
        server = type("Server", (ThreadingHTTPServer,), {"request_queue_size": 64, "daemon_threads": True})(
            ("127.0.0.1", a.port), Handler)  # the default backlog of 5 refuses a page's burst of requests
    except OSError as err:
        sys.exit(f"kip dashboard: can't listen on 127.0.0.1:{a.port} ({err.strerror}); is it already running?")
    sweep()
    poll()
    threading.Thread(target=follow, daemon=True).start()

    def idle_exit():
        while True:
            time.sleep(min(5, a.idle))
            if not streams and time.time() - live["busy"] > a.idle:
                return server.shutdown()

    if a.idle:
        threading.Thread(target=idle_exit, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so a stopped server drops its record too
    record = SERVERS / f"{os.getpid()}.json"
    try:
        if os.environ.get(OWNER):
            SERVERS.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps({"session": os.environ[OWNER]}))
        print(f"kip dashboard: http://127.0.0.1:{a.port}", flush=True)
        server.serve_forever()
    finally:
        record.unlink(missing_ok=True)


def cmd_check(a):
    """The gate host hooks and CI call. Claude Code: exit 2 blocks, reasons on stderr. Cursor (its
    events carry workspace_roots): JSON on stdout, a follow-up message at stop, a deny before a PR."""
    if a.pr:
        return check_pr(a.pr)
    if a.hook in ("tool", "prompt", "response", "end"):  # never blocks, never adds to the agent's context
        hook = {}
        try:
            hook = json.loads(sys.stdin.read() or "{}")
            {"tool": log_tool, "prompt": log_prompt,
             "response": lambda h: log_said(h, "response", h.get("text")), "end": stop_servers}[a.hook](hook)
        except Exception:
            pass
        if isinstance(hook, dict) and "workspace_roots" in hook:  # Cursor reads a JSON answer, even if logging failed
            print(json.dumps({"continue": True} if a.hook == "prompt" else {}))
        return
    hook = {}
    try:
        hook = json.loads(sys.stdin.read() or "{}") if a.hook else {}
        cwd = hook.get("cwd") or (hook.get("workspace_roots") or [None])[0]
        if cwd:
            os.chdir(cwd)
        blocked, msg = (False, session_start(hook)) if a.hook == "start" else gate(a, hook)
    except Exception as err:  # hosts let a crashed hook through, so a broken gate blocks instead, once per turn
        if not a.hook:
            raise
        hook = hook if isinstance(hook, dict) else {}
        blocked = opens_pr(hook) if a.hook == "pr" else \
            a.hook == "stop" and not (hook.get("stop_hook_active") or hook.get("loop_count"))
        msg = (f"kip-receipts: the gate crashed ({type(err).__name__}: {err}). "
               "Fix it or tell the user; don't work around it.")
    if a.hook == "stop":  # Claude Code's reply; Cursor sends its own on afterAgentResponse
        log_said(hook, "response", hook.get("last_assistant_message"))
    if a.hook and (a.hook != "pr" or opens_pr(hook)):  # a plain shell or MCP call is the tool hook's to log
        log_gate(a.hook, hook, blocked, msg)
    if a.hook == "start":
        try:
            ensure_server()
        except Exception:
            pass
        return print(json.dumps({"additional_context": msg} if msg else {}) if "workspace_roots" in hook else msg)
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
    common.add_argument("--run", help="run id or its folder (default: this branch's latest run)")
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

    s = sub.add_parser("accept", parents=[common])
    s.add_argument("claim")
    s.add_argument("--reason", help="why it's fine as it stands")
    s.set_defaults(fn=cmd_accept)

    s = sub.add_parser("orphan", parents=[common])
    s.add_argument("--reason", help="why its work was abandoned")
    s.set_defaults(fn=cmd_orphan)

    s = sub.add_parser("sweep")
    s.set_defaults(fn=cmd_sweep)

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
    s.add_argument("--file", help="a run file (files/NAME or NAME) the agent uploaded itself; --url is its reference")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("path", parents=[common])
    s.set_defaults(fn=cmd_path)

    s = sub.add_parser("json", parents=[common])
    s.set_defaults(fn=cmd_json)

    s = sub.add_parser("db", parents=[common])
    s.add_argument("--migrate", action="store_true", help="create or upgrade kip.db and import the JSON store")
    s.set_defaults(fn=cmd_db)

    s = sub.add_parser("check", parents=[common])
    g = s.add_mutually_exclusive_group()
    g.add_argument("--hook", choices=["start", "stop", "pr", "tool", "prompt", "response", "end"], help="read a host hook's JSON event from stdin")
    g.add_argument("--pr", help="check the ledger published on this pull request (CI; needs gh)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("serve", parents=[common])
    s.add_argument("--port", type=int, default=PORT)
    s.add_argument("--idle", type=float, default=float(os.environ.get("KIP_DASHBOARD_IDLE", 1800)),
                   help="seconds with no events and no open pages before it exits; 0 never does")
    s.set_defaults(fn=cmd_serve)

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
