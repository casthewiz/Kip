//! kip-app: a window and tray icon around Kip's dashboard. Attaches to the `receipt.py serve` already listening on
//! the dashboard port, or starts one and stops it again on Quit. The page is the server's own http://127.0.0.1
//! URL, not a custom protocol: the server refuses any other Host. Polls its /api/status each second: the tray icon
//! is Kip's mood, and a session newly waiting on approval or blocked at stop raises a notification. macOS and Linux only.
#[cfg(windows)]
compile_error!("kip-app's Windows shell is app/windows/kip_app.pyw: Smart App Control blocks binaries built locally");

use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use std::{env, thread};

use tao::event::{Event, StartCause, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use tray_icon::menu::{Menu, MenuEvent, MenuItem};
use tray_icon::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use wry::WebViewBuilder;

/// receipt.py's MOOD_RANK, each with its mascot; the first frame of each is a tray icon.
const MOODS: [(&str, &str); 5] = [
    ("failure", include_str!("../../assets/mascot/kip-failure.svg")),
    ("asking", include_str!("../../assets/mascot/kip-asking.svg")),
    ("working", include_str!("../../assets/mascot/kip-working.svg")),
    ("success", include_str!("../../assets/mascot/kip-success.svg")),
    ("waiting", include_str!("../../assets/mascot/kip-waiting.svg")),
];
const NOTICE: [(&str, &str, &str); 2] = [
    ("approval", "A session is waiting on you", "needs your approval or an answer."),
    ("blocked", "A session is blocked at stop", "was stopped by Kip's gate: its claims need evidence."),
];
const OWNERS: [&str; 2] = ["CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"]; // a server with these is that session's to stop

enum UserEvent {
    Show,
    Quit,
    Ready(Option<Child>, Result<(), String>),
    Status(Status),
    Open(String),
}

/// What the tray shows of /api/status: Kip's mood and the (kind, session) pairs waiting on approval or blocked.
#[derive(Debug, PartialEq)]
struct Status {
    mood: String,
    waiting: Vec<(String, String)>,
}

fn kip_home() -> PathBuf {
    env::var_os("KIP_HOME").map(PathBuf::from).unwrap_or_else(|| env::home_dir().unwrap_or_default().join(".kip"))
}

/// The body of a 200 from the dashboard, else None.
fn get(port: u16, path: &str) -> Option<String> {
    let mut s = TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_millis(500)).ok()?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    write!(s, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n").ok()?;
    let mut out = String::new();
    s.read_to_string(&mut out).ok()?;
    let (head, body) = out.split_once("\r\n\r\n")?;
    (head.starts_with("HTTP/1.") && head.get(9..12) == Some("200")).then(|| body.to_string())
}

/// The dashboard answers /api/sessions with a 200 (a bare open port could be anything).
fn answers(port: u16) -> bool {
    get(port, "/api/sessions").is_some()
}

/// /api/status's `{"mood": "..", "active": n, "approval": [ids], "blocked": [ids]}`, as receipt.py's json.dumps
/// writes it. messy-bun: a string scan, not a JSON parser (no serde in the lock); ids are uuids, without quotes.
fn parse_status(body: &str) -> Option<Status> {
    let after = |key: &str| body.split_once(&format!("\"{key}\": ")).map(|(_, rest)| rest);
    let mood = after("mood")?.strip_prefix('"')?.split('"').next()?.to_string();
    let mut waiting = vec![];
    for (kind, ..) in NOTICE {
        let list = after(kind)?.strip_prefix('[')?.split(']').next()?;
        waiting.extend(list.split('"').skip(1).step_by(2).map(|id| (kind.to_string(), id.to_string())));
    }
    Some(Status { mood, waiting })
}

/// The pairs in `now` that weren't in `seen`, in order; then `seen` becomes `now`. With `seen` None (the first answer
/// since launch) nothing is new: those sessions were waiting before the app was, and the icon shows them.
fn news(seen: &mut Option<HashSet<(String, String)>>, now: &[(String, String)]) -> Vec<(String, String)> {
    let new = seen.as_ref().map_or(vec![], |s| now.iter().filter(|p| !s.contains(*p)).cloned().collect());
    *seen = Some(now.iter().cloned().collect());
    new
}

#[test]
fn transitions() {
    let st = parse_status(r#"{"mood": "failure", "active": 3, "approval": ["a", "c"], "blocked": ["b"]}"#).unwrap();
    let pair = |k: &str, s: &str| (k.to_string(), s.to_string());
    let want = vec![pair("approval", "a"), pair("approval", "c"), pair("blocked", "b")];
    assert_eq!(st, Status { mood: "failure".into(), waiting: want });
    assert!(parse_status(r#"{"mood": "waiting", "active": 0, "approval": [], "blocked": []}"#).unwrap().waiting.is_empty());
    let mut seen = None;
    assert!(news(&mut seen, &st.waiting[..1]).is_empty()); // startup: nothing new
    assert_eq!(news(&mut seen, &st.waiting), &st.waiting[1..]);
    assert!(news(&mut seen, &st.waiting).is_empty());
    news(&mut seen, &[]);
    assert_eq!(news(&mut seen, &st.waiting[..1]), &st.waiting[..1]); // waiting again is new again
}

/// Tray icon for a mood: its mascot's first frame, or waiting's for a mood this build doesn't know.
fn mood_icon(mood: &str) -> Vec<u8> {
    mascot_rgba(MOODS.iter().find(|(m, _)| *m == mood).unwrap_or(&MOODS[4]).1)
}

/// One OS notification, off the event loop; a click on it sends Open(session) where the platform reports clicks.
fn notify(proxy: tao::event_loop::EventLoopProxy<UserEvent>, title: &str, text: &str, session: String) {
    let (title, text) = (title.to_string(), text.to_string());
    thread::spawn(move || {
        if cfg!(target_os = "macos") {
            // messy-bun: osascript can't report a click, so the tray's next click opens the session (UserEvent::Show);
            // a click-through needs a bundled app on UNUserNotificationCenter.
            let q = |s: &str| s.replace('\\', "\\\\").replace('"', "\\\"");
            let script = format!("display notification \"{}\" with title \"Kip\" subtitle \"{}\"", q(&text), q(&title));
            let _ = Command::new("osascript").args(["-e", &script]).status();
            return;
        }
        // libnotify 0.7.10+ prints the action clicked; an older notify-send refuses --action, so it's sent plain.
        match Command::new("notify-send").args(["-a", "Kip", "--action=default=Open", "--wait", &title, &text]).output() {
            Ok(o) if o.status.success() => {
                if String::from_utf8_lossy(&o.stdout).trim() == "default" {
                    let _ = proxy.send_event(UserEvent::Open(session));
                }
            }
            _ => drop(Command::new("notify-send").args(["-a", "Kip", &title, &text]).status()),
        }
    });
}

/// KIP_PYTHON, else the first of python3/python that runs.
fn python() -> Option<String> {
    if let Ok(p) = env::var("KIP_PYTHON") {
        return Some(p);
    }
    ["python3", "python"].into_iter().map(String::from).find(|p| {
        Command::new(p).args(["-c", "import sys"]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
    })
}

/// Attach to a running dashboard, or start one; the child is returned only when this app started it.
fn connect(port: u16) -> (Option<Child>, Result<(), String>) {
    if answers(port) {
        return (None, Ok(()));
    }
    let installed = kip_home().join("dashboard");
    let root = if installed.is_dir() { installed } else { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..") };
    let script = root.join("skills/verification/kip-receipts/receipt.py");
    let Some(py) = python() else { return (None, Err("no Python found: set KIP_PYTHON".into())) };
    let log = kip_home().join("kip-app-server.log");
    let mut cmd = Command::new(&py);
    cmd.arg(&script).args(["serve", "--idle", "0", "--port", &port.to_string()]).stdin(Stdio::null()).stdout(Stdio::null());
    cmd.stderr(File::create(&log).map(Stdio::from).unwrap_or_else(|_| Stdio::null()));
    for k in OWNERS {
        cmd.env_remove(k);
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (None, Err(format!("couldn't run {py} {}: {e}", script.display()))),
    };
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(30) {
        if answers(port) {
            return (Some(child), Ok(()));
        }
        if let Ok(Some(code)) = child.try_wait() {
            return (None, Err(format!("the dashboard server exited ({code}); see {}", log.display())));
        }
        thread::sleep(Duration::from_millis(200));
    }
    let _ = child.kill();
    (None, Err(format!("the dashboard server didn't answer on port {port}; see {}", log.display())))
}

/// The first frame of a mascot SVG (32x32 pixel-art rects, frames side by side) as RGBA.
fn mascot_rgba(svg: &str) -> Vec<u8> {
    let mut px = vec![0u8; 32 * 32 * 4];
    for rect in svg.split("<rect").skip(1) {
        let rect = rect.split('>').next().unwrap_or(""); // starts with a space, so " x" can't match inside "width"
        let attr = |name: &str| {
            let at = rect.find(&format!("{name}=\"")).map(|i| i + name.len() + 2)?;
            Some(&rect[at..at + rect[at..].find('"')?])
        };
        let num = |name| attr(name).and_then(|v: &str| v.parse::<usize>().ok());
        let (Some(x), Some(y), Some(w), Some(h), Some(fill)) = (num(" x"), num(" y"), num("width"), num("height"), attr("fill"))
        else {
            continue;
        };
        let Ok(rgb) = u32::from_str_radix(fill.trim_start_matches('#'), 16) else { continue };
        for (cx, cy) in (x..(x + w).min(32)).flat_map(|cx| (y..(y + h).min(32)).map(move |cy| (cx, cy))) {
            px[(cy * 32 + cx) * 4..][..4].copy_from_slice(&[(rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8, 255]);
        }
    }
    px
}

#[test]
fn mascot_first_frame() {
    let px = mood_icon("waiting");
    assert_eq!(&px[(3 * 32 + 3) * 4..][..4], &[0x9a, 0xa3, 0xb5, 255]); // kip-waiting.svg's first rect, x=3 y=3
    assert_eq!(px[3], 0); // (0,0) is transparent
}

fn page(body: &str) -> String {
    format!("<body style='font:16px system-ui;background:#1d1b2e;color:#f4f7ff;display:grid;place-items:center;height:90vh'>{body}</body>")
}

fn main() {
    let port: u16 = env::var("KIP_DASHBOARD_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(7377);
    let url = format!("http://127.0.0.1:{port}/");
    let home = kip_home();
    let _ = fs::create_dir_all(&home);

    // Single instance: the lock holder listens on a local port; a second launch pokes it to show its window.
    let lock = OpenOptions::new().create(true).truncate(false).write(true).open(home.join("kip-app.lock")).expect("kip-app.lock");
    if lock.try_lock().is_err() {
        if let Some(p) = fs::read_to_string(home.join("kip-app.port")).ok().and_then(|p| p.trim().parse::<u16>().ok()) {
            let _ = TcpStream::connect(("127.0.0.1", p));
        }
        return;
    }
    let listener = TcpListener::bind("127.0.0.1:0").expect("local socket");
    let _ = fs::write(home.join("kip-app.port"), listener.local_addr().unwrap().port().to_string());

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let p = proxy.clone();
    thread::spawn(move || listener.incoming().for_each(|_| drop(p.send_event(UserEvent::Show))));
    let p = proxy.clone();
    thread::spawn(move || {
        let (child, ok) = connect(port);
        let _ = p.send_event(UserEvent::Ready(child, ok));
    });
    let p = proxy.clone();
    thread::spawn(move || {
        loop {
            if let Some(st) = get(port, "/api/status").as_deref().and_then(parse_status) {
                if p.send_event(UserEvent::Status(st)).is_err() {
                    return; // the event loop is gone
                }
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
    let notify_proxy = proxy.clone();

    let icon = mood_icon("waiting");
    let window = WindowBuilder::new()
        .with_title("Kip")
        .with_inner_size(tao::dpi::LogicalSize::new(1200.0, 800.0))
        .with_window_icon(tao::window::Icon::from_rgba(icon.clone(), 32, 32).ok())
        .build(&event_loop)
        .expect("window");
    let builder = WebViewBuilder::new().with_html(page("Starting Kip's dashboard…"));
    #[cfg(not(target_os = "linux"))]
    let webview = builder.build(&window).expect("webview");
    #[cfg(target_os = "linux")]
    let webview = {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        builder.build_gtk(window.default_vbox().unwrap()).expect("webview")
    };

    let menu = Menu::new();
    let (open, quit) = (MenuItem::new("Open dashboard", true, None), MenuItem::new("Quit", true, None));
    menu.append_items(&[&open, &quit]).expect("tray menu");
    let (open_id, p) = (open.id().clone(), Mutex::new(proxy.clone()));
    MenuEvent::set_event_handler(Some(move |e: MenuEvent| {
        let _ = p.lock().unwrap().send_event(if e.id == open_id { UserEvent::Show } else { UserEvent::Quit });
    }));
    let p = Mutex::new(proxy);
    TrayIconEvent::set_event_handler(Some(move |e| {
        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = e {
            let _ = p.lock().unwrap().send_event(UserEvent::Show);
        }
    }));

    let (mut tray, mut child) = (None::<tray_icon::TrayIcon>, None::<Child>);
    let (mut shown, mut seen, mut pending) = (String::from("waiting"), None, None::<String>);
    let open_session = |webview: &wry::WebView, id: &str| {
        let id: String = id.chars().filter(|c| c.is_ascii_alphanumeric() || "-_.".contains(*c)).collect();
        let _ = webview.evaluate_script(&format!("location.hash = 'session={id}'; location.reload()")); // read on load
    };
    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::NewEvents(StartCause::Init) => {
                // macOS wants the tray made once the loop runs
                tray = TrayIconBuilder::new()
                    .with_menu(Box::new(menu.clone()))
                    .with_tooltip("Kip")
                    .with_icon(tray_icon::Icon::from_rgba(icon.clone(), 32, 32).expect("tray icon"))
                    .build()
                    .ok();
            }
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => window.set_visible(false),
            Event::UserEvent(UserEvent::Show) => {
                window.set_visible(true);
                window.set_minimized(false);
                window.set_focus();
                if let Some(id) = pending.take() {
                    open_session(&webview, &id); // the last notification's session, where a click on it can't reach us
                }
            }
            Event::UserEvent(UserEvent::Open(id)) => {
                pending = None;
                window.set_visible(true);
                window.set_minimized(false);
                window.set_focus();
                open_session(&webview, &id);
            }
            Event::UserEvent(UserEvent::Status(st)) => {
                if st.mood != shown {
                    if let Some(t) = &tray {
                        let _ = t.set_icon(tray_icon::Icon::from_rgba(mood_icon(&st.mood), 32, 32).ok());
                        let _ = t.set_tooltip(Some(format!("Kip: {}", st.mood)));
                    }
                    shown = st.mood;
                }
                let new = news(&mut seen, &st.waiting);
                if let Some((kind, id)) = new.first() {
                    let (_, title, text) = NOTICE.iter().find(|(k, ..)| k == &kind.as_str()).expect("a NOTICE kind");
                    let more = if new.len() > 1 { format!(" (+{} more)", new.len() - 1) } else { String::new() };
                    let short: String = id.chars().take(8).collect();
                    pending = cfg!(target_os = "macos").then(|| id.clone());
                    notify(notify_proxy.clone(), title, &format!("{short} {text}{more}"), id.clone());
                }
            }
            Event::UserEvent(UserEvent::Ready(c, ok)) => {
                child = c;
                let _ = match ok {
                    Ok(()) => webview.load_url(&url),
                    Err(e) => webview.load_html(&page(&format!("Kip's dashboard didn't start: {e}"))),
                };
            }
            Event::UserEvent(UserEvent::Quit) => {
                if let Some(c) = child.as_mut() {
                    let _ = c.kill(); // only ever the server this app started; an attached one is left running
                    let _ = c.wait();
                }
                tray.take();
                *flow = ControlFlow::Exit;
            }
            _ => {}
        }
    });
}
