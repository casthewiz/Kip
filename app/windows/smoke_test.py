"""Drives kip_app.pyw on a spare port, the way a person would: spawn then attach, a second launch, Quit.

    python app/windows/smoke_test.py [path/to/kip_app.pyw]

Opens real Edge windows for a few seconds. Exits non-zero on the first failed check, after stopping what it started.
"""
import atexit
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
os.environ.update(KIP_DASHBOARD_PORT=str(PORT), KIP_HOME=str(HOME))
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


def launch(*args):
    return subprocess.Popen([str(PYTHONW), str(PYW), *args], env=ENV)


check(not kip_app.answers() and not trays(), f"nothing on port {PORT} and no kip-app running")
atexit.register(cleanup)  # only now: before, a kip-app running is the user's, not this test's

print("-- spawn: no server answering")
tray = launch()
check(until(kip_app.answers), "kip-app started a dashboard that answers /api/sessions")
spawned = servers()
check(len(spawned) == 1 and SPAWN_LOG.exists(), f"kip-app spawned exactly one receipt.py serve --port {PORT}: {spawned}")
check(until(lambda: kip_app.App.windows()), "an Edge app window titled Kip is open")
second = launch()
check(until(lambda: second.poll() == 0, 15) and trays() == [tray.pid], "a second launch exits and leaves the first one running")
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
