use std::io::BufRead;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

static BACKEND: Mutex<Option<Child>> = Mutex::new(None);
static BACKEND_PORT: Mutex<u16> = Mutex::new(8844);

fn pid_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".config").join("codex-cockpit").join("backend.pid")
}

fn kill_previous_backend() {
    let path = pid_path();
    if let Ok(content) = std::fs::read_to_string(&path) {
        if let Ok(pid) = content.trim().parse::<i32>() {
            // kill -0 checks if process exists without sending a signal
            let alive = std::process::Command::new("kill")
                .arg("-0").arg(pid.to_string())
                .output().map(|o| o.status.success()).unwrap_or(false);
            if alive {
                let _ = std::process::Command::new("kill").arg(pid.to_string()).output();
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
        }
        let _ = std::fs::remove_file(&path);
    }
}

fn save_pid(pid: u32) {
    let _ = std::fs::write(pid_path(), pid.to_string());
}

fn log(msg: &str) {
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true)
        .open("/tmp/codex-cockpit.log")
    {
        let _ = writeln!(f, "{}", msg);
    }
    eprintln!("{}", msg);
}

fn find_backend_main() -> Option<PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent()?.parent()?.join("Resources").join("backend").join("main.py");
        if candidate.exists() { return Some(candidate); }
    }
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.join("..").join("backend").join("main.py");
        if let Ok(canonical) = std::fs::canonicalize(&candidate) {
            if canonical.exists() { return Some(canonical); }
        }
    }
    None
}

fn find_python() -> Option<String> {
    if let Ok(p) = std::env::var("CODEX_BACKEND_PYTHON") { return Some(p); }
    for path in &["python3", "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"] {
        if std::process::Command::new(path).arg("--version").output().is_ok() {
            return Some(path.to_string());
        }
    }
    None
}

fn start_and_wait() {
    kill_previous_backend();

    let python = match find_python() {
        Some(p) => p,
        None => { log("[cockpit] FATAL: python3 not found"); return; }
    };
    let main_py = match find_backend_main() {
        Some(p) => p,
        None => { log("[cockpit] FATAL: Cannot find backend/main.py"); return; }
    };

    log(&format!("[cockpit] Spawning: {} {}", python, main_py.display()));

    let mut child = match Command::new(&python).arg(&main_py)
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()
    {
        Ok(c) => c,
        Err(e) => { log(&format!("[cockpit] FATAL: spawn failed: {}", e)); return; }
    };

    save_pid(child.id());

    if let Some(stdout) = child.stdout.take() {
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines().flatten() {
            if let Some(port_str) = line.strip_prefix("PORT=") {
                if let Ok(port) = port_str.trim().parse::<u16>() {
                    log(&format!("[cockpit] Backend ready on port {}", port));
                    *BACKEND_PORT.lock().unwrap() = port;
                    break;
                }
            }
        }
    }

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

fn stop_backend() {
    if let Ok(mut proc) = BACKEND.lock() {
        if let Some(mut child) = proc.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    let _ = std::fs::remove_file(pid_path());
}

#[tauri::command]
async fn api_call(method: String, path: String, body: Option<String>) -> Result<String, String> {
    log(&format!("[cockpit] api_call {} {}", method, path));
    let url = {
        let port = *BACKEND_PORT.lock().unwrap();
        format!("http://127.0.0.1:{}{}", port, path)
    };
    let m = method.clone();
    let p = path.clone();
    let resp = tauri::async_runtime::spawn_blocking(move || {
        let result = match m.as_str() {
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
            _ => return Err(format!("Unsupported: {}", m)),
        };
        match &result {
            Ok(r) => log(&format!("[cockpit] api_call OK {} {} -> {}", m, p, r.status())),
            Err(e) => log(&format!("[cockpit] api_call ERR {} {} -> {}", m, p, e)),
        }
        // On error, try to extract detail from response body
        let resp = result.map_err(|e| {
            let msg = e.to_string();
            // ureq includes response body in error for 4xx/5xx
            if msg.contains("UNSUPPORTED_AUTH") {
                msg.replace("UNSUPPORTED_AUTH: ", "")
            } else {
                format!("请求失败: {}", msg)
            }
        })?;
        resp.into_string().map_err(|e| format!("Read error: {}", e))
    }).await.map_err(|e| format!("Spawn error: {}", e))??;
    Ok(resp)
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
                stop_backend();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
