#!/usr/bin/env python3
"""Desk-test a site with a Playwright spec, once per viewport, each viewport
recorded as its own receipts evidence with screenshots (plus video and trace
at ultra).

  desktest.py CLAIM URL SPEC [--red] [--viewports NAME,...] [--run DIR]

Viewports come from Kip's config (evidence.frontend.viewports, see kip.py
config): mobile, tablet and desktop unless a user or project config changes
them. Each one is a Playwright `use` block.

URL is whatever is under test: a local dev server, a preview deploy, staging,
prod. It doesn't have to be work this session did.

Playwright runs from ~/.kip on the Node version pinned in .nvmrc (via nvm),
never the machine's default Node; run setup.sh once. Overrides: $KIP_NODE
(a node binary, for machines without nvm), $KIP_PLAYWRIGHT (a playwright
executable to run as-is, used by the self-check).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "kip.py").is_file())))
import kip  # noqa: E402

HERE = Path(__file__).resolve().parent
RECEIPT = HERE.parent / "receipt.py"
CONFIG = HERE / "kip.config.mjs"
KIP_HOME = Path(os.environ.get("KIP_HOME", Path.home() / ".kip"))
PW_CLI = KIP_HOME / "node_modules" / "@playwright" / "test" / "cli.js"
NODE_VERSION = (HERE / ".nvmrc").read_text().strip()
SETUP = f"run `bash {HERE / 'setup.sh'}` once"


def pinned_node():
    """Absolute path of the .nvmrc Node, so the recorded command names the exact version."""
    if os.environ.get("KIP_NODE"):
        return os.environ["KIP_NODE"]
    nvm = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm")) / "nvm.sh"
    if not nvm.is_file():
        sys.exit(f"nvm not found at {nvm}; install nvm or set KIP_NODE to a Node {NODE_VERSION} binary")
    r = subprocess.run(["bash", "-c", '. "$0" >/dev/null && nvm which "$1"', str(nvm), NODE_VERSION],
                       capture_output=True, text=True)
    node = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else ""
    if not Path(node).is_file():
        sys.exit(f"Node {NODE_VERSION} isn't installed in nvm; {SETUP}")
    return node


def playwright():
    """The argv prefix that runs Playwright."""
    if os.environ.get("KIP_PLAYWRIGHT"):
        return [os.environ["KIP_PLAYWRIGHT"]]
    if not PW_CLI.is_file():
        sys.exit(f"Playwright not found at {PW_CLI}; {SETUP}")
    return [pinned_node(), str(PW_CLI)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("claim")
    p.add_argument("url")
    p.add_argument("spec")
    p.add_argument("--red", action="store_true", help="run against a baseline that lacks the behavior; must fail")
    p.add_argument("--viewports", help="comma-separated; default: every configured viewport")
    p.add_argument("--run")
    a = p.parse_args()

    configured = kip.load()["evidence"].get("frontend", {}).get("viewports", {})
    viewports = a.viewports.split(",") if a.viewports else list(configured)
    unknown = set(viewports) - set(configured)
    if unknown or not viewports:
        sys.exit(f"unknown viewport {', '.join(sorted(unknown))}; available: {', '.join(configured) or 'none'}")
    pw = playwright()
    spec = Path(a.spec).resolve()
    if not spec.is_file():
        sys.exit(f"no such spec: {spec}")

    run_args = ["--run", a.run] if a.run else []
    run = Path(subprocess.run([sys.executable, str(RECEIPT), "path", *run_args],
                              capture_output=True, text=True, check=True).stdout.strip())
    rec = json.loads((run / "receipt.json").read_text())
    claim = next((c for c in rec["claims"] if c["id"] == a.claim), None)
    if not claim:
        sys.exit(f"unknown claim {a.claim}")
    level = claim.get("level") or rec["level"]

    failed = False
    for vp in viewports:
        with tempfile.TemporaryDirectory(prefix=f"kip-{vp}-") as out:
            # Every input is spelled out in the recorded command, so the evidence reruns exactly.
            # NODE_PATH lets specs anywhere (e.g. a custom $KIP_RECEIPTS_DIR) import Kip's @playwright/test.
            vp_json = json.dumps({vp: configured[vp]}, separators=(",", ":"))
            cmd = ["env", f"BASE_URL={a.url}", f"KIP_VIEWPORTS={vp_json}", f"KIP_LEVEL={level}",
                   f"KIP_SPECS={spec.parent}", f"KIP_OUT={out}", f"NODE_PATH={KIP_HOME / 'node_modules'}",
                   *pw, "test", str(spec), "--config", str(CONFIG), f"--project={vp}"]
            r = subprocess.run([sys.executable, str(RECEIPT), "run", a.claim, *run_args,
                                *(["--red"] if a.red else []), "--collect", out,
                                "--label", f"desktest {vp} {a.url}", "--", *cmd],
                               capture_output=True, text=True)
            line = (r.stdout + r.stderr).strip()
            print(f"{vp:8} {line}")
            failed |= r.returncode != 0 or "-> pass" not in line
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
