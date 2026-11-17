//! Windows: kip.ico (the waiting mascot, 32 and 64 px) becomes kip-app.exe's icon resource. A pinned taskbar item or
//! a shortcut shows the exe's own icon, not the one the window sets at run time.
use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=src/kip.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let ico = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("src").join("kip.ico");
    let rc = out.join("kip.rc");
    fs::write(&rc, format!("1 ICON \"{}\"\n", ico.display().to_string().replace('\\', "/"))).unwrap();
    let (res, mut cmd) = if env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        let res = out.join("kip.res");
        let mut c = Command::new("rc");
        c.arg("/nologo").arg("/fo").arg(&res).arg(&rc);
        (res, c)
    } else {
        let res = out.join("kip-res.o");
        let mut c = Command::new("windres");
        c.arg(&rc).args(["-O", "coff", "-o"]).arg(&res);
        (res, c)
    };
    match cmd.status() {
        Ok(s) if s.success() => println!("cargo:rustc-link-arg-bins={}", res.display()),
        _ => println!("cargo:warning=kip-app.exe has no icon: couldn't run {:?}", cmd.get_program()),
    }
}
