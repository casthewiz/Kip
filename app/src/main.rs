//! kip-app: a window and tray icon around Kip's dashboard. Attaches to the `receipt.py serve` already listening on
//! the dashboard port, or starts one and stops it again on Quit. The page is the server's own http://127.0.0.1
//! URL, not a custom protocol: the server refuses any other Host.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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

const MASCOT: &str = include_str!("../../assets/mascot/kip-waiting.svg");
const OWNERS: [&str; 2] = ["CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"]; // a server with these is that session's to stop

enum UserEvent {
    Show,
    Quit,
    Ready(Option<Child>, Result<(), String>),
}

fn kip_home() -> PathBuf {
    env::var_os("KIP_HOME").map(PathBuf::from).unwrap_or_else(|| env::home_dir().unwrap_or_default().join(".kip"))
}

/// The dashboard answers /api/sessions with a 200 (a bare open port could be anything).
fn answers(port: u16) -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_millis(500)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let mut head = [0u8; 12];
    write!(s, "GET /api/sessions HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n").is_ok()
        && s.read_exact(&mut head).is_ok()
        && head.starts_with(b"HTTP/1.")
        && &head[9..] == b"200"
}

fn quiet(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    std::os::windows::process::CommandExt::creation_flags(cmd, 0x0800_0000); // CREATE_NO_WINDOW: no console flash
    cmd
}

/// KIP_PYTHON, else the first of python3/python that runs (Windows' python3 is often a Store stub that doesn't).
fn python() -> Option<String> {
    if let Ok(p) = env::var("KIP_PYTHON") {
        return Some(p);
    }
    ["python3", "python"].into_iter().map(String::from).find(|p| {
        quiet(Command::new(p).args(["-c", "import sys"]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()))
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
    let mut child = match quiet(&mut cmd).spawn() {
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
fn mascot_rgba() -> Vec<u8> {
    let mut px = vec![0u8; 32 * 32 * 4];
    for rect in MASCOT.split("<rect").skip(1) {
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
    let px = mascot_rgba();
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

    let icon = mascot_rgba();
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

    let (mut tray, mut child) = (None, None::<Child>);
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
