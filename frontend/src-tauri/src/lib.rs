use std::io::BufRead;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

static BACKEND: Mutex<Option<Child>> = Mutex::new(None);
static BACKEND_PORT: Mutex<u16> = Mutex::new(8844);

fn find_backend_main() -> Option<PathBuf> {
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.parent()?.join("backend").join("main.py");
        if candidate.exists() { return Some(candidate); }
    }
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent()?.parent()?.join("Resources").join("backend").join("main.py");
        if candidate.exists() { return Some(candidate); }
    }
    None
}

#[tauri::command]
fn api_call(method: String, path: String, body: Option<String>) -> Result<String, String> {
    let port = *BACKEND_PORT.lock().unwrap();
    let url = format!("http://127.0.0.1:{}{}", port, path);
    let resp = match method.as_str() {
        "GET" => ureq::get(&url).call(),
        "POST" => {
            let r = ureq::post(&url);
            if let Some(b) = &body {
                r.set("Content-Type", "application/json").send_string(b)
            } else { r.call() }
        }
        "PUT" => {
            let r = ureq::put(&url);
            if let Some(b) = &body {
                r.set("Content-Type", "application/json").send_string(b)
            } else { r.call() }
        }
        "DELETE" => ureq::delete(&url).call(),
        _ => return Err(format!("Unsupported: {}", method)),
    }.map_err(|e| format!("API error: {}", e))?;
    resp.into_string().map_err(|e| format!("Read error: {}", e))
}

fn start_python_backend() {
    let python = std::env::var("CODEX_BACKEND_PYTHON").unwrap_or_else(|_| "python3".into());
    let main_py = match find_backend_main() {
        Some(p) => p,
        None => return,
    };

    let mut child = Command::new(&python)
        .arg(&main_py)
        .arg("--port")
        .arg("0")  // Let OS pick
        .stdout(Stdio::piped())
        .spawn()
        .ok();

    if let Some(ref mut c) = child {
        // Read port from stdout: Python prints "PORT=<number>"
        if let Some(stdout) = c.stdout.take() {
            let reader = std::io::BufReader::new(stdout);
            for line in reader.lines().flatten() {
                if let Some(port_str) = line.strip_prefix("PORT=") {
                    if let Ok(port) = port_str.parse::<u16>() {
                        *BACKEND_PORT.lock().unwrap() = port;
                        break;
                    }
                }
            }
        }
    }

    *BACKEND.lock().unwrap() = child;
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    start_python_backend();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_call])
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
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
