#!/usr/bin/env python3
"""Self-check for kip.py (config layers, doctor, install): python3 test_kip.py"""
import json
import os
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
        env = {k: v for k, v in os.environ.items() if k not in ("KIP_HOME", "LINEAR_API_KEY")}
        env["HOME"] = str(home)

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
                         ({"default_destination": "nope"}, "default_destination nope isn't a destination")]:
            write(repo / ".kip" / "config.json", bad)
            assert msg in kip("doctor", ok=False), msg
        (repo / ".kip" / "config.json").unlink()

        # Install: detected hosts only, every skill linked, rules imported once, idempotent.
        skills = sorted(p.parent.name for p in (ROOT / "skills").rglob("SKILL.md"))
        settings = home / ".claude" / "settings.json"
        mine = {"hooks": [{"type": "command", "command": "say done"}]}
        stale = {"hooks": [{"type": "command", "command": "python3 /old/skills/verification/kip-receipts/receipt.py check --hook stop"}]}
        write(settings, {"model": "x", "hooks": {"Stop": [mine, stale]}})
        assert "claude-code    ready · hooks not installed" in kip("doctor")
        out = kip("install")
        first = settings.read_text()
        assert "cursor: skipped, missing ~/.cursor" in out, out
        for d in (home / ".kip" / "skills", home / ".claude" / "skills"):
            assert sorted(p.name for p in d.iterdir()) == skills, d
            assert all(p.is_symlink() and p.resolve().parent.parent.parent == ROOT for p in d.iterdir())
        assert (home / ".kip" / "kip").resolve() == ROOT
        claude_md = home / ".claude" / "CLAUDE.md"
        assert claude_md.read_text() == f"@{RULES}\n", claude_md.read_text()
        assert "rules: already imported" in kip("install") and claude_md.read_text() == f"@{RULES}\n"

        # Hooks: Kip's groups are added once, a stale Kip path is replaced, the user's own hooks and keys stay.
        on = json.loads(settings.read_text())
        assert on["model"] == "x" and on["hooks"]["Stop"][0] == mine and len(on["hooks"]["Stop"]) == 2, on
        assert on["hooks"]["Stop"][1]["hooks"][0]["command"] == f"python3 {RECEIPT} check --hook stop", on
        pre = on["hooks"]["PreToolUse"]
        assert len(pre) == 1 and pre[0]["matcher"] == "Bash" and pre[0]["hooks"][0]["command"].endswith("--hook pr"), pre
        assert settings.read_text() == first, "second install changed hooks"
        assert "claude-code    ready · hooks installed" in kip("doctor")
        assert "cursor         missing ~/.cursor · rules only, no hooks" in kip("doctor")
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
        kip("install", "--project")
        assert "Stop" in json.loads((repo / ".claude" / "settings.json").read_text())["hooks"]
        assert sorted(p.name for p in (repo / ".claude" / "skills").iterdir()) == skills
        assert "unknown host nope" in kip("install", "--host", "nope", ok=False)
    print("kip.py: all checks passed")


if __name__ == "__main__":
    main()
