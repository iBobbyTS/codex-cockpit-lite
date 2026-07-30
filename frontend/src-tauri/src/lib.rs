use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

static BACKEND: Mutex<Option<Child>> = Mutex::new(None);

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
    let url = format!("http://127.0.0.1:8844{}", path);
    let resp = match method.as_str() {
        "GET" => ureq::get(&url).call(),
        "POST" => {
            let r = ureq::post(&url);
            if let Some(b) = &body {
                r.set("Content-Type", "application/json").send_string(b)
            } else {
                r.call()
            }
        }
        "PUT" => {
            let r = ureq::put(&url);
            if let Some(b) = &body {
                r.set("Content-Type", "application/json").send_string(b)
            } else {
                r.call()
            }
        }
        "DELETE" => ureq::delete(&url).call(),
        _ => return Err(format!("Unsupported method: {}", method)),
    }
    .map_err(|e| format!("API error: {}", e))?;

    resp.into_string().map_err(|e| format!("Read error: {}", e))
}

fn start_python_backend() {
    let python = std::env::var("CODEX_BACKEND_PYTHON").unwrap_or_else(|_| "python3".into());
    let main_py = match find_backend_main() {
        Some(p) => p,
        None => return,
    };
    let child = Command::new(&python).arg(&main_py).spawn().ok();
    *BACKEND.lock().unwrap() = child;
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    start_python_backend();
    let _ = std::process::Command::new("open").arg("http://127.0.0.1:8844").spawn();

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
