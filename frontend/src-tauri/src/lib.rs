use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

static BACKEND: Mutex<Option<Child>> = Mutex::new(None);

fn find_backend_main() -> Option<PathBuf> {
    // Try relative to current dir (dev mode)
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.parent()?.join("backend").join("main.py");
        if candidate.exists() {
            return Some(candidate);
        }
    }

    // Try relative to executable (bundled app)
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent()?.parent()?.join("Resources").join("backend").join("main.py");
        if candidate.exists() {
            return Some(candidate);
        }
    }

    None
}

fn start_python_backend() {
    let python = std::env::var("CODEX_BACKEND_PYTHON")
        .unwrap_or_else(|_| "python3".into());
    let main_py = match find_backend_main() {
        Some(p) => p,
        None => return,
    };

    let child = Command::new(&python)
        .arg(&main_py)
        .spawn()
        .ok();

    *BACKEND.lock().unwrap() = child;
}

pub fn main() {
    start_python_backend();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_http::init())
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
