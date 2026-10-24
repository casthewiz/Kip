#!/usr/bin/env python3
"""Self-check for kip.py (config layers, doctor, install): python3 test_kip.py"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = ROOT / "rules" / "user-rules.md"
RECEIPT = ROOT / "skills" / "verification" / "kip-receipts" / "receipt.py"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home, repo = Path(tmp, "home"), Path(tmp, "repo")
        (home / ".claude").mkdir(parents=True)
        (repo / ".kip").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in ("KIP_HOME", "LINEAR_API_KEY", "KIP_RECEIPTS_DIR")}
        env["HOME"] = str(home)
        env["KIP_DASHBOARD"] = "0"  # never load a real login service from a test
        env["KIP_GIT_HOOKS"] = "0"  # nor touch this checkout's git hooks
        env["KIP_INSTALL_FROM_WORKTREE"] = "1"  # this checkout may itself be a linked worktree

        def kip(*args, ok=True, **extra):
            r = subprocess.run([sys.executable, str(ROOT / "kip.py"), *args], cwd=repo, env={**env, **extra},
                               capture_output=True, text=True)
            assert (r.returncode == 0) == ok, f"{args}: exit {r.returncode}\n{r.stdout}{r.stderr}"
            return (r.stdout + r.stderr).strip()

        def write(path, cfg):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg))

        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

        # Layers: project beats user beats defaults; null deletes; doc paths resolve from their own file.
        write(home / ".kip" / "config.json", {"destinations": {"linear": {"env": {"LINEAR_API_KEY": "MY_LINEAR"}}},
                                              "default_destination": "linear"})
        write(repo / ".kip" / "config.json", {"default_destination": "local", "destinations": {"jira": None},
                                              "evidence": {"api": {"doc": "api.md"}}})
        cfg = json.loads(kip("config"))
        assert cfg["default_destination"] == "local" and "jira" not in cfg["destinations"], cfg
        assert cfg["destinations"]["linear"]["env"] == {"LINEAR_API_KEY": "MY_LINEAR"}, cfg
        assert cfg["evidence"]["api"]["doc"] == str((repo / ".kip" / "api.md").resolve()), cfg
        assert cfg["evidence"]["generic"]["doc"].startswith(str(ROOT)), cfg

        # Doctor: missing names the aliased env var; MCP/tool surfaces are never "ready" from a script.
        write(repo / ".kip" / "config.json", {"connectors": {"linear-mcp": {"kind": "mcp"}},
                                              "destinations": {"tracker": {"via": "linear-mcp"}}})
        doc = kip("doctor")
        assert "linear         missing MY_LINEAR" in doc, doc
        assert "ready" in kip("doctor", MY_LINEAR="x").split("linear ")[1].splitlines()[0]
        assert "tracker        agent-confirm via linear-mcp (mcp)" in doc, doc
        assert "linear-mcp     agent-confirm (mcp)" in doc, doc
        assert "browser        agent-confirm via browser (tool) → falls back to generic" in doc, doc

        # Validation fails loudly and names what's valid.
        for bad, msg in [({"surfaces": {}}, "unknown surface kind surfaces; valid: hosts, connectors, evidence"),
                         ({"destinations": {"x": {"via": "nope"}}}, "via nope isn't a connector"),
                         ({"connectors": {"x": {"kind": "magic"}}}, "kind must be one of mcp, tool"),
                         ({"destinations": {"x": {"settings": {"X_TOKEN": "s"}}}}, "X_TOKEN looks secret"),
                         ({"evidence": {"x": {"fallback": "nope"}}}, "fallback nope isn't an evidence surface"),
                         ({"default_destination": "nope"}, "default_destination nope isn't a destination"),
                         ({"workflow": {"stages": "tidy"}}, "workflow.stages must be a list"),
                         ({"workflow": {"checks": ["make test"]}}, "workflow.checks must map check names"),
                         ({"workflow": {"checks": {"tests": ""}}}, "workflow.checks.tests must be a command"),
                         ({"dashboard": {"conversation": "no"}}, "dashboard.conversation must be true or false"),
                         ({"setup": {"x": {"about": "a"}}}, "setup.x needs about, run, and requires")]:
            write(repo / ".kip" / "config.json", bad)
            assert msg in kip("doctor", ok=False), msg
        (repo / ".kip" / "config.json").unlink()

        # A user install from a linked worktree is refused: what it links would go when the worktree is removed.
        clone, linked = Path(tmp, "kip-clone"), Path(tmp, "kip-linked")
        clone.mkdir()
        for name in ("kip.py", "defaults.json", "rules", "skills"):
            src = ROOT / name
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, clone / name)
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "kip"],
                    ["worktree", "add", "-q", "--detach", str(linked)]):
            subprocess.run(["git", *cmd], cwd=clone, check=True, capture_output=True)
        unset = {k: v for k, v in env.items() if k != "KIP_INSTALL_FROM_WORKTREE"}
        r = subprocess.run([sys.executable, str(linked / "kip.py"), "install"], cwd=repo, env=unset,
                           capture_output=True, text=True)
        assert r.returncode == 1 and "is a linked worktree" in r.stderr and str(clone.resolve() / "kip.py") in r.stderr, r
        assert not (home / ".kip" / "kip").exists(), "a refused install linked something"

        # Install: detected hosts only, every skill linked, rules imported once, idempotent.
        skills = sorted(p.parent.name for p in (ROOT / "skills").rglob("SKILL.md"))
        settings = home / ".claude" / "settings.json"
        mine = {"hooks": [{"type": "command", "command": "say done"}]}
        stale = {"hooks": [{"type": "command", "command": "python3 /old/skills/verification/kip-receipts/receipt.py check --hook stop"}]}
        write(settings, {"model": "x", "hooks": {"Stop": [mine, stale]}})
        assert "claude-code    ready · hooks not installed" in kip("doctor")
        # Receipts: install creates kip.db and imports the JSON store from before it; doctor reports it.
        kipdb = home / ".kip" / "receipts" / "kip.db"
        assert f"receipts\n  {kipdb} · not created yet" in kip("doctor")
        old_run = home / ".kip" / "receipts" / "demo" / "20250101-000000-abcd"
        write(old_run / "receipt.json", {"id": old_run.name, "repo": "demo", "branch": "main", "level": "full",
                                         "created_at": "2025-01-01T00:00:00+00:00", "claims": []})
        out = kip("install")
        assert f"receipts\n  imported 1 runs and 0 session starts from the JSON store\n  {kipdb} · schema v2 of 2 · 1 runs" \
            in out, out
        assert f"receipts\n  {kipdb} · schema v2 of 2 · 1 runs" in kip("doctor")
        first = settings.read_text()
        assert "cursor: skipped, missing ~/.cursor" in out, out
        for d in (home / ".kip" / "skills", home / ".claude" / "skills"):
            assert sorted(p.name for p in d.iterdir()) == skills, d
            assert all(p.is_symlink() and ROOT in p.resolve().parents for p in d.iterdir())
        assert (home / ".kip" / "kip").resolve() == ROOT
        claude_md = home / ".claude" / "CLAUDE.md"
        assert claude_md.read_text() == f"@{RULES}\n", claude_md.read_text()
        assert "rules: already imported" in kip("install") and claude_md.read_text() == f"@{RULES}\n"

        # Hooks: Kip's groups are added once, a stale Kip path is replaced, the user's own hooks and keys stay.
        on = json.loads(settings.read_text())
        assert on["model"] == "x" and on["hooks"]["Stop"][0] == mine and len(on["hooks"]["Stop"]) == 2, on
        assert on["hooks"]["Stop"][1]["hooks"][0]["command"] == f"python3 {RECEIPT} check --hook stop", on
        pre = on["hooks"]["PreToolUse"]
        assert [g["matcher"] for g in pre] == ["Bash", "mcp__.*(create_pull_request|create_pr)", "*"], pre
        assert [g["hooks"][0]["command"].split()[-1] for g in pre] == ["pr", "pr", "tool"], pre
        assert on["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("check --hook start"), on
        assert on["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("check --hook prompt"), on
        assert on["hooks"]["SessionEnd"][0]["hooks"][0]["command"].endswith("check --hook end"), on
        assert settings.read_text() == first, "second install changed hooks"
        assert "claude-code    ready · hooks installed" in kip("doctor")
        write(settings, {**on, "hooks": {**on["hooks"], "PreToolUse": pre[:2]}})  # installed before the tool hook
        assert "claude-code    ready · hooks not installed" in kip("doctor")
        kip("install", "--host", "claude-code")
        assert settings.read_text() == first, "install didn't add the missing hook"
        assert "cursor         missing ~/.cursor · hooks not installed (run install)" in kip("doctor")
        settings.write_text("{not json")
        assert "fix it before installing hooks" in kip("install", "--host", "claude-code", ok=False)
        assert settings.read_text() == "{not json"
        write(settings, on)
        # An existing ~-relative import counts as already imported.
        (home / "Kip").symlink_to(ROOT)
        claude_md.write_text("# mine\n@~/Kip/rules/user-rules.md\n")
        before = claude_md.read_text()
        kip("install", "--host", "claude-code")
        assert claude_md.read_text() == before, claude_md.read_text()

        # A link to a skill that moved is pruned; a real dir in the way is left alone.
        stale = home / ".claude" / "skills" / "kip-old"
        stale.symlink_to(ROOT / "skills" / "gone" / "kip-old")
        (home / ".claude" / "skills" / "mine").mkdir()
        kip("install", "--host", "claude-code")
        assert not stale.is_symlink() and (home / ".claude" / "skills" / "mine").is_dir()

        # --host forces an undetected host; --project links into the repo instead.
        out = kip("install", "--host", "cursor")
        assert "rules: Paste" in out and len(list((home / ".cursor" / "skills").iterdir())) == len(skills), out
        # With Cursor's state DB present, rules go into its User Rules value, once, beside the user's own.
        db = home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            con.execute("INSERT INTO ItemTable VALUES ('aicontext.personalContext', 'my own rule')")
        assert "rules: written to" in kip("install", "--host", "cursor")
        assert "rules: already in" in kip("install", "--host", "cursor")
        with sqlite3.connect(db) as con:
            value = con.execute("SELECT value FROM ItemTable WHERE key = 'aicontext.personalContext'").fetchone()[0]
        assert value.startswith("my own rule\n\n<!-- kip rules: ") and value.count("<!-- /kip rules -->") == 1, value
        assert RULES.read_text().strip() in value
        # Cursor hooks: flat entries in hooks.json with the version Cursor expects, beside the user's own.
        cursor_hooks = home / ".cursor" / "hooks.json"
        on = json.loads(cursor_hooks.read_text())
        assert on["version"] == 1 and on["hooks"]["stop"] == [{"command": f"python3 {RECEIPT} check --hook stop"}], on
        assert on["hooks"]["beforeShellExecution"][0]["command"].endswith("check --hook pr"), on
        assert on["hooks"]["beforeMCPExecution"][0]["command"].endswith("check --hook pr"), on
        assert on["hooks"]["afterFileEdit"][0]["command"].endswith("check --hook tool"), on
        assert on["hooks"]["preToolUse"] == [{"command": f"python3 {RECEIPT} check --hook tool"}], on
        assert on["hooks"]["beforeSubmitPrompt"][0]["command"].endswith("check --hook prompt"), on
        assert on["hooks"]["afterAgentResponse"][0]["command"].endswith("check --hook response"), on
        on["hooks"]["stop"].insert(0, {"command": "say hi"})
        write(cursor_hooks, on)
        kip("install", "--host", "cursor")
        assert json.loads(cursor_hooks.read_text())["hooks"]["stop"] == on["hooks"]["stop"], cursor_hooks.read_text()
        assert "cursor         ready · hooks installed · rules current" in kip("doctor")
        assert on["hooks"]["sessionStart"][0]["command"].endswith("check --hook start"), on
        # Cursor holds a copy of the rules, so doctor says when it's behind rules/user-rules.md.
        with sqlite3.connect(db) as con:
            con.execute("UPDATE ItemTable SET value = replace(value, 'Lazy senior developer', 'Old rule')")
        assert "rules stale (run install)" in kip("doctor")
        kip("install", "--host", "cursor")
        assert "rules current" in kip("doctor")
        # The conversation switch: install saves it into the user's layer (the repo's with --project),
        # beside what's already there; no flag leaves it as it was.
        kip("install", "--host", "cursor", "--no-conversation")
        user = json.loads((home / ".kip" / "config.json").read_text())
        assert user["dashboard"] == {"conversation": False} and user["default_destination"] == "linear", user
        kip("install", "--host", "cursor")
        assert json.loads(kip("config"))["dashboard"]["conversation"] is False
        kip("install", "--host", "cursor", "--project", "--conversation")
        assert json.loads(kip("config"))["dashboard"]["conversation"] is True
        assert "not allowed with" in kip("install", "--conversation", "--no-conversation", ok=False)
        kip("install", "--project")
        assert "Stop" in json.loads((repo / ".claude" / "settings.json").read_text())["hooks"]
        assert sorted(p.name for p in (repo / ".claude" / "skills").iterdir()) == skills
        kip("uninstall", "--project")
        assert not (repo / ".claude" / "settings.json").exists() and not list((repo / ".claude" / "skills").iterdir())
        # The dashboard service: KIP_DASHBOARD=0 and --project write none; Linux's restarts it whenever it exits.
        assert not [p for p in home.rglob("*") if "kip.dashboard" in p.name or "kip-dashboard" in p.name]
        sys.path.insert(0, str(ROOT))
        import kip as kip_py
        path, text, load, unload = kip_py.service("linux")
        assert path.name == "kip-dashboard.service" and "Restart=always" in text and "serve --idle 0" in text, text
        assert kip_py.service("win32") is None
        os.environ["KIP_DASHBOARD_PORT"] = "7400"
        assert "--port 7400" in kip_py.service("linux")[1]
        del os.environ["KIP_DASHBOARD_PORT"]
        assert "unknown host nope" in kip("install", "--host", "nope", ok=False)
        # Setup a person still has to do: listed with its command, in doctor too, until skipped.
        out = kip("setup")
        assert "playwright: Playwright for desk tests" in out and "setup.sh" in out, out
        assert "playwright     missing" in kip("doctor") and "→ bash" in kip("doctor")
        assert "skipped playwright" in kip("setup", "--skip", "playwright")
        assert "playwright" not in kip("setup") + kip("doctor").split("setup\n")[-1]
        # Git hooks re-run install; someone else's hook is left alone, and uninstall takes only Kip's.
        hooks = Path(tmp, "hooks")
        hooks.mkdir()
        (hooks / "post-merge").write_text("#!/bin/sh\nmine\n")
        kip_py.install_git_hooks(hooks)
        kip_py.install_git_hooks(hooks)  # idempotent
        commit = (hooks / "post-commit").read_text()
        assert f"{ROOT / 'kip.py'} install" in commit and os.access(hooks / "post-commit", os.X_OK), commit
        assert (hooks / "post-merge").read_text() == "#!/bin/sh\nmine\n"
        kip_py.uninstall_git_hooks(hooks)
        assert [p.name for p in hooks.iterdir()] == ["post-merge"]

        # Uninstall undoes install: the user's own settings, rules, and hooks are as they were,
        # files install created are gone, and nothing links back into Kip.
        home = Path(tmp, "home2")
        db = home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            con.execute("INSERT INTO ItemTable VALUES ('aicontext.personalContext', 'my own rule')")
        write(home / ".claude" / "settings.json", {"model": "x", "hooks": {"Stop": [mine]}})
        (home / ".claude" / "CLAUDE.md").write_text("# mine\n")

        def snapshot():
            with sqlite3.connect(db) as con:
                rules = con.execute("SELECT value FROM ItemTable").fetchall()
            files = [home / ".claude" / "settings.json", home / ".claude" / "CLAUDE.md", home / ".cursor" / "hooks.json"]
            return rules, [f.read_text() if f.exists() else None for f in files]

        before = snapshot()
        kip("install", "--host", "claude-code", "--host", "cursor", HOME=str(home))
        assert snapshot() != before and (home / ".cursor" / "hooks.json").exists()
        out = kip("uninstall", HOME=str(home))
        kept = home / ".kip" / "receipts" / "kip.db"
        assert f"receipts\n  kept: {kept} · schema v2 of 2 · 0 runs" in out and kept.exists(), out
        assert json.loads(snapshot()[1][0]) == json.loads(before[1][0]) and snapshot()[1][1:] == before[1][1:], snapshot()
        assert snapshot()[0] == before[0], snapshot()
        assert not [p for p in home.rglob("*") if p.is_symlink() and os.readlink(p).startswith(str(ROOT))], out
        kip("uninstall", HOME=str(home))  # idempotent
        with sqlite3.connect(db) as con:  # a rules value Kip never wrote to is left byte for byte
            con.execute("UPDATE ItemTable SET value = '  spaced rule  '")
        kip("uninstall", HOME=str(home))
        assert snapshot()[0] == [("  spaced rule  ",)], snapshot()[0]
        assert json.loads(snapshot()[1][0]) == json.loads(before[1][0])
    print("kip.py: all checks passed")


if __name__ == "__main__":
    main()
