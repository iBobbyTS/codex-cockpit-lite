use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Emitter;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

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
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![api_call])
        .setup(|app| {
            let main_py = find_backend_main().expect("Cannot find backend main.py");
            let shell = app.shell();
            let handle = app.handle().clone();

            let (mut rx, _child) = shell
                .command("python3")
                .args([main_py.to_string_lossy().to_string()])
                .spawn()
                .expect("Failed to spawn Python backend");

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(line) = event {
                        let text = String::from_utf8_lossy(&line);
                        if let Some(port_str) = text.strip_prefix("PORT=") {
                            if let Ok(port) = port_str.trim().parse::<u16>() {
                                *BACKEND_PORT.lock().unwrap() = port;
                                let _ = handle.emit("backend-ready", port);
                                break;
                            }
                        }
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
