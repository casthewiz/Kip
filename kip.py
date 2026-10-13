#!/usr/bin/env python3
"""Kip's surfaces: one layered config for every place Kip plugs into.

  kip.py config                         print the merged config
  kip.py doctor                         what each configured surface needs, and whether it's here
  kip.py install [--host NAME] [--project]
                                        link skills (and rules, and hooks) into each host,
                                        and this repo at ~/.kip/kip; idempotent
  kip.py uninstall [--host NAME] [--project]
                                        undo install: Kip's links, rules, and hooks; the user's own stay

Config layers, later wins: defaults.json next to this file, then
$KIP_HOME/config.json (default ~/.kip), then <repo>/.kip/config.json. Dicts
merge, anything else replaces, null deletes. A `doc` path is relative to the
file that sets it. Surface kinds are hosts, connectors, evidence and
destinations, plus `workflow` (stages a PR needs claims for); see the README
for the schema.

Standard library only, Python 3.8+.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIP_HOME = Path(os.environ.get("KIP_HOME", Path.home() / ".kip"))
RULES = ROOT / "rules" / "user-rules.md"
RECEIPT = ROOT / "skills" / "verification" / "kip-receipts" / "receipt.py"
SURFACES = ("hosts", "connectors", "evidence", "destinations")
CONNECTOR_KINDS = ("mcp", "tool")
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
        unknown = set(layer) - set(SURFACES) - {"default_destination", "workflow"}
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
    stages = cfg.get("workflow", {}).get("stages", [])
    if not (isinstance(stages, list) and all(isinstance(x, str) for x in stages)):
        sys.exit("workflow.stages must be a list of stage names")
    checks = cfg.get("workflow", {}).get("checks", {})
    if not isinstance(checks, dict):
        sys.exit("workflow.checks must map check names to commands")
    for name, cmd in checks.items():
        if not (isinstance(cmd, str) and cmd.strip() or
                isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)):
            sys.exit(f"workflow.checks.{name} must be a command: a string or a list of arguments")
    if cfg.get("default_destination", "local") not in cfg["destinations"]:
        sys.exit(f"default_destination {cfg['default_destination']} isn't a destination")


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
            if kind == "hosts" and rules_db(entry.get("rules", {})):
                s += " · rules " + rules_copy_status(entry["rules"])
            print(f"  {name:14} {s}")
    print_receipts()


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
    db = rules_db(rules)
    if db:
        return install_rules_sqlite(db, rules["key"])
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


def is_import(line):
    return line.startswith("@") and Path(line[1:].strip()).expanduser().resolve() == RULES


def user_rules(con, key):
    """The rules value with Kip's block taken out: (row, the user's own rules)."""
    row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
    return row, re.sub(r"<!-- kip rules: .*?<!-- /kip rules -->\n?", "", row[0] if row else "", flags=re.S).strip()


def rules_copy_status(rules):
    """A host holding a copy of the rules (not an import) goes stale when they change."""
    with sqlite3.connect(rules_db(rules)) as con:
        row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (rules["key"],)).fetchone()
    block = re.search(r"<!-- kip rules: .*? -->\n(.*?)\n<!-- /kip rules -->", row[0] if row else "", flags=re.S)
    if not block:
        return "not installed (run install)"
    return "current" if block.group(1) == RULES.read_text().strip() else "stale (run install)"


def install_rules_sqlite(db, key):
    """Replace Kip's block in a rules value kept in an app's key-value store; keep the user's own rules."""
    begin, end = f"<!-- kip rules: {RULES} -->", "<!-- /kip rules -->"
    with sqlite3.connect(db) as con:
        row, mine = user_rules(con, key)
        value = "\n\n".join(filter(None, [mine, f"{begin}\n{RULES.read_text().strip()}\n{end}"])) + "\n"
        if row and row[0] == value:
            return print(f"  rules: already in {db}")
        con.execute("INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)", (key, value))
    print(f"  rules: written to {db} (restart the app to load them; re-run install after pulling)")


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


def install_hooks(hooks, path):
    """Replace Kip's own hook groups in the host's settings; leave every other hook alone."""
    settings = {**hooks.get("init", {}), **read_settings(path)}
    on = settings.setdefault("hooks", {})
    for event, groups in hook_groups(hooks).items():
        on[event] = [g for g in on.get(event, []) if not is_kip(g)] + groups
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  hooks: {', '.join(hooks['events'])} in {path}")


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


def cmd_install(a):
    selected, top = hosts(a)
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
            install_hooks(h["hooks"], where(h["hooks"], "settings", top))
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
    db = rules_db(rules)
    if db:
        with sqlite3.connect(db) as con:
            row, mine = user_rules(con, rules["key"])
            if not row or "<!-- kip rules: " not in row[0]:
                return
            if mine:
                con.execute("UPDATE ItemTable SET value = ? WHERE key = ?", (mine, rules["key"]))
            elif row:
                con.execute("DELETE FROM ItemTable WHERE key = ?", (rules["key"],))
        return print(f"  rules: removed from {db}")
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
    print_receipts(prefix="kept: ")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config").set_defaults(fn=cmd_config)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    for name, fn, verb in (("install", cmd_install, "link into"), ("uninstall", cmd_uninstall, "unlink from")):
        s = sub.add_parser(name)
        s.add_argument("--host", action="append", default=[], help="only this host, even if it isn't detected")
        s.add_argument("--project", action="store_true", help=f"{verb} this repo's project skills dirs instead")
        s.set_defaults(fn=fn)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
