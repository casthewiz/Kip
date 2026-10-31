#!/usr/bin/env python3
"""Kip's surfaces: one layered config for every place Kip plugs into.

  kip.py config                         print the merged config
  kip.py doctor                         what each configured surface needs, and whether it's here
  kip.py install [--host NAME] [--project] [--conversation | --no-conversation]
                                        link skills (and rules, and hooks) into each host,
                                        and this repo at ~/.kip/kip; idempotent. The flag saves
                                        whether the session log keeps prompts and replies. A user
                                        install also runs the dashboard as a login service
                                        (launchd or systemd) from a copy at ~/.kip/dashboard, which
                                        re-running install refreshes; KIP_DASHBOARD=0 skips it. A user
                                        install also adds git hooks to this repo that re-run it after a
                                        commit, merge, checkout, or rebase; KIP_GIT_HOOKS=0 skips them.
                                        A user install refuses to run from a linked worktree
                                        (KIP_INSTALL_FROM_WORKTREE=1 overrides)
  kip.py worktree new NAME [--base REF] [--parent RUN --unit N]
                                        a linked worktree of this repo at .kip/worktrees/NAME on branch
                                        kip/NAME (from REF, default HEAD): copies the gitignored files
                                        .worktreeinclude names, runs workflow.worktree.setup there, and with
                                        --parent starts the unit's receipts run; prints its path
  kip.py setup [--skip NAME]           one-time steps a person does (sign-ins, downloads) that aren't
                                        done yet, and how; sessions are reminded of them until done or skipped
  kip.py uninstall [--host NAME] [--project]
                                        undo install: Kip's links, rules, hooks, and service; the user's own stay

Config layers, later wins: defaults.json next to this file, then
$KIP_HOME/config.json (default ~/.kip), then <repo>/.kip/config.json. Dicts
merge, anything else replaces, null deletes. A `doc` path is relative to the
file that sets it. Surface kinds are hosts, connectors, evidence,
destinations and setup, plus `workflow` (stages a PR needs claims for) and `dashboard`
(`conversation`: whether the session log keeps prompts and replies); see docs/configuration.md
for the schema.

Standard library only, Python 3.8+.
"""
import argparse
import json
import os
import plistlib
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIP_HOME = Path(os.environ.get("KIP_HOME", Path.home() / ".kip"))
RULES = ROOT / "rules" / "user-rules.md"
RECEIPT = ROOT / "skills" / "verification" / "kip-receipts" / "receipt.py"
APP = KIP_HOME / "dashboard"  # the copy the service runs: macOS stalls a login agent reading ~/Documents and the like
GIT_HOOKS = ("post-commit", "post-merge", "post-checkout", "post-rewrite")
GIT_HOOK = f"#!/bin/sh\n# kip: re-sync every host after this repo changes\npython3 {shlex.quote(str(ROOT / 'kip.py'))} install >/dev/null 2>&1\nexit 0\n"
SURFACES = ("hosts", "connectors", "evidence", "destinations", "setup")
CONNECTOR_KINDS = ("mcp", "tool")
POLICIES = ("off", "nudge", "required")
ASK = {"decompose": "`/kip-decompose` (lite is enough for a small change)",  # workflow.skills' stages, as a gate asks
       "implement": "`/kip-messy-bun` (lite is enough for a small change)",
       "tidy": "`/kip-marie-kondo` over this session's changes"}
RULES_BLOCK = re.compile(r"<!-- kip rules: .*? -->\s*(.*?)\s*<!-- /kip rules -->\s*", re.S)  # Kip's block in a rules value
SECRET_ENV = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)


def toplevel():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else None


def layers():
    top = toplevel()
    return [ROOT / "defaults.json", KIP_HOME / "config.json"] + ([top / ".kip" / "config.json"] if top else [])


def merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    cfg = {}
    for path in layers():
        if not path.is_file():
            continue
        try:
            layer = json.loads(path.read_text())
        except ValueError as err:
            sys.exit(f"{path}: {err}")
        unknown = set(layer) - set(SURFACES) - {"default_destination", "workflow", "dashboard"}
        if unknown:
            sys.exit(f"{path}: unknown surface kind {', '.join(sorted(unknown))}; valid: {', '.join(SURFACES)}")
        for entry in (layer.get("evidence") or {}).values():
            if entry and entry.get("doc"):
                entry["doc"] = str((path.parent / entry["doc"]).resolve())
        cfg = merge(cfg, layer)
    for kind in SURFACES:
        cfg.setdefault(kind, {})
    validate(cfg)
    return cfg


def validate(cfg):
    for kind in SURFACES:
        for name, entry in cfg[kind].items():
            if entry.get("via") and entry["via"] not in cfg["connectors"]:
                sys.exit(f"{kind}.{name}: via {entry['via']} isn't a connector; connectors: "
                         f"{', '.join(cfg['connectors']) or 'none'}")
            for k in entry.get("settings", {}):
                if SECRET_ENV.search(k):
                    sys.exit(f"{kind}.{name}: {k} looks secret; set it in the environment, never in config")
    for name, c in cfg["connectors"].items():
        if c.get("kind") not in CONNECTOR_KINDS:
            sys.exit(f"connectors.{name}: kind must be one of {', '.join(CONNECTOR_KINDS)}")
    for name, e in cfg["evidence"].items():
        if e.get("fallback") and e["fallback"] not in cfg["evidence"]:
            sys.exit(f"evidence.{name}: fallback {e['fallback']} isn't an evidence surface")
    for name, e in cfg["setup"].items():
        if not (e.get("about") and e.get("run") and e.get("requires")):
            sys.exit(f"setup.{name} needs about, run, and requires (how to tell it's done)")
    stages = cfg.get("workflow", {}).get("stages", [])
    if not (isinstance(stages, list) and all(isinstance(x, str) for x in stages)):
        sys.exit("workflow.stages must be a list of stage names")
    if cfg.get("workflow", {}).get("worktrees", "nudge") not in POLICIES:
        sys.exit("workflow.worktrees must be off, nudge or required")
    skills = cfg.get("workflow", {}).get("skills", {})
    if not (isinstance(skills, dict) and set(skills) <= set(ASK) and all(v in POLICIES for v in skills.values())):
        sys.exit(f"workflow.skills must map {', '.join(ASK)} to off, nudge or required")
    worktree = cfg.get("workflow", {}).get("worktree", {})
    if not isinstance(worktree, dict):
        sys.exit("workflow.worktree must be an object, like {\"setup\": \"npm ci\"}")
    setup = worktree.get("setup")
    if setup is not None and not is_command(setup):
        sys.exit("workflow.worktree.setup must be a command: a string or a list of arguments")
    checks = cfg.get("workflow", {}).get("checks", {})
    if not isinstance(checks, dict):
        sys.exit("workflow.checks must map check names to commands")
    for name, cmd in checks.items():
        if not is_command(cmd):
            sys.exit(f"workflow.checks.{name} must be a command: a string or a list of arguments")
    if not isinstance(cfg.get("dashboard", {}).get("conversation", True), bool):
        sys.exit("dashboard.conversation must be true or false")
    if cfg.get("default_destination", "local") not in cfg["destinations"]:
        sys.exit(f"default_destination {cfg['default_destination']} isn't a destination")


def is_command(cmd):
    return isinstance(cmd, str) and cmd.strip() or isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)


def argv(cmd):
    return cmd if isinstance(cmd, list) else ["sh", "-c", cmd]


def env_name(entry, setting):
    return entry.get("env", {}).get(setting, setting)


def settings(entry):
    """Values for the settings an entry requires: a literal from config, else its env var."""
    lit = entry.get("settings", {})
    names = entry.get("requires", {}).get("env", [])
    return {n: lit[n] if n in lit else os.environ.get(env_name(entry, n)) for n in names}


def missing(entry):
    req = entry.get("requires", {})
    return ([env_name(entry, n) for n, v in settings(entry).items() if not v]
            + [c for c in req.get("cmd", []) if not shutil.which(c)]
            + [f for f in req.get("file", []) if not Path(f).expanduser().exists()])


def status(cfg, entry):
    """ready, missing <what>, or agent-confirm: a script can't see the agent's MCP servers or tools."""
    if entry.get("via"):
        c = cfg["connectors"][entry["via"]]
        return f"agent-confirm via {entry['via']} ({c['kind']})"
    if "kind" in entry:
        return f"agent-confirm ({entry['kind']})"
    m = missing(entry)
    return "missing " + ", ".join(m) if m else "ready"


def cmd_config(a):
    print(json.dumps(load(), indent=2, ensure_ascii=False))


def cmd_doctor(a):
    cfg = load()
    for kind in SURFACES:
        print(kind)
        for name, entry in cfg[kind].items():
            s = status(cfg, entry)
            if s != "ready" and entry.get("fallback"):
                s += f" → falls back to {entry['fallback']}"
            if kind == "hosts" and entry.get("hooks"):
                s += " · hooks " + ("installed" if hooks_installed(entry["hooks"]) else "not installed (run install)")
            elif kind == "hosts" and entry.get("rules"):
                s += " · rules only, no hooks"
            if kind == "setup" and s != "ready":
                s += f" → {entry['run']}"
            if kind == "hosts" and entry.get("hooks", {}).get("trust") and hooks_installed(entry["hooks"]):
                s += " · " + hooks_trust(entry["hooks"])
            if kind == "hosts" and rules_store(entry.get("rules", {})):
                s += " · rules " + rules_copy_status(entry["rules"])
            print(f"  {name:14} {s}")
    print_receipts()


def pending_setup():
    """The setup steps not done yet: (name, entry)."""
    return [(n, e) for n, e in load()["setup"].items() if missing(e)]


def cmd_setup(a):
    if a.skip:
        path = KIP_HOME / "config.json"
        save_layer(path, "setup", a.skip, None)  # null deletes it from the merged config
        return print(f"setup: skipped {a.skip} ({path})")
    for name, e in pending_setup():
        print(f"{name}: {e['about']}\n  {e['run']}")


def skills():
    found = {}
    for f in sorted((ROOT / "skills").rglob("SKILL.md")):
        if f.parent.name in found:
            sys.exit(f"skill name {f.parent.name} is used twice: {found[f.parent.name]} and {f.parent}")
        found[f.parent.name] = f.parent
    return found


def link(dest, found):
    dest.mkdir(parents=True, exist_ok=True)
    for p in dest.iterdir():  # links to skills that moved or were deleted
        if p.is_symlink() and not p.exists() and os.readlink(p).startswith(str(ROOT)):
            p.unlink()
    for name, src in found.items():
        t = dest / name
        if t.is_symlink():
            t.unlink()
        elif t.exists():
            print(f"  skipped {t}: exists and isn't a link")
            continue
        t.symlink_to(src)
    print(f"  {len(found)} skills → {dest}")


def install_rules(rules):
    store = rules_store(rules)
    if store:
        return install_rules_copy(rules, store)
    if "manual" in rules:
        print("  rules: " + rules["manual"].format(rules=RULES))
        return
    target = Path(rules["append"]).expanduser()
    lines = target.read_text().splitlines() if target.exists() else []
    if any(is_import(l) for l in lines):
        print(f"  rules: already imported in {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines + [rules["line"].format(rules=RULES)]) + "\n")
    print(f"  rules: imported in {target}")


def rules_db(rules):
    return next((p for p in (Path(os.path.expandvars(d)).expanduser() for d in rules.get("sqlite", [])) if p.exists()),
                None)


def rules_store(rules):
    """Where a host keeps a copy of the rules: a plain file, or an app's key-value store when it exists."""
    return Path(rules["file"]).expanduser() if "file" in rules else rules_db(rules)


def is_import(line):
    return line.startswith("@") and Path(line[1:].strip()).expanduser().resolve() == RULES


def read_rules(rules, store):
    """The rules value a host keeps, or None when there's none."""
    if "file" in rules:
        return store.read_text() if store.exists() else None
    with sqlite3.connect(store) as con:
        row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (rules["key"],)).fetchone()
    return row[0] if row else None


def write_rules(rules, store, value):
    """Set the rules value a host keeps; None removes it."""
    if "file" in rules:
        if value is None:
            store.unlink()
        else:
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(value if value.endswith("\n") else value + "\n")
        return
    with sqlite3.connect(store) as con:
        if value is None:
            con.execute("DELETE FROM ItemTable WHERE key = ?", (rules["key"],))
        else:
            con.execute("INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)", (rules["key"], value))


def user_rules(value):
    """A rules value split around Kip's block: (Kip's block or None, the user's own rules)."""
    return RULES_BLOCK.search(value or ""), RULES_BLOCK.sub("", value or "").strip()


def rules_copy_status(rules):
    """A host holding a copy of the rules (not an import) goes stale when they change."""
    block, _ = user_rules(read_rules(rules, rules_store(rules)))
    if not block:
        return "not installed (run install)"
    return "current" if block.group(1) == RULES.read_text().strip() else "stale (run install)"


def install_rules_copy(rules, store):
    """Replace Kip's block in a rules value the host keeps; keep the user's own rules."""
    old = read_rules(rules, store)
    block = f"<!-- kip rules: {RULES} -->\n{RULES.read_text().strip()}\n<!-- /kip rules -->"
    value = "\n\n".join(filter(None, [user_rules(old)[1], block])) + "\n"
    if old == value:
        return print(f"  rules: already in {store}")
    write_rules(rules, store, value)
    print(f"  rules: written to {store} (restart the app to load them; re-run install after pulling)")


def is_kip(group):
    """Kip's hook groups are the ones calling receipt.py check, wherever the repo lived when installed.
    A group nests its commands under `hooks` (Claude Code) or is one command itself (Cursor)."""
    return any("kip-receipts/receipt.py check" in h.get("command", "") for h in group.get("hooks", [group]))


def fill(group):
    if "hooks" in group:
        return {**group, "hooks": [fill(h) for h in group["hooks"]]}
    return {**group, "command": group["command"].format(receipt=shlex.quote(str(RECEIPT)))}


def hook_groups(hooks):
    """The host's hook events with {receipt} filled in, as the host's settings expect them."""
    return {event: [fill(g) for g in groups] for event, groups in hooks["events"].items()}


def read_settings(path):
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError as err:
        sys.exit(f"{path}: {err}; fix it before installing hooks")


def hooks_installed(hooks):
    on = read_settings(Path(hooks["settings"]).expanduser()).get("hooks", {})
    return all([g for g in on.get(event, []) if is_kip(g)] == groups for event, groups in hook_groups(hooks).items())


def hooks_trust(hooks):
    """Whether the host trusts Kip's user hooks, from its app server's hooks/list (Codex's)."""
    path = Path(hooks["settings"]).expanduser()
    exe = next(filter(None, map(shutil.which, hooks["trust"]["app_server"])), None)
    if not exe:
        return "hook trust unknown (Codex not found)"
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "kip", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {"cwds": [str(path.parent.parent)]}}]
    p = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(20, p.kill)  # a server that never answers closes stdout, ending the read
    timer.start()
    try:
        p.stdin.write("".join(json.dumps(m) + "\n" for m in msgs))
        p.stdin.flush()
        reply = next((m for m in map(json.loads, filter(lambda l: l.startswith("{"), p.stdout)) if m.get("id") == 2), {})
    except (OSError, ValueError):  # it exited early, or was killed mid-line
        reply = {}
    finally:
        timer.cancel()
        p.kill()
    if "result" not in reply:
        return "hook trust unknown (Codex didn't answer)"
    listed = [h for d in reply["result"].get("data", []) for h in d.get("hooks", [])]
    mine = [h for h in listed if is_kip(h) and Path(h.get("sourcePath", "")).resolve() == path.resolve()]
    untrusted = [h for h in mine if h.get("trustStatus") != "trusted"]
    if not mine:
        return "hooks not listed by Codex"
    if untrusted:
        return f"{len(untrusted)} of {len(mine)} hooks untrusted ({hooks['trust']['manual']})"
    return "hooks trusted"


def install_hooks(hooks, path):
    """Replace Kip's own hook groups in the host's settings; leave every other hook alone. True if Kip's groups
    changed, which a host that trusts hooks by their hash (Codex) stops running until they're trusted again."""
    settings = {**hooks.get("init", {}), **read_settings(path)}
    on = settings.setdefault("hooks", {})
    changed = False
    for event, groups in hook_groups(hooks).items():
        changed |= [g for g in on.get(event, []) if is_kip(g)] != groups
        on[event] = [g for g in on.get(event, []) if not is_kip(g)] + groups
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  hooks: {', '.join(hooks['events'])} in {path}")
    return changed


def hosts(a):
    """The hosts named with --host (all of them by default), and the repo root with --project."""
    cfg = load()
    unknown = set(a.host) - set(cfg["hosts"])
    if unknown:
        sys.exit(f"unknown host {', '.join(sorted(unknown))}; hosts: {', '.join(cfg['hosts'])}")
    top = toplevel() if a.project else None
    if a.project and not top:
        sys.exit("--project needs a git repo")
    return [(n, h) for n, h in cfg["hosts"].items() if not a.host or n in a.host], top


def where(entry, key, top):
    """A host path: the project one under the repo root with --project, else the user one."""
    return top / entry[f"project_{key}"] if top else Path(entry[key]).expanduser()


def print_receipts(*args, prefix=""):
    """kip.db's status from receipt.py, which owns it: its path, schema version, and run count."""
    r = subprocess.run([sys.executable, str(RECEIPT), "db", *args], capture_output=True, text=True)
    print("receipts")
    for line in r.stdout.strip().splitlines() if r.returncode == 0 else [f"error: {r.stderr.strip()}"]:
        print(f"  {prefix}{line}")


def save_layer(path, kind, key, value):
    """Set kind.key in one config layer, beside whatever else it holds."""
    layer = json.loads(path.read_text()) if path.is_file() else {}
    layer.setdefault(kind, {})[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layer, indent=2) + "\n")


def service(platform=sys.platform):
    """The dashboard as a login service the OS keeps running: its file, that file's text, and the commands
    that load and unload it; None where there's no service manager (a session's start hook still launches it)."""
    serve = [sys.executable, str(APP / RECEIPT.relative_to(ROOT)), "serve", "--idle", "0"]
    if os.environ.get("KIP_DASHBOARD_PORT"):  # the port a session's start hook will look for
        serve += ["--port", os.environ["KIP_DASHBOARD_PORT"]]
    if platform == "darwin":
        label, path = "com.kip.dashboard", Path.home() / "Library" / "LaunchAgents" / "com.kip.dashboard.plist"
        text = plistlib.dumps({"Label": label, "ProgramArguments": serve, "RunAtLoad": True, "KeepAlive": True})
        domain = f"gui/{os.getuid()}"
        return path, text.decode(), [["launchctl", "bootstrap", domain, str(path)]], \
            [["launchctl", "bootout", f"{domain}/{label}"]]
    if platform.startswith("linux"):
        path = Path.home() / ".config" / "systemd" / "user" / "kip-dashboard.service"
        text = (f"[Unit]\nDescription=Kip dashboard\n\n[Service]\nExecStart={shlex.join(serve)}\nRestart=always\n\n"
                "[Install]\nWantedBy=default.target\n")
        ctl = ["systemctl", "--user"]
        return path, text, [ctl + ["daemon-reload"], ctl + ["enable", "--now", path.name]], \
            [ctl + ["disable", "--now", path.name]]
    return None


def run_quiet(cmds):
    """Run each command; the first failure's message, or None."""
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            return (r.stderr or r.stdout).strip() or f"{cmd[0]} exited {r.returncode}"


def install_service():
    s = service()
    if not s or os.environ.get("KIP_DASHBOARD") == "0":
        return
    path, text, load, unload = s
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        run_quiet(unload)  # reload, so a changed copy, file, or python takes effect
    shutil.rmtree(APP, ignore_errors=True)
    shutil.copytree(ROOT, APP, ignore=shutil.ignore_patterns(".git", ".claude", "__pycache__", "node_modules"))
    path.write_text(text)
    err = run_quiet(load)
    print(f"dashboard\n  service: {path}" + (f" (not started: {err})" if err else ""))


def git_hooks_dir():
    """This repo's hooks folder, or None when it isn't a git checkout or KIP_GIT_HOOKS=0."""
    if os.environ.get("KIP_GIT_HOOKS") == "0":
        return None
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-path", "hooks"], capture_output=True, text=True)
    return None if r.returncode else ROOT / r.stdout.strip()


def main_checkout(cwd=None):
    """The main checkout's folder, from a worktree too; None outside a clone with a .git folder (a submodule, say)."""
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd,
                       capture_output=True, text=True)
    common = Path(r.stdout.strip()) if r.returncode == 0 else None
    return common.resolve().parent if common and common.name == ".git" else None


def main_of_worktree():
    """The main checkout's folder when this copy of Kip is a linked worktree of it, else None."""
    main = main_checkout(ROOT)
    return main if main and not main.samefile(ROOT) else None  # samefile: ROOT keeps however it was spelled


def install_git_hooks(d):
    """Re-run install whenever the repo changes, so the copies (Cursor's rules, the dashboard) and links stay current."""
    d.mkdir(parents=True, exist_ok=True)
    print("git hooks")
    written = []
    for name in GIT_HOOKS:
        p = d / name
        if p.exists() and "# kip:" not in p.read_text():
            print(f"  skipped {p}: not Kip's")
            continue
        p.write_text(GIT_HOOK)
        p.chmod(0o755)
        written.append(name)
    if written:
        print(f"  {', '.join(written)} in {d} re-run install")


def uninstall_git_hooks(d):
    gone = [p for p in (d / n for n in GIT_HOOKS) if p.exists() and "# kip:" in p.read_text()]
    for p in gone:
        p.unlink()
    if gone:
        print(f"git hooks\n  removed from {d}")


def uninstall_service():
    s = service()
    if not s or not s[0].exists():
        return
    run_quiet(s[3])
    s[0].unlink()
    shutil.rmtree(APP, ignore_errors=True)
    print(f"dashboard\n  service: removed {s[0]}")


def cmd_install(a):
    main = None if a.project else main_of_worktree()
    if main and os.environ.get("KIP_INSTALL_FROM_WORKTREE") != "1":
        sys.exit(f"kip install: {ROOT} is a linked worktree; hooks, ~/.kip/kip and the dashboard would point at a "
                 f"folder that goes when it's removed. Install from the main checkout: python3 {main / 'kip.py'} install "
                 "(KIP_INSTALL_FROM_WORKTREE=1 installs from here anyway)")
    selected, top = hosts(a)
    if a.conversation is not None:
        path = top / ".kip" / "config.json" if a.project else KIP_HOME / "config.json"
        save_layer(path, "dashboard", "conversation", a.conversation)
        print(f"dashboard: prompts and replies {'kept' if a.conversation else 'not kept'} ({path})")
    found = skills()
    if not a.project:  # a host-neutral path to kip.py for skills to call
        KIP_HOME.mkdir(parents=True, exist_ok=True)
        if (KIP_HOME / "kip").is_symlink():
            (KIP_HOME / "kip").unlink()
        (KIP_HOME / "kip").symlink_to(ROOT)
    for name, h in selected:
        if a.project and not h.get("project_skills"):
            continue
        m = missing(h)
        if m and not a.host:
            print(f"{name}: skipped, missing {', '.join(m)}")
            continue
        print(name)
        link(where(h, "skills", top), found)
        if h.get("rules") and not a.project:
            install_rules(h["rules"])
        if h.get("hooks"):
            if install_hooks(h["hooks"], where(h["hooks"], "settings", top)) and h["hooks"].get("trust"):
                print("  hooks: new or changed; " + h["hooks"]["trust"]["project_manual" if top else "manual"])
    if not a.project:
        install_service()
        if git_hooks_dir():
            install_git_hooks(git_hooks_dir())
    print_receipts("--migrate")


def uninstall_hooks(hooks, path):
    """Take Kip's hook groups out; drop what that leaves empty, and the file if install created it."""
    if not path.exists():
        return
    settings = read_settings(path)
    on = settings.get("hooks", {})
    for event in list(on):
        on[event] = [g for g in on[event] if not is_kip(g)]
        if not on[event]:
            del on[event]
    if not on:
        settings.pop("hooks", None)
    if settings in ({}, hooks.get("init")):
        path.unlink()
    else:
        path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  hooks: removed from {path}")


def uninstall_rules(rules):
    store = rules_store(rules)
    if store:
        block, mine = user_rules(read_rules(rules, store))
        if not block:
            return
        write_rules(rules, store, mine or None)  # a file Kip's block alone was in goes
        return print(f"  rules: removed from {store}")
    target = Path(rules["append"]).expanduser() if "append" in rules else None
    if target and target.exists():
        lines = [l for l in target.read_text().splitlines() if not is_import(l)]
        if lines:
            target.write_text("\n".join(lines) + "\n")
        else:
            target.unlink()
        print(f"  rules: removed from {target}")


def unlink_skills(dest):
    if not dest.is_dir():
        return
    gone = [p for p in dest.iterdir() if p.is_symlink() and os.readlink(p).startswith(str(ROOT))]
    for p in gone:
        p.unlink()
    print(f"  {len(gone)} skills unlinked from {dest}")


def cmd_uninstall(a):
    selected, top = hosts(a)
    for name, h in selected:
        if a.project and not h.get("project_skills"):
            continue
        print(name)
        unlink_skills(where(h, "skills", top))
        if h.get("rules") and not a.project:
            uninstall_rules(h["rules"])
        if h.get("hooks"):
            uninstall_hooks(h["hooks"], where(h["hooks"], "settings", top))
    if not a.project and (KIP_HOME / "kip").is_symlink():
        (KIP_HOME / "kip").unlink()
    if not a.project:
        uninstall_service()
        if git_hooks_dir():
            uninstall_git_hooks(git_hooks_dir())
    print_receipts(prefix="kept: ")


def git_in(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def cmd_worktree(a):
    """A linked worktree ready to work in: on its own branch, with the gitignored files .worktreeinclude names
    (the convention Claude Code uses), set up, and with --parent, its unit's receipts run started."""
    top = toplevel()
    if not top:
        sys.exit("kip worktree: not in a git repo")
    main = main_checkout(top) or top  # a submodule's worktrees go in its own checkout
    path, branch = main / ".kip" / "worktrees" / a.name, f"kip/{a.name}"
    exclude = Path(git_in(top, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())
    lines = exclude.read_text().splitlines() if exclude.is_file() else []
    if "/.kip/worktrees/" not in lines:  # the main checkout shouldn't see its worktrees as untracked files
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(lines + ["/.kip/worktrees/"]) + "\n")
    git_in(main, "worktree", "add", "-q", "-b", branch, str(path), a.base or git_in(top, "rev-parse", "HEAD").strip())
    include = main / ".worktreeinclude"
    if include.is_file():
        named = git_in(main, "ls-files", "-z", "--others", "--ignored", f"--exclude-from={include}")
        r = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=main, input=named,
                           capture_output=True, text=True)  # only files git ignores, as Claude Code copies them
        for rel in filter(None, r.stdout.split("\0")):
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(main / rel, path / rel)
    os.chdir(path)  # the worktree's own config, which may be a copied, gitignored one
    setup = load().get("workflow", {}).get("worktree", {}).get("setup")
    if setup and subprocess.run(argv(setup), cwd=path, stdout=sys.stderr).returncode:
        sys.exit(f"kip worktree: setup failed in {path}; fix it there, or remove it: git worktree remove {path}")
    if a.parent:
        r = subprocess.run([sys.executable, str(RECEIPT), "init", "--parent", a.parent, "--unit", a.unit or ""],
                           cwd=path, stdout=sys.stderr)
        if r.returncode:
            sys.exit(f"kip worktree: couldn't start unit {a.unit}'s run in {path}")
    print(path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config").set_defaults(fn=cmd_config)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("worktree").add_subparsers(dest="action", required=True).add_parser("new")
    s.add_argument("name")
    s.add_argument("--base", help="the commit or branch it starts from (default: this checkout's HEAD)")
    s.add_argument("--parent", help="a plan's receipts run; starts the unit's run in the worktree (with --unit)")
    s.add_argument("--unit")
    s.set_defaults(fn=cmd_worktree)
    s = sub.add_parser("setup")
    s.add_argument("--skip", metavar="NAME", help="never remind about this step again (saved to config)")
    s.set_defaults(fn=cmd_setup)
    for name, fn, verb in (("install", cmd_install, "link into"), ("uninstall", cmd_uninstall, "unlink from")):
        s = sub.add_parser(name)
        s.add_argument("--host", action="append", default=[], help="only this host, even if it isn't detected")
        s.add_argument("--project", action="store_true", help=f"{verb} this repo's project skills dirs instead")
        s.set_defaults(fn=fn, conversation=None)
        if name == "install":
            g = s.add_mutually_exclusive_group()
            g.add_argument("--conversation", action="store_const", const=True,
                           help="keep prompts and replies in the session log (the default), saved to config")
            g.add_argument("--no-conversation", dest="conversation", action="store_const", const=False,
                           help="keep neither, saved to config")
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
