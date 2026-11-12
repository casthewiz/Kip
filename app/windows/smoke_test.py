"""Drives kip_app.pyw on a spare port, the way a person would: spawn then attach, a second launch, Quit; and the
tray's mood icon and balloons as sessions start waiting on approval or get blocked at stop.

    python app/windows/smoke_test.py [path/to/kip_app.pyw]

Opens real Edge windows for a few seconds. Exits non-zero on the first failed check, after stopping what it started.
"""
import atexit
import datetime
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PORT = 7391
HERE = Path(__file__).resolve().parent
PYW = Path(sys.argv[1]).resolve() if sys.argv[1:] else HERE / "kip_app.pyw"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
RECEIPT = HERE.parents[1] / "skills/verification/kip-receipts/receipt.py"
HOME = Path(tempfile.mkdtemp(prefix="kip-app-smoke-"))  # its own KIP_HOME: lock, logs and Edge profile stay out of ~/.kip
SPAWN_LOG = HOME / "kip-app-server.log"  # written only when kip-app starts a server
TRAY_LOG = HOME / "kip-app.log"  # pythonw's stderr: kip-app logs each icon change, balloon and balloon click
EVENTS = HOME / "store" / "events"  # its own event log, so only this test's sessions show
os.environ.update(KIP_DASHBOARD_PORT=str(PORT), KIP_HOME=str(HOME), KIP_RECEIPTS_DIR=str(HOME / "store"))
ENV = dict(os.environ)
_spec = importlib.util.spec_from_loader("kip_app", importlib.machinery.SourceFileLoader("kip_app", str(PYW)))
kip_app = importlib.util.module_from_spec(_spec)  # load_module() is gone in 3.15
_spec.loader.exec_module(kip_app)


def procs():
    """pid -> command line of every Python process."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                          "Select-Object ProcessId,CommandLine | ConvertTo-Json"], capture_output=True, text=True).stdout
    rows = json.loads(out or "[]")
    return {r["ProcessId"]: r["CommandLine"] or "" for r in ([rows] if isinstance(rows, dict) else rows)}


def servers():
    return [p for p, c in procs().items() if "receipt.py" in c and f"--port {PORT}" in c]


def trays():
    return [p for p, c in procs().items() if PYW.name in c and "--quit" not in c and "smoke_test" not in c]


def cleanup():
    for hwnd in kip_app.App.windows():
        kip_app.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    for pid in trays() + servers():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def until(cond, secs=30):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.3)
    return False


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        sys.exit(1)


def event(session, hook, **kw):
    """Append a hook event, as Kip's hooks would."""
    EVENTS.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    with open(EVENTS / f"{now.date()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "session": session, "hook": hook, **kw}) + "\n")


def logged(what):
    """kip-app.log lines (without their time) that start with `what`."""
    lines = TRAY_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if TRAY_LOG.exists() else []
    return [l.split(" ", 1)[1] for l in lines if l.split(" ", 1)[1:2] and l.split(" ", 1)[1].startswith(what)]


def timed(cond, secs):
    """Seconds until cond() holds, or None after `secs`."""
    start = time.time()
    while time.time() - start < secs:
        if cond():
            return time.time() - start
        time.sleep(0.05)
    return None


def launch(*args):
    return subprocess.Popen([str(PYTHONW), str(PYW), *args], env=ENV)


print("-- transitions, without a tray")
st = lambda mood="waiting", approval=(), blocked=(): {"mood": mood, "active": 3, "approval": list(approval), "blocked": list(blocked)}
seen, new = kip_app.news(None, st(approval=["a"], blocked=["b"]))
check(new == [] and seen == {("approval", "a"), ("blocked", "b")}, "the first answer notifies nothing and seeds what's waiting")
seen, new = kip_app.news(seen, st(approval=["a", "c"], blocked=["b", "a"]))
check(new == [("approval", "c"), ("blocked", "a")], f"only pairs new since the last poll, in order (a went on to be blocked): {new}")
check(kip_app.news(seen, st(approval=["a", "c"], blocked=["b", "a"]))[1] == [], "the same answer again notifies nothing")
seen, _ = kip_app.news(seen, st())
check(kip_app.news(seen, st(approval=["c"]))[1] == [("approval", "c")], "a session that waits again is new again")
check(kip_app.news(seen, None) == (set(), []) and kip_app.news(None, {})[1] == [], "no answer or an empty one notifies nothing")
check(kip_app.notice([("blocked", "abcdef0123"), ("approval", "x")]) ==
      ("A session is blocked at stop", "abcdef01 was stopped by Kip's gate: its claims need evidence. (+1 more)", "abcdef0123"),
      "one balloon per poll: the first pair, a count of the rest, and the session a click opens")
check([kip_app.icon(st(m)) for m in kip_app.MOODS] == [f"kip-{m}.ico" for m in kip_app.MOODS]
      and kip_app.icon(None) == kip_app.icon(st("sleepy")) == "kip-waiting.ico", "each mood has its icon; unknown ones wait")
check(all((HERE / f"kip-{m}.ico").read_bytes()[:4] == b"\0\0\1\0" for m in kip_app.MOODS), "every mood's .ico is committed")

check(not kip_app.answers() and not trays(), f"nothing on port {PORT} and no kip-app running")
atexit.register(cleanup)  # only now: before, a kip-app running is the user's, not this test's

print("-- spawn: no server answering")
event("pre", "ask", tool="Bash")  # already waiting on approval when kip-app starts
tray = launch()
check(until(kip_app.answers), "kip-app started a dashboard that answers /api/sessions")
spawned = servers()
check(len(spawned) == 1 and SPAWN_LOG.exists(), f"kip-app spawned exactly one receipt.py serve --port {PORT}: {spawned}")
check(until(lambda: kip_app.App.windows()), "an Edge app window titled Kip is open")
second = launch()
check(until(lambda: second.poll() == 0, 15) and trays() == [tray.pid], "a second launch exits and leaves the first one running")

print("-- mood and notifications")
check(until(lambda: logged("icon kip-waiting.ico True")), "the tray shows kip-waiting (a session waiting on approval)")
time.sleep(1.5)
check(not logged("notify"), "no balloon at startup for a session that was already waiting")
event("late", "stop", blocked=True)
took = timed(lambda: logged("icon kip-failure.ico True") and logged("notify blocked:late True"), 5)
check(took is not None and took <= 2, f"blocked at stop: icon kip-failure and one balloon within 2s ({took and round(took, 2)}s)")
event("late2", "ask", tool="Bash")
took = timed(lambda: logged("notify approval:late2 True"), 5)
check(took is not None and took <= 2, f"waiting on approval: one balloon within 2s ({took and round(took, 2)}s)")
time.sleep(2.5)
check(len(logged("notify")) == 2 and logged("icon")[-1] == "icon kip-failure.ico True",
      f"one balloon per transition, none repeated: {logged('notify')}")
before = set(kip_app.App.windows())
kip_app.user32.PostMessageW(kip_app.user32.FindWindowW(kip_app.CLASS, None), kip_app.TRAY, 0, 0x405)  # NIN_BALLOONUSERCLICK
check(until(lambda: logged("open late2")), "clicking the balloon opens its session, late2")
check(until(lambda: (now := set(kip_app.App.windows())) and not now & before, 15),
      "the session's page is a new app window and the old one closed")
for hwnd in kip_app.App.windows():
    kip_app.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, as the window's X button
check(until(lambda: not kip_app.App.windows(), 10), "the app window closed")
time.sleep(1)
check(tray.poll() is None and servers() == spawned and kip_app.answers(), "closing the window leaves kip-app and its server running")
launch().wait(15)
check(until(lambda: kip_app.App.windows()), "a later launch opens the window again")
launch("--quit").wait(15)
check(until(lambda: tray.poll() is not None), "Quit exits kip-app")
check(until(lambda: not servers()) and not kip_app.answers(), "Quit stopped the server kip-app spawned")
check(until(lambda: not kip_app.App.windows(), 10), "Quit closed the app window")

print("-- attach: a server already answering")
own = subprocess.Popen([sys.executable, str(RECEIPT), "serve", "--idle", "0", "--port", str(PORT)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    check(until(kip_app.answers), "a server started outside kip-app answers")
    SPAWN_LOG.unlink()
    tray = launch()
    check(until(lambda: kip_app.App.windows()), "an Edge app window titled Kip is open")
    check(servers() == [own.pid] and not SPAWN_LOG.exists(), "kip-app attached: it started no server of its own")
    launch("--quit").wait(15)
    check(until(lambda: tray.poll() is not None), "Quit exits kip-app")
    time.sleep(1)
    check(own.poll() is None and kip_app.answers(), "Quit left the attached server running")
finally:
    own.terminate()
print("all passed")
