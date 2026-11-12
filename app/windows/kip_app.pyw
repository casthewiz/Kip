"""kip-app on Windows: a tray icon that keeps Kip's dashboard up and opens it in an Edge app window.

Smart App Control blocks unsigned binaries built on the machine, so Windows gets no compiled shell; this runs
under pythonw.exe and Edge, both signed. Attaches to the `receipt.py serve` answering on the dashboard port, or
starts one and stops it again on Quit. Polls its /api/status each second: the tray icon is Kip's mood, and a
session newly waiting on approval or blocked at stop raises a balloon that opens its page. Stdlib only.
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import winreg
from ctypes import wintypes as w
from pathlib import Path

PORT = int(os.environ.get("KIP_DASHBOARD_PORT", 7377))
URL = f"http://127.0.0.1:{PORT}/"
HOME = Path(os.environ.get("KIP_HOME", Path.home() / ".kip"))
HERE = Path(__file__).resolve().parent
OWNERS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")  # a server with these is that session's to stop
CLASS, TRAY, OPEN, QUIT = "KipAppTray", 0x8001, 0x8002, 0x8003  # WM_APP+1 tray callback; +2/+3 from a later launch
MENU_OPEN, MENU_QUIT = 1, 2
MOODS = ("failure", "asking", "working", "success", "waiting")  # receipt.py's MOOD_RANK; kip-<mood>.ico each
NOTICE = {"approval": ("A session is waiting on you", "needs your approval or an answer."),
          "blocked": ("A session is blocked at stop", "was stopped by Kip's gate: its claims need evidence.")}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
ENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user32.DefWindowProcW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
user32.FindWindowW.restype = w.HWND
user32.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.LoadImageW.restype = w.HANDLE
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = (w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
user32.TrackPopupMenu.argtypes = (w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID)
user32.DestroyMenu.argtypes = (w.HMENU,)
user32.EnumWindows.argtypes = (ENUMPROC, w.LPARAM)
kernel32.CreateMutexW.restype = w.HANDLE
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.OpenProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
kernel32.CloseHandle.argtypes = (w.HANDLE,)
user32.GetClassNameW.argtypes = user32.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = user32.SetForegroundWindow.argtypes = (w.HWND,)
user32.ShowWindow.argtypes = (w.HWND, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT), ("uCallbackMessage", w.UINT),
                ("hIcon", w.HICON), ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]


def get(path):
    """GET a dashboard path, past any proxy; the response, else None."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(URL + path, timeout=5) as r:
            return r.read() if r.status == 200 else None
    except OSError:
        return None


def answers():
    """The dashboard answers /api/sessions with a 200 (a bare open port could be anything)."""
    return get("api/sessions") is not None


def status():
    try:
        return json.loads(get("api/status") or "null")
    except ValueError:
        return None


def icon(st):
    """The mood icon's name for a /api/status answer (waiting when there's none, or a mood this build doesn't know)."""
    return "kip-%s.ico" % (st["mood"] if st and st.get("mood") in MOODS else "waiting")


def news(seen, st):
    """(seen, new): the (kind, session) pairs now waiting on approval or blocked, and those not in `seen` before,
    in /api/status order. A session that stops waiting and waits again is new again. With `seen` None (the first
    answer since launch) nothing is new: those sessions were waiting before the app was, and the icon shows them."""
    now = [(k, s) for k in NOTICE for s in (st or {}).get(k, [])]
    return set(now), [] if seen is None else [p for p in now if p not in seen]


def notice(new):
    """(title, text, session) of the one balloon for a poll's new pairs: the first, plus a count of the rest."""
    kind, session = new[0]
    title, text = NOTICE[kind]
    more = f" (+{len(new) - 1} more)" if len(new) > 1 else ""
    return title, f"{session[:8]} {text}{more}", session


class App:
    def __init__(self):
        self.server = None  # the server this app started; never one it attached to
        self.lock = threading.Lock()

    def spawn_server(self):
        """Start a dashboard and wait until it answers; returns an error message or None."""
        installed = HOME / "dashboard"
        script = (installed if installed.is_dir() else HERE.parents[1]) / "skills/verification/kip-receipts/receipt.py"
        log = HOME / "kip-app-server.log"
        with open(log, "w") as err:
            self.server = subprocess.Popen(
                [os.environ.get("KIP_PYTHON", sys.executable), str(script), "serve", "--idle", "0", "--port", str(PORT)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                env={k: v for k, v in os.environ.items() if k not in OWNERS})
        start = time.time()
        while time.time() - start < 30:
            if answers():
                return None
            if self.server.poll() is not None:
                self.server = None
                return f"the dashboard server exited; see {log}"
            time.sleep(0.2)
        self.stop_server()
        return f"the dashboard server didn't answer on port {PORT}; see {log}"

    def stop_server(self):
        if self.server:
            self.server.terminate()
            self.server.wait(10)
            self.server = None

    @staticmethod
    def windows():
        """Edge app windows on the dashboard: titled exactly "Kip" (a browser tab's window adds " - Microsoft Edge").
        Matched by window, not by the Edge pid launched: a relaunch hands its window to the Edge already running."""
        found, text, image = [], ctypes.create_unicode_buffer(260), ctypes.create_unicode_buffer(260)

        def each(hwnd, _):
            user32.GetClassNameW(hwnd, text, 260)
            if text.value != "Chrome_WidgetWin_1" or not user32.IsWindowVisible(hwnd):
                return True
            user32.GetWindowTextW(hwnd, text, 260)
            if text.value != "Kip":
                return True
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if h := kernel32.OpenProcess(0x1000, False, pid.value):  # PROCESS_QUERY_LIMITED_INFORMATION
                size = w.DWORD(260)
                if kernel32.QueryFullProcessImageNameW(h, 0, image, ctypes.byref(size)) and image.value.lower().endswith("msedge.exe"):
                    found.append(hwnd)
                kernel32.CloseHandle(h)
            return True

        user32.EnumWindows(ENUMPROC(each), 0)
        return found

    def open(self, session=None):
        """Show the app window, or with `session`, that session's page in it."""
        with self.lock:  # one spawn at a time, however many clicks
            if not answers() and (err := self.spawn_server()):  # attach to one already answering
                user32.MessageBoxW(None, f"Kip's dashboard didn't start: {err}", "Kip", 0x10)
                return
            old = self.windows()
            if old and not session:
                user32.ShowWindow(old[0], 9)  # SW_RESTORE
                user32.SetForegroundWindow(old[0])
                return
            # The page reads its #session= only on load, and Edge can't be told to navigate a window it has, so a
            # session's page is a new app window. The old one closes only once the new one is up: closed first, it
            # can be Edge's last window, and an Edge shutting down drops the launch handed to it.
            url = URL + "#session=" + urllib.parse.quote(session, safe="") if session else URL
            subprocess.Popen([edge(), f"--app={url}", f"--user-data-dir={HOME / 'edge-app'}", "--no-first-run",
                              "--no-default-browser-check"])
            end = time.time() + 15
            while old and time.time() < end and not set(self.windows()) - set(old):
                time.sleep(0.2)
            for hwnd in old:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    def quit(self):
        for hwnd in self.windows():
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self.stop_server()


def edge():
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            return winreg.QueryValue(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
        except OSError:
            pass
    return "msedge.exe"


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    if sys.stderr is None:  # pythonw: keep tracebacks somewhere
        sys.stderr = open(HOME / "kip-app.log", "a", buffering=1)
    quitting = sys.argv[1:] == ["--quit"]  # `kip_app.pyw --quit`: the running one quits, as from its tray menu
    kernel32.CreateMutexW(None, False, "Local\\kip-app")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: hand over to the running one
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: this launch's right to the foreground passes to it
        user32.PostMessageW(user32.FindWindowW(CLASS, None), QUIT if quitting else OPEN, 0, 0)
        return
    if quitting:
        return

    app = App()
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    icons = {f"kip-{m}.ico": user32.LoadImageW(None, str(HERE / f"kip-{m}.ico"), 1, 0, 0, 0x50) for m in MOODS}  # IMAGE_ICON; LOADFROMFILE|DEFAULTSIZE
    nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), uID=1, uFlags=0x7, uCallbackMessage=TRAY, szTip="Kip",
                          hIcon=icons[icon(None)])
    clicked = [None]  # the session the latest balloon is about

    def in_background(fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def log(*what):  # the smoke test reads these from kip-app.log
        print(time.strftime("%H:%M:%S"), *what, file=sys.stderr, flush=True)

    def tray(flags, **fields):
        """NIM_MODIFY the icon with `fields`; whether the shell took it."""
        data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=nid.hWnd, uID=1, uFlags=flags, **fields)
        return bool(shell32.Shell_NotifyIconW(1, ctypes.byref(data)))

    def poll():
        """Each second: the icon follows Kip's mood, and newly waiting or blocked sessions raise one balloon."""
        seen, shown = None, None
        while True:
            if (st := status()) is not None:
                name = icon(st)
                tip = (f"Kip: {name[4:-4]}" + "".join(f" · {len(st.get(k, []))} {k}" for k in NOTICE if st.get(k)))[:127]
                if (name, tip) != shown:
                    nid.hIcon, nid.szTip = icons[name], tip  # kept for re-adding after Explorer restarts
                    log("icon", name, tray(0x6, hIcon=icons[name], szTip=tip))  # NIF_ICON | NIF_TIP
                    shown = (name, tip)
                seen, new = news(seen, st)
                if new:
                    title, text, clicked[0] = notice(new)
                    kind = icons["kip-failure.ico" if new[0][0] == "blocked" else "kip-asking.ico"]
                    ok = tray(0x10, szInfoTitle=title, szInfo=text, dwInfoFlags=0x24, hBalloonIcon=kind)  # NIF_INFO; NIIF_USER|LARGE_ICON
                    log("notify", *(f"{k}:{s}" for k, s in new), ok)
            time.sleep(1)

    def menu(hwnd):
        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, 0, MENU_OPEN, "Open dashboard")
        user32.AppendMenuW(m, 0, MENU_QUIT, "Quit")
        pt = w.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)  # or the menu won't close when clicking away
        cmd = user32.TrackPopupMenu(m, 0x180, pt.x, pt.y, 0, hwnd, None)  # TPM_RETURNCMD | TPM_NONOTIFY
        user32.DestroyMenu(m)
        if cmd == MENU_OPEN:
            in_background(app.open)
        elif cmd == MENU_QUIT:
            quit()

    def quit():
        app.quit()
        shell32.Shell_NotifyIconW(2, ctypes.byref(nid))  # NIM_DELETE
        user32.PostQuitMessage(0)

    @WNDPROC
    def proc(hwnd, msg, wp, lp):
        if msg == TRAY and lp in (0x202, 0x203):  # WM_LBUTTONUP, WM_LBUTTONDBLCLK
            in_background(app.open)
        elif msg == TRAY and lp == 0x205:  # WM_RBUTTONUP
            menu(hwnd)
        elif msg == TRAY and lp == 0x405 and clicked[0]:  # NIN_BALLOONUSERCLICK
            log("open", clicked[0])
            in_background(app.open, clicked[0])
        elif msg == OPEN:
            in_background(app.open)
        elif msg == QUIT:
            quit()
        elif msg == taskbar_created:  # Explorer restarted and dropped every tray icon
            shell32.Shell_NotifyIconW(0, ctypes.byref(nid))
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    hinst = kernel32.GetModuleHandleW(None)
    user32.RegisterClassW(ctypes.byref(WNDCLASSW(lpfnWndProc=proc, hInstance=hinst, lpszClassName=CLASS)))
    nid.hWnd = user32.CreateWindowExW(0, CLASS, "Kip", 0, 0, 0, 0, 0, None, None, hinst, None)  # hidden: FindWindow sees it
    shell32.Shell_NotifyIconW(0, ctypes.byref(nid))  # NIM_ADD
    in_background(app.open)
    in_background(poll)
    msg = w.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
