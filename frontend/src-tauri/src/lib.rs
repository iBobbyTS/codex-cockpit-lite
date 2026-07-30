use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

struct BackendProcess(Mutex<Option<Child>>);

fn get_config_dir() -> PathBuf {
    std::env::var("CODEX_COCKPIT_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs_next::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".config")
                .join("codex-cockpit")
        })
}

fn find_backend_main() -> Option<PathBuf> {
    let cwd = std::env::current_dir().ok()?;
    let candidate = cwd.parent()?.join("backend").join("main.py");
    if candidate.exists() {
        return Some(candidate);
    }
    None
}

fn start_python_backend() -> Option<Child> {
    let python = std::env::var("CODEX_BACKEND_PYTHON")
        .unwrap_or_else(|_| "python3".into());
    let main_py = find_backend_main()?;
    let config_dir = get_config_dir();

    Command::new(&python)
        .arg(&main_py)
        .arg("--config-dir")
        .arg(config_dir.to_string_lossy().to_string())
        .spawn()
        .ok()
}

pub fn main() {
    let backend = start_python_backend();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(backend)))
        .setup(|app| {
            let window = app.get_webview_window("main").unwrap();
            let _ = window.eval("window.location.replace('http://localhost:8844')");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let child_opt = {
                    let state: tauri::State<BackendProcess> = window.state();
                    let mut proc = state.0.lock().unwrap();
                    proc.take()
                };
                if let Some(mut child) = child_opt {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
