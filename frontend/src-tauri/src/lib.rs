use std::io::BufRead;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

static BACKEND: Mutex<Option<Child>> = Mutex::new(None);
static BACKEND_PORT: Mutex<u16> = Mutex::new(8844);

fn log(msg: &str) {
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true)
        .open("/tmp/codex-cockpit.log")
    {
        let _ = writeln!(f, "{}", msg);
    }
    eprintln!("{}", msg);
}

fn find_backend_main() -> Option<PathBuf> {
    // Try relative to exe first (works both in dev and bundled app)
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent()?.parent()?.join("Resources").join("backend").join("main.py");
        if candidate.exists() { return Some(candidate); }
    }
    // Fallback: relative to cwd (dev mode)
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.join("..").join("backend").join("main.py");
        let canonical = std::fs::canonicalize(&candidate).ok()?;
        if canonical.exists() { return Some(canonical); }
    }
    None
}

fn find_python() -> Option<String> {
    // Try CODEX_BACKEND_PYTHON env var first
    if let Ok(p) = std::env::var("CODEX_BACKEND_PYTHON") {
        return Some(p);
    }
    // Try common python paths
    for path in &["python3", "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"] {
        if std::process::Command::new(path).arg("--version").output().is_ok() {
            return Some(path.to_string());
        }
    }
    None
}

fn start_and_wait() {
    // Kill any stale Python backends from previous runs
    let _ = std::process::Command::new("pkill").arg("-f").arg("main.py").output();

    let python = match find_python() {
        Some(p) => p,
        None => {
            log("[cockpit] FATAL: python3 not found in PATH. Set CODEX_BACKEND_PYTHON env var.");
            return;
        }
    };

    let main_py = match find_backend_main() {
        Some(p) => p,
        None => {
            log(&format!("[cockpit] FATAL: Cannot find backend/main.py"));
            if let Ok(exe) = std::env::current_exe() {
                let candidate = exe.parent().unwrap_or(std::path::Path::new("."))
                    .parent().unwrap_or(std::path::Path::new("."))
                    .join("Resources").join("backend").join("main.py");
                log(&format!("[cockpit] Expected at: {}", candidate.display()));
                log(&format!("[cockpit] Exists: {}", candidate.exists()));
            }
            return;
        }
    };

    log(&format!("[cockpit] Spawning: {} {}", python, main_py.display()));

    let mut child = match Command::new(&python)
        .arg(&main_py)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            log(&format!("[cockpit] FATAL: spawn failed: {}", e));
            return;
        }
    };

    // Read PORT= from stdout, discard remaining output in background
    if let Some(stdout) = child.stdout.take() {
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines().flatten() {
            log(&format!("[cockpit] [stdout] {}", line));
            if let Some(port_str) = line.strip_prefix("PORT=") {
                if let Ok(port) = port_str.trim().parse::<u16>() {
                    log(&format!("[cockpit] Backend ready on port {}", port));
                    *BACKEND_PORT.lock().unwrap() = port;
                    break;
                }
            }
        }
    }

    // Drain stderr in background (never blocks start_and_wait)
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            let reader = std::io::BufReader::new(stderr);
            for line in reader.lines().flatten() {
                log(&format!("[cockpit] [stderr] {}", line));
            }
        });
    }

    *BACKEND.lock().unwrap() = Some(child);
}

#[tauri::command]
fn api_call(method: String, path: String, body: Option<String>) -> Result<String, String> {
    let port = *BACKEND_PORT.lock().unwrap();
    let url = format!("http://127.0.0.1:{}{}", port, path);
    let resp = match method.as_str() {
        "GET" => ureq::get(&url).call(),
        "POST" => {
            let r = ureq::post(&url);
            if let Some(b) = &body { r.set("Content-Type", "application/json").send_string(b) }
            else { r.call() }
        }
        "PUT" => {
            let r = ureq::put(&url);
            if let Some(b) = &body { r.set("Content-Type", "application/json").send_string(b) }
            else { r.call() }
        }
        "DELETE" => ureq::delete(&url).call(),
        _ => return Err(format!("Unsupported: {}", method)),
    }.map_err(|e| format!("API error: {}", e))?;
    resp.into_string().map_err(|e| format!("Read error: {}", e))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    log("[cockpit] App starting");
    start_and_wait();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_call])
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                log("[cockpit] Window destroyed, killing backend");
                if let Ok(mut proc) = BACKEND.lock() {
                    if let Some(mut child) = proc.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
